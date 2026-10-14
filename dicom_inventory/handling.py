"""Human review metadata, independent of Qt and immutable DICOM observations."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from .snapshot_codec import dumps, loads, decode
from .core.models import QualityIssue

EDITABLE_STATES = ("待确认", "已确认", "可忽略", "已处理待复检")
ALL_STATES = (*EDITABLE_STATES, "已关闭")
HANDLING_TABLES = {"issue_handling", "issue_observations", "handling_history"}


def now():
    return datetime.now(timezone.utc).isoformat()


def track_key(source, rule_version, issue):
    # Conservative exact-evidence reuse, not a recheck/comparison algorithm.
    value = dumps((str(Path(source).resolve()).casefold(), rule_version, dumps(issue)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HandlingRecord:
    issue_id: str
    track_key: str
    state: str
    note: str
    revision: int
    created_at: str
    updated_at: str
    first_run: str
    latest_run: str


@dataclass(frozen=True)
class HandlingEvent:
    revision: int
    run_id: str
    old_state: str
    new_state: str
    old_note: str
    new_note: str
    changed_at: str


class HandlingMixin:
    def _migrate_1_to_2(self, connection):
        connection.execute("""CREATE TABLE issue_handling (
            track_key TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>=0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE issue_observations (
            run_id TEXT NOT NULL, issue_id TEXT NOT NULL,
            track_key TEXT NOT NULL REFERENCES issue_handling(track_key),
            PRIMARY KEY(run_id,issue_id), FOREIGN KEY(run_id,issue_id) REFERENCES issues(run_id,issue_id))""")
        connection.execute("CREATE INDEX observation_track ON issue_observations(track_key)")
        connection.execute("""CREATE TABLE handling_history (
            track_key TEXT NOT NULL REFERENCES issue_handling(track_key), revision INTEGER NOT NULL,
            run_id TEXT NOT NULL REFERENCES scan_runs(run_id), old_state TEXT NOT NULL, new_state TEXT NOT NULL,
            old_note TEXT NOT NULL, new_note TEXT NOT NULL, changed_at TEXT NOT NULL,
            PRIMARY KEY(track_key,revision))""")
        for (run_id,) in connection.execute("SELECT run_id FROM scan_runs ORDER BY started_at,run_id").fetchall():
            self._register_handling(connection, run_id)
        connection.execute("PRAGMA user_version=2")

    def _register_handling(self, connection, run_id):
        from .storage import StorageError
        source, version = connection.execute(
            "SELECT source_root,rule_version FROM scan_runs WHERE run_id=?", (run_id,)).fetchone()
        for issue_id, payload in connection.execute(
                "SELECT issue_id,payload FROM issues WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall():
            issue = decode(loads(payload), QualityIssue)
            if issue.issue_id != issue_id:
                raise StorageError("问题键与观察记录不一致。")
            key = track_key(source, version, issue)
            stamp = now()
            if not connection.execute("SELECT 1 FROM issue_handling WHERE track_key=?", (key,)).fetchone():
                connection.execute("INSERT INTO issue_handling VALUES (?,?,?,?,?,?)",
                                   (key, "待确认", "", 0, stamp, stamp))
                connection.execute("INSERT INTO handling_history VALUES (?,?,?,?,?,?,?,?)",
                                   (key, 0, run_id, "", "待确认", "", "", stamp))
            connection.execute("INSERT INTO issue_observations VALUES (?,?,?)", (run_id, issue_id, key))

    def _handling_records(self, connection, run_id):
        from .storage import StorageError
        header = connection.execute("SELECT source_root,rule_version FROM scan_runs WHERE run_id=?", (run_id,)).fetchone()
        if header is None:
            raise StorageError("所选扫描批次不存在。")
        records = {}
        issues = connection.execute("SELECT issue_id,payload FROM issues WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
        for issue_id, payload in issues:
            issue = decode(loads(payload), QualityIssue)
            key = track_key(*header, issue)
            row = connection.execute("""SELECT h.track_key,h.state,h.note,h.revision,h.created_at,h.updated_at
                FROM issue_handling h JOIN issue_observations o ON o.track_key=h.track_key
                WHERE o.run_id=? AND o.issue_id=?""", (run_id, issue_id)).fetchone()
            if (row is None or issue.issue_id != issue_id or row[0] != key or row[1] not in ALL_STATES
                or type(row[2]) is not str or len(row[2]) > 2000 or type(row[3]) is not int or row[3] < 0
                or (row[1] == "可忽略" and not row[2].strip())):
                raise StorageError("处理记录不完整或与检查证据不一致；未使用该状态。")
            runs = connection.execute("""SELECT s.run_id FROM scan_runs s JOIN issue_observations o ON o.run_id=s.run_id
                WHERE o.track_key=? ORDER BY s.started_at,s.run_id""", (key,)).fetchall()
            records[issue_id] = HandlingRecord(issue_id, *row, runs[0][0], runs[-1][0])
        return records

    def get_handling(self, run_id):
        with self._connection() as connection:
            self._check(connection)
            connection.execute("BEGIN")
            return self._handling_records(connection, run_id)

    def get_history(self, run_id, issue_id):
        from .storage import StorageError
        with self._connection() as connection:
            self._check(connection)
            connection.execute("BEGIN")
            record = self._handling_records(connection, run_id).get(issue_id)
            if record is None:
                raise StorageError("问题不属于所选批次。")
            return tuple(HandlingEvent(*row) for row in connection.execute("""SELECT revision,run_id,old_state,new_state,
                old_note,new_note,changed_at FROM handling_history WHERE track_key=? ORDER BY revision""", (record.track_key,)))

    def update_handling(self, run_id, expected, state, note=None):
        """Atomic batch; expected maps issue IDs to (track_key, revision).

        note=None preserves each note. Empty string explicitly clears it.
        No operation in this module marks a problem resolved or closes it.
        """
        from .storage import StorageError
        if state not in EDITABLE_STATES:
            raise StorageError("只能修改四种人工状态；已关闭需要后续复检依据。")
        if not isinstance(expected, dict) or not expected:
            raise StorageError("请选择需要更新的问题。")
        if note is not None and (type(note) is not str or len(note) > 2000):
            raise StorageError("备注需为文字，最多2000字；不要填写患者身份信息。")
        with self._connection(write=True, create=False) as connection:
            self._check(connection)
            connection.execute("BEGIN IMMEDIATE")
            records = self._handling_records(connection, run_id)
            changes = []
            for issue_id, token in expected.items():
                record = records.get(issue_id)
                if record is None or token != (record.track_key, record.revision):
                    raise StorageError("问题或修订已变化，未保存任何修改；请重新打开处理清单后再核对。")
                if record.state == "已关闭":
                    raise StorageError("已关闭记录不能通过人工入口改写。")
                new_note = record.note if note is None else note
                if state == "可忽略" and not new_note.strip():
                    raise StorageError("标记可忽略必须填写理由。")
                if (state, new_note) != (record.state, record.note):
                    changes.append((record, new_note))
            stamp = now()
            for record, new_note in changes:
                revision = record.revision + 1
                connection.execute("UPDATE issue_handling SET state=?,note=?,revision=?,updated_at=? WHERE track_key=?",
                                   (state, new_note, revision, stamp, record.track_key))
                connection.execute("INSERT INTO handling_history VALUES (?,?,?,?,?,?,?,?)",
                    (record.track_key, revision, run_id, record.state, state, record.note, new_note, stamp))
            updated = self._handling_records(connection, run_id)
            connection.commit()
            return updated

