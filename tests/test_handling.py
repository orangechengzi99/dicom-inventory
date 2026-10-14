"""Synthetic-only human review, legacy migration and export tests."""
from dataclasses import replace
import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
import pytest
from dicom_inventory.handling import EDITABLE_STATES
from dicom_inventory.storage import SnapshotStore, StorageError, SCHEMA_VERSION
from dicom_inventory.snapshot_codec import dumps
from dicom_inventory.handling_export import export_handling_csv, HANDLING_COLUMNS
from dicom_inventory.core.exporter import ISSUE_CSV_COLUMNS
from test_storage import saved_sample as snapshot_sample
from dicom_inventory.core import inventory_files


@pytest.fixture
def saved_sample(snapshot_sample):
    root, paths, _, store = snapshot_sample
    another = root / "second-invalid.dcm"
    another.write_bytes(b"synthetic second unreadable file")
    paths = [*paths, another]
    return root, paths, inventory_files(paths, source_root=root), store


def saved(sample):
    _, _, result, store = sample
    store.save(result)
    records = store.get_handling(result.scan_run.run_id)
    return result, store, records


def tokens(records):
    return {key: (item.track_key, item.revision) for key, item in records.items()}


@pytest.mark.parametrize("state", EDITABLE_STATES)
def test_states_persist_without_modifying_observations(saved_sample, state):
    result, store, initial = saved(saved_sample)
    source_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in saved_sample[1]]
    updated = store.update_handling(result.scan_run.run_id, tokens(initial), state, "人工核对说明")
    assert all(item.state == state and item.note == "人工核对说明" and item.revision == 1 for item in updated.values())
    assert SnapshotStore(store.directory).get_handling(result.scan_run.run_id) == updated
    assert store.load(result.scan_run.run_id) == result
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in saved_sample[1]] == source_hashes
    for key in initial:
        history = store.get_history(result.scan_run.run_id, key)
        assert [(item.old_state, item.new_state) for item in history] == [("", "待确认"), ("待确认", state)]
        assert history[-1].old_note == "" and history[-1].new_note == "人工核对说明"


def test_noop_does_not_create_fake_audit(saved_sample):
    result, store, initial = saved(saved_sample)
    assert store.update_handling(result.scan_run.run_id, tokens(initial), "待确认") == initial
    assert len(store.get_history(result.scan_run.run_id, next(iter(initial)))) == 1


@pytest.mark.parametrize("state,note", [("已关闭", "复检尚未运行"), ("可忽略", ""), ("可忽略", "  "),
                                     ("未知", ""), ("已确认", "a" * 2001), ("已确认", 123)])
def test_invalid_update_changes_nothing(saved_sample, state, note):
    result, store, initial = saved(saved_sample)
    with pytest.raises(StorageError):
        store.update_handling(result.scan_run.run_id, tokens(initial), state, note)
    assert store.get_handling(result.scan_run.run_id) == initial


def test_bulk_preserves_individual_notes_and_requires_each_ignore_reason(saved_sample):
    result, store, initial = saved(saved_sample)
    keys = list(initial)
    assert len(keys) >= 2
    one = {keys[0]: tokens(initial)[keys[0]]}
    current = store.update_handling(result.scan_run.run_id, one, "已确认", "只用于第一项")
    with pytest.raises(StorageError, match="理由"):
        store.update_handling(result.scan_run.run_id, tokens(current), "可忽略")
    assert store.get_handling(result.scan_run.run_id) == current
    changed = store.update_handling(result.scan_run.run_id, tokens(current), "已处理待复检")
    assert changed[keys[0]].note == "只用于第一项"
    assert changed[keys[1]].note == ""
    assert all(item.state == "已处理待复检" for item in changed.values())
    assert store.load(result.scan_run.run_id).summary == result.summary


@pytest.mark.parametrize("wrong", ["unknown", "revision", "track"])
def test_bulk_stale_or_unknown_token_rolls_back_every_issue(saved_sample, wrong):
    result, store, initial = saved(saved_sample)
    expected = tokens(initial)
    key = list(expected)[-1]
    if wrong == "unknown":
        expected["not-in-this-run"] = expected.pop(key)
    elif wrong == "revision":
        expected[key] = (expected[key][0], 8)
    else:
        expected[key] = ("different-evidence", expected[key][1])
    with pytest.raises(StorageError, match="修订"):
        store.update_handling(result.scan_run.run_id, expected, "已确认", "不能部分保存")
    assert store.get_handling(result.scan_run.run_id) == initial


def test_two_store_editors_do_not_silently_overwrite(saved_sample):
    result, store, initial = saved(saved_sample)
    second = SnapshotStore(store.directory)
    current = store.update_handling(result.scan_run.run_id, tokens(initial), "已确认", "先保存")
    with pytest.raises(StorageError):
        second.update_handling(result.scan_run.run_id, tokens(initial), "可忽略", "过期修改")
    assert second.get_handling(result.scan_run.run_id) == current


def test_failure_during_audit_rolls_back_state_and_history(saved_sample):
    result, store, initial = saved(saved_sample)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("""CREATE TRIGGER audit_failure BEFORE INSERT ON handling_history
            WHEN NEW.revision=1 BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
    with pytest.raises(StorageError):
        store.update_handling(result.scan_run.run_id, tokens(initial), "已确认", "仍应待确认")
    assert store.get_handling(result.scan_run.run_id) == initial
    assert all(len(store.get_history(result.scan_run.run_id, key)) == 1 for key in initial)


def test_identical_evidence_reuses_status_first_latest_by_scan_time(saved_sample):
    result, store, initial = saved(saved_sample)
    updated = store.update_handling(result.scan_run.run_id, tokens(initial), "可忽略", "有人工依据")
    early = replace(result, scan_run=replace(result.scan_run, run_id="earlier", started_at="2000-01-01T00:00:00+00:00"))
    later = replace(result, scan_run=replace(result.scan_run, run_id="later", started_at="2090-01-01T00:00:00+00:00"))
    store.save(later)
    store.save(early)  # Save order must not overwrite discovery chronology.
    for key, record in store.get_handling(later.scan_run.run_id).items():
        assert record.track_key == updated[key].track_key
        assert record.state == "可忽略" and record.note == "有人工依据"
        assert record.first_run == "earlier" and record.latest_run == "later"
        assert len(store.get_history(later.scan_run.run_id, key)) == 2
    assert len(store.list_scans()) == 3


@pytest.mark.parametrize("kind", ["source", "version", "evidence", "files"])
def test_changed_scope_does_not_inherit_ignore(saved_sample, kind, tmp_path):
    result, store, initial = saved(saved_sample)
    store.update_handling(result.scan_run.run_id, tokens(initial), "可忽略", "仅旧证据适用")
    run = replace(result.scan_run, run_id="changed")
    issue = result.issues[0]
    other = issue
    if kind == "source":
        run = replace(run, source_root=str(tmp_path / "other-source"))
    elif kind == "version":
        run = replace(run, rule_version="future-rule")
    elif kind == "evidence":
        other = replace(issue, evidence=(*issue.evidence, ("new_evidence", "changed")))
    else:
        other = replace(issue, affected_files=(*issue.affected_files, result.records[0].file_path))
    mapping = {item.issue_id: (other if item.issue_id == issue.issue_id else item) for item in result.issues}
    changed = replace(result, scan_run=run, issues=tuple(mapping.values()),
        records=[replace(record, issues=tuple(mapping[item.issue_id] for item in record.issues)) for record in result.records],
        studies=tuple(replace(group, issues=tuple(mapping[item.issue_id] for item in group.issues)) for group in result.studies),
        series=tuple(replace(group, issues=tuple(mapping[item.issue_id] for item in group.issues)) for group in result.series))
    store.save(changed)
    current = store.get_handling(run.run_id)
    assert current[issue.issue_id].state == "待确认" and current[issue.issue_id].note == ""
    assert store.get_handling(result.scan_run.run_id)[issue.issue_id].state == "可忽略"


def legacy_store(sample):
    _, _, result, store = sample
    # Construct the actual v1 schema and payload, without creating v2 first.
    with store._connection(write=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        store._migrate_0_to_1(connection)
        counts = {"files": len(result.records), "issues": len(result.issues), "studies": len(result.studies), "series": len(result.series)}
        run = result.scan_run
        connection.execute("INSERT INTO scan_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            run.run_id, run.started_at, run.finished_at, run.source_root, run.status, run.rule_version,
            dumps(result.summary), dumps(run.notes), dumps(result.duplicates), dumps(counts), 1))
        for table, items, key in (("files", result.records, "file_path"), ("issues", result.issues, "issue_id"),
                                  ("studies", result.studies, "group_key"), ("series", result.series, "group_key")):
            connection.executemany(f"INSERT INTO {table} VALUES (?,?,?,?)", (
                (run.run_id, i, getattr(item, key), dumps(item)) for i, item in enumerate(items)))
        connection.commit()
    return result, store


def snapshot_rows(store):
    with sqlite3.connect(store.db_path) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("scan_runs", "files", "issues", "studies", "series")}


def test_real_v1_migration_retains_all_old_payloads(saved_sample):
    result, store = legacy_store(saved_sample)
    before = snapshot_rows(store)
    store.initialize()
    assert snapshot_rows(store) == before
    assert store.load(result.scan_run.run_id) == result
    assert all(item.state == "待确认" for item in store.get_handling(result.scan_run.run_id).values())
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    store.initialize()
    assert snapshot_rows(store) == before


def test_v1_upgrade_failure_rolls_back_ddl_and_version(saved_sample, monkeypatch):
    result, store = legacy_store(saved_sample)
    before = snapshot_rows(store)
    original = store._register_handling
    monkeypatch.setattr(store, "_register_handling", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("synthetic")))
    with pytest.raises(StorageError):
        store.initialize()
    assert snapshot_rows(store) == before
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='issue_handling'").fetchall() == []
    monkeypatch.setattr(store, "_register_handling", original)
    store.initialize()
    assert store.load(result.scan_run.run_id) == result


def test_fresh_process_restores_states_notes_and_history(saved_sample):
    result, store, initial = saved(saved_sample)
    store.update_handling(result.scan_run.run_id, tokens(initial), "已处理待复检", "中文备注")
    code = ("from dicom_inventory.storage import SnapshotStore; import sys; s=SnapshotStore(sys.argv[1]); "
            "r=s.get_handling(sys.argv[2]); k=next(iter(r)); print(r[k].state,r[k].note,len(s.get_history(sys.argv[2],k)))")
    output = subprocess.run([sys.executable, "-B", "-c", code, str(store.directory), result.scan_run.run_id],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=15,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert output.stdout.strip() == "已处理待复检 中文备注 2"


def test_export_real_states_notes_formula_safety_and_no_filter_loss(saved_sample, tmp_path):
    result, store, initial = saved(saved_sample)
    store.update_handling(result.scan_run.run_id, tokens(initial), "可忽略", "=SUM(1,2)")
    output = tmp_path / "export"
    output.mkdir()
    destination = export_handling_csv(result, output, store.directory)
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == (*ISSUE_CSV_COLUMNS, *HANDLING_COLUMNS)
        rows = list(reader)
    assert len(rows) == len(result.issues)
    assert all(row["HandlingState"] == "可忽略" and row["HumanNote"] == "'=SUM(1,2)" for row in rows)
    assert all(row["ExportScanRun"] == result.scan_run.run_id for row in rows)
    assert export_handling_csv(result, output, store.directory).name == "issues_1.csv"
    for text in ("PatientName", "PatientID", "PRIVATE-ID", "身份字段不得导出", "PixelData"):
        assert text not in destination.read_text(encoding="utf-8-sig")
    with pytest.raises(ValueError):
        export_handling_csv(result, saved_sample[0], store.directory)


def test_unsaved_and_changed_result_export_rejected_without_file(saved_sample, tmp_path):
    result, store, initial = saved(saved_sample)
    output = tmp_path / "export"
    output.mkdir()
    different = replace(result, scan_run=replace(result.scan_run, run_id="unsaved"))
    with pytest.raises(StorageError):
        export_handling_csv(different, output, store.directory)
    with pytest.raises(StorageError):
        export_handling_csv(replace(result, issues=()), output, store.directory)
    assert not list(output.iterdir())


def test_ignored_original_issue_and_audit_are_never_deleted(saved_sample):
    result, store, initial = saved(saved_sample)
    ignored = store.update_handling(result.scan_run.run_id, tokens(initial), "可忽略", "手工依据")
    store.update_handling(result.scan_run.run_id, tokens(ignored), "待确认", "重新人工核对")
    assert store.load(result.scan_run.run_id).issues == result.issues
    for key in initial:
        events = store.get_history(result.scan_run.run_id, key)
        assert len(events) == 3 and events[-1].old_state == "可忽略" and events[-1].old_note == "手工依据"


def test_missing_library_not_created_by_update(tmp_path):
    work = tmp_path / "empty-work"
    work.mkdir()
    store = SnapshotStore(work)
    with pytest.raises(StorageError):
        store.update_handling("missing-run", {"missing-issue":("missing-key", 0)}, "已确认", "未保存")
    assert not store.db_path.exists()


def test_new_observation_registration_failure_rolls_back_all_tables(saved_sample, monkeypatch):
    result, store, initial = saved(saved_sample)
    original = store._register_handling
    def fail(connection, run_id):
        original(connection, run_id)
        raise sqlite3.OperationalError("synthetic late registration failure")
    monkeypatch.setattr(store, "_register_handling", fail)
    later = replace(result, scan_run=replace(result.scan_run, run_id="rollback-new"))
    with pytest.raises(StorageError):
        store.save(later)
    assert store.get_handling(result.scan_run.run_id) == initial
    with sqlite3.connect(store.db_path) as connection:
        for table in ("scan_runs", "files", "issues", "studies", "series", "issue_observations", "handling_history"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id='rollback-new'").fetchone()[0] == 0


def test_batch_failure_after_first_issue_does_not_partially_commit(saved_sample):
    result, store, initial = saved(saved_sample)
    last = list(initial.values())[-1]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(f"""CREATE TRIGGER second_audit_failure BEFORE INSERT ON handling_history
            WHEN NEW.revision=1 AND NEW.track_key='{last.track_key}'
            BEGIN SELECT RAISE(ABORT,'synthetic second failure'); END""")
    with pytest.raises(StorageError):
        store.update_handling(result.scan_run.run_id, tokens(initial), "已确认", "批量必须整体保存")
    assert store.get_handling(result.scan_run.run_id) == initial
    assert all(len(store.get_history(result.scan_run.run_id, key)) == 1 for key in initial)


def test_read_only_closed_record_cannot_be_manually_reopened(saved_sample):
    result, store, initial = saved(saved_sample)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE issue_handling SET state='已关闭'")  # Reserved future-state read compatibility only.
    records = store.get_handling(result.scan_run.run_id)
    assert all(item.state == "已关闭" for item in records.values())
    with pytest.raises(StorageError, match="已关闭"):
        store.update_handling(result.scan_run.run_id, tokens(records), "待确认", "人工不能改写关闭")
    assert store.get_handling(result.scan_run.run_id) == records



@pytest.mark.parametrize("corruption", ["observation", "state", "key"])
def test_corrupt_handling_not_treated_as_confirmed(saved_sample, corruption):
    result, store, initial = saved(saved_sample)
    with sqlite3.connect(store.db_path) as connection:
        if corruption == "observation":
            connection.execute("DELETE FROM issue_observations")
        elif corruption == "state":
            connection.execute("UPDATE issue_handling SET state='unknown'")
        else:
            connection.execute("UPDATE issue_observations SET track_key='invalid'")
    with pytest.raises(StorageError):
        store.get_handling(result.scan_run.run_id)

