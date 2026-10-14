"""Transactional comparison observations and verified system state transitions."""
from dataclasses import dataclass, replace
from .core.comparison import (ComparisonReport, ReviewState, compare_scans, comparison_id,
                              COMPARISON_VERSION, source_key, stamp)
from .snapshot_codec import dumps, loads, decode
from .handling import now

COMPARISON_TABLES = {"comparisons", "comparison_transitions"}


@dataclass(frozen=True)
class SavedComparison:
    comparison_id: str
    baseline_run: str
    current_run: str
    created_at: str
    comparable: bool


class ComparisonMixin:
    def _migrate_2_to_3(self, connection):
        connection.execute("""CREATE TABLE comparisons (
            comparison_id TEXT PRIMARY KEY, baseline_run TEXT NOT NULL REFERENCES scan_runs(run_id),
            current_run TEXT NOT NULL REFERENCES scan_runs(run_id), created_at TEXT NOT NULL,
            payload_version INTEGER NOT NULL, payload TEXT NOT NULL,
            UNIQUE(baseline_run,current_run))""")
        connection.execute("""CREATE TABLE comparison_transitions (
            comparison_id TEXT NOT NULL REFERENCES comparisons(comparison_id), track_key TEXT NOT NULL,
            revision INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY(comparison_id,track_key),
            FOREIGN KEY(track_key,revision) REFERENCES handling_history(track_key,revision))""")
        connection.execute("PRAGMA user_version=3")

    def _read_comparison(self, connection, key):
        from .storage import StorageError
        row = connection.execute("SELECT baseline_run,current_run,created_at,payload_version,payload FROM comparisons WHERE comparison_id=?", (key,)).fetchone()
        if row is None:
            raise StorageError("对比记录不存在。")
        if row[3] != 1:
            raise StorageError("对比记录格式不受支持。")
        report = decode(loads(row[4]), ComparisonReport)
        if (report.comparison_id != key or key != comparison_id(*row[:2]) or
            (report.baseline_run, report.current_run, report.created_at) != row[:3] or
            report.version != COMPARISON_VERSION or report.comparable == bool(report.reasons) or
            any(item.before < 0 or item.after < 0 for item in report.count_changes) or
            len({item.issue_id for item in report.issue_changes}) != len(report.issue_changes)):
            raise StorageError("对比记录或关联批次不完整。")
        return report

    def get_comparison(self, key):
        with self._connection() as connection:
            self._check(connection)
            connection.execute("BEGIN")
            return self._read_comparison(connection, key)

    def list_comparisons(self, current_run=None):
        with self._connection() as connection:
            self._check(connection)
            connection.execute("BEGIN")
            rows = connection.execute("SELECT comparison_id FROM comparisons " +
                ("WHERE current_run=? " if current_run else "") + "ORDER BY created_at DESC,comparison_id DESC",
                (current_run,) if current_run else ()).fetchall()
            reports = [self._read_comparison(connection, row[0]) for row in rows]
            return tuple(SavedComparison(r.comparison_id, r.baseline_run, r.current_run, r.created_at, r.comparable) for r in reports)

    def compare_and_save(self, baseline_run, current_run):
        """Writer lock covers fresh human state, report, transitions and audit.

        Only stored snapshots are compared. A repeated pair returns its immutable
        historical comparison; it cannot replay transitions after a manual edit.
        """
        from .storage import StorageError
        self.initialize()
        with self._connection(write=True, create=False) as connection:
            self._check(connection)
            connection.execute("BEGIN IMMEDIATE")
            key = comparison_id(baseline_run, current_run)
            if connection.execute("SELECT 1 FROM comparisons WHERE comparison_id=?", (key,)).fetchone():
                return self._read_comparison(connection, key)
            baseline = self._load_result(connection, baseline_run)
            current = self._load_result(connection, current_run)
            self._guard(baseline)
            self._guard(current)
            old_records = self._handling_records(connection, baseline_run)
            new_records = self._handling_records(connection, current_run)
            def states(records):
                return {key: ReviewState(r.state, r.updated_at, r.track_key, r.revision) for key, r in records.items()}
            started = stamp(current.scan_run.started_at)
            later = any(source_key(source) == source_key(current.scan_run.source_root) and
                        (stamp(date) is None or started is None or stamp(date) >= started)
                        for run, source, date in connection.execute("SELECT run_id,source_root,started_at FROM scan_runs")
                        if run != current_run)
            report = compare_scans(baseline, current, baseline_states=states(old_records),
                                   current_states=states(new_records), allow_state_changes=not later)
            connection.execute("INSERT INTO comparisons VALUES (?,?,?,?,?,?)",
                (key, baseline_run, current_run, report.created_at, 1, dumps(report)))
            changes = []
            for item in report.issue_changes:
                action = item.suggested_action
                if not action:
                    changes.append(item)
                    continue
                record = old_records[item.issue_id] if action == "关闭" else new_records[item.issue_id]
                updated = stamp(record.updated_at)
                if updated is None or started is None or updated > started:
                    changes.append(replace(item, suggested_action="", reason=item.reason + " 处理状态晚于本次扫描，未自动变更。"))
                    continue
                state = "已关闭" if action == "关闭" else "待确认"
                revision, date = record.revision + 1, now()
                changed = connection.execute("""UPDATE issue_handling SET state=?,revision=?,updated_at=?
                    WHERE track_key=? AND revision=? AND state=?""",
                    (state, revision, date, record.track_key, record.revision, record.state))
                if changed.rowcount != 1:
                    raise StorageError("处理修订已变化，未保存对比或任何状态变更。")
                connection.execute("INSERT INTO handling_history VALUES (?,?,?,?,?,?,?,?)",
                    (record.track_key, revision, current_run, record.state, state, record.note, record.note, date))
                connection.execute("INSERT INTO comparison_transitions VALUES (?,?,?,?,?)",
                    (key, record.track_key, revision, action, item.reason))
                changes.append(replace(item, action=action, handling_after=state))
            report = replace(report, issue_changes=tuple(changes))
            connection.execute("UPDATE comparisons SET payload=? WHERE comparison_id=?", (dumps(report), key))
            connection.commit()
            return report
