"""Synthetic-only comparison, closure, recurrence, migrations and failure gates."""
import csv
from dataclasses import replace
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydicom.uid import generate_uid, EncapsulatedPDFStorage

from dicom_inventory.core import inventory_files, discover_dicom_files
from dicom_inventory.core.cancellation import CancellationToken
from dicom_inventory.core.comparison import compare_scans, ReviewState
from dicom_inventory.core.comparison_export import export_comparison_csv, COMPARISON_COLUMNS
from dicom_inventory.storage import SnapshotStore, StorageError, SCHEMA_VERSION
from dicom_inventory.snapshot_codec import dumps


@pytest.fixture
def case(tmp_path, make_dicom):
    root, work, output = [tmp_path / name for name in ("source", "work", "output")]
    work.mkdir()
    output.mkdir()
    study, series, frame = [generate_uid() for _ in range(3)]
    sops = [generate_uid() for _ in range(6)]
    paths = [root / f"CT_{i:03}.dcm" for i in range(5)]
    settings = [{"FrameOfReferenceUID": frame, "ImageType": ["ORIGINAL", "PRIMARY"],
                 "InstanceNumber": i+1, "ImagePositionPatient": [0, 0, i*1.25]} for i in range(6)]
    settings[0]["PixelSpacing"] = None
    def write(i, **values):
        settings[i].update(values)
        path = paths[i] if i < len(paths) else root / f"CT_{i:03}.dcm"
        return make_dicom(path, study_uid=study, series_uid=series, sop_uid=sops[i], overrides=settings[i])
    for i in range(5):
        write(i)
    store = SnapshotStore(work)
    def scan():
        return inventory_files(discover_dicom_files(root), source_root=root)
    def mark(result, state="已处理待复检", ids=None):
        records = store.get_handling(result.scan_run.run_id)
        selected = {key: (r.track_key, r.revision) for key, r in records.items() if ids is None or key in ids}
        return store.update_handling(result.scan_run.run_id, selected, state, "合成测试处理依据")
    return SimpleNamespace(root=root, work=work, output=output, paths=paths, sops=sops,
                           write=write, scan=scan, store=store, mark=mark)


def before(case, state=None):
    result = case.scan()
    case.store.save(result)
    if state:
        case.mark(result, state)
    return result


def fixed(case):
    case.write(0, PixelSpacing=[.7, .7])
    result = case.scan()
    case.store.save(result)
    return result


def compare(case, baseline, current):
    return case.store.compare_and_save(baseline.scan_run.run_id, current.scan_run.run_id)


def states(records):
    return {key: ReviewState(r.state, r.updated_at, r.track_key, r.revision) for key, r in records.items()}


@pytest.mark.parametrize("state,expected", [("待确认", "待确认"), ("已确认", "已确认"),
                                          ("可忽略", "可忽略"), ("已处理待复检", "已关闭")])
def test_resolved_and_actual_closure_are_distinct(case, state, expected):
    baseline = before(case, state)
    current = fixed(case)
    original = dumps(baseline.records)
    record = next(iter(case.store.get_handling(baseline.scan_run.run_id).values()))
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.code == "MISSING_PixelSpacing")
    assert report.comparable and row.observation == "已解决"
    assert row.status == ("已忽略" if state == "可忽略" else "已解决")
    assert row.action == ("关闭" if state == "已处理待复检" else "")
    restored = case.store.get_handling(baseline.scan_run.run_id)[row.issue_id]
    assert restored.state == row.handling_after == expected and restored.note == record.note
    assert dumps(case.store.load(baseline.scan_run.run_id).records) == original
    assert SnapshotStore(case.work).get_comparison(report.comparison_id) == report
    history = case.store.get_history(baseline.scan_run.run_id, row.issue_id)
    if state == "已处理待复检":
        assert history[-1].new_state == "已关闭" and history[-1].run_id == current.scan_run.run_id


def test_pure_comparison_proposes_but_never_writes(case):
    baseline = before(case, "已处理待复检")
    current = fixed(case)
    old = case.store.get_handling(baseline.scan_run.run_id)
    db_hash = hashlib.sha256(case.store.db_path.read_bytes()).digest()
    result = compare_scans(baseline, current, baseline_states=states(old))
    assert result.issue_changes[0].suggested_action == "关闭" and result.issue_changes[0].action == ""
    assert result.issue_changes[0].handling_after == "已处理待复检"
    assert hashlib.sha256(case.store.db_path.read_bytes()).digest() == db_hash


@pytest.mark.parametrize("state", ["已确认", "可忽略", "已处理待复检"])
def test_persistent_issue_retains_manual_state_and_note(case, state):
    baseline = before(case, state)
    original = case.store.get_handling(baseline.scan_run.run_id)
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = report.issue_changes[0]
    assert row.observation == "仍然存在" and row.action == ""
    assert row.status == ("已忽略" if state == "可忽略" else "仍然存在")
    assert case.store.get_handling(current.scan_run.run_id)[row.issue_id].state == state
    assert case.store.get_handling(current.scan_run.run_id)[row.issue_id].note == original[row.issue_id].note


def test_unchanged_issue_payload_but_changed_metadata_requires_review(case):
    baseline = before(case, "可忽略")
    case.write(0, Rows=256)
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.code == "MISSING_PixelSpacing")
    assert row.status == "需重新确认" and row.action == "重新确认"
    assert case.store.get_handling(current.scan_run.run_id)[row.issue_id].state == "待确认"
    assert case.store.get_handling(current.scan_run.run_id)[row.issue_id].note == "合成测试处理依据"
    assert any(item.observation == "新出现" for item in report.issue_changes)


def test_changed_duplicate_group_evidence_gets_new_pending_track(case):
    case.sops[1] = case.sops[0]
    case.write(1)
    baseline = before(case)
    issue = next(item for item in baseline.issues if item.code == "DUPLICATE_SOP_UID")
    case.mark(baseline, "可忽略", [issue.issue_id])
    old = case.store.get_handling(baseline.scan_run.run_id)[issue.issue_id]
    case.sops[2] = case.sops[0]
    case.write(2)
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.issue_id == issue.issue_id)
    actual = case.store.get_handling(current.scan_run.run_id)[issue.issue_id]
    assert row.status == "需重新确认" and actual.state == "待确认"
    assert actual.track_key != old.track_key
    assert case.store.get_handling(baseline.scan_run.run_id)[issue.issue_id].state == "可忽略"


@pytest.mark.parametrize("kind", ["current_cancel", "baseline_partial", "source", "version", "time", "same", "read_failure"])
def test_incompatible_batches_never_close(case, kind):
    baseline = before(case, "已处理待复检")
    case.write(0, PixelSpacing=[.7, .7])
    if kind == "current_cancel":
        token = CancellationToken()
        def progress(n, total, record):
            if n == 2:
                token.request()
        current = inventory_files(case.paths, progress, source_root=case.root, cancel_check=token.checkpoint)
    else:
        if kind == "read_failure":
            case.paths[-1].write_bytes(b"synthetic read failure")
        current = case.scan()
    if kind == "source":
        current.scan_run = replace(current.scan_run, source_root=str(case.root.parent / "other-source"))
    if kind == "version":
        current.scan_run = replace(current.scan_run, rule_version="future-rules")
    if kind == "time":
        current.scan_run = replace(current.scan_run, started_at="2000-01-01T00:00:00+00:00")
    if kind == "same":
        current = baseline
    else:
        case.store.save(current)
    if kind == "baseline_partial":
        # Keep original full snapshot; a separate partial baseline has its own run.
        partial = replace(baseline, scan_run=replace(baseline.scan_run, run_id="partial-baseline", status="未完成"))
        case.store.save(partial)
        baseline = partial
    report = compare(case, baseline, current)
    assert not report.comparable and report.reasons
    assert all(row.observation == "无法确认" and not row.action for row in report.issue_changes)
    assert all(record.state != "已关闭" for record in case.store.get_handling(baseline.scan_run.run_id).values())


@pytest.mark.parametrize("kind", ["removed", "renamed", "unsupported", "identity"])
def test_disappeared_issue_with_lost_evidence_is_unconfirmed(case, kind):
    baseline = before(case, "已处理待复检")
    if kind == "removed":
        case.paths[0].unlink()  # Explicit synthetic fixture only.
    elif kind == "renamed":
        case.paths[0].rename(case.root / "renamed-synthetic.dcm")
    elif kind == "unsupported":
        case.write(0, Modality="SR", SOPClassUID=EncapsulatedPDFStorage)
    else:
        case.write(0, PixelSpacing=[.7, .7], SOPInstanceUID=generate_uid())
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.issue_id == baseline.issues[0].issue_id)
    assert report.comparable and row.observation == "无法确认" and row.action == ""
    assert row.reason
    if kind == "renamed":
        assert any(item.code == "MISSING_PixelSpacing" and item.observation == "新出现" for item in report.issue_changes)
    assert case.store.get_handling(baseline.scan_run.run_id)[row.issue_id].state == "已处理待复检"


@pytest.mark.parametrize("kind", ["fixed", "removed", "skipped"])
def test_series_resolution_requires_full_membership_and_executed_rule(case, kind):
    case.write(0, PixelSpacing=[.7, .7])
    case.write(4, Rows=256)
    baseline = before(case, "已处理待复检")
    if kind == "removed":
        case.paths[4].unlink()
    else:
        case.write(4, Rows=512, NumberOfFrames=2 if kind == "skipped" else None)
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.code == "SERIES_INCONSISTENT_Rows")
    assert row.observation == ("已解决" if kind == "fixed" else "无法确认")
    assert row.action == ("关闭" if kind == "fixed" else "")


def test_file_and_count_changes_are_metadata_only(case):
    baseline = before(case)
    case.write(0, Rows=256)
    case.paths[4].unlink()
    case.write(5)
    current = case.scan()
    report = compare_scans(baseline, current)
    assert {item.status for item in report.file_changes} == {"所选元数据变化", "新增文件", "减少文件"}
    fields = next(item.fields for item in report.file_changes if item.status == "所选元数据变化")
    assert ("rows", "512", "256") in fields
    assert len(report.count_changes) == 10
    assert "像素" in "".join(report.limits)
    assert "PatientName" not in repr(report) and "PRIVATE-ID" not in repr(report)


def test_pixel_only_change_is_not_claimed_as_detected(case):
    baseline = before(case)
    case.write(1, PixelData=b"\0" * (512*512), BitsAllocated=8, BitsStored=8, HighBit=7, PixelRepresentation=0)
    current = case.scan()
    assert compare_scans(baseline, current).file_changes == ()


@pytest.mark.parametrize("field,value", [("PixelSpacing", [-.7, .7]), ("SliceThickness", "0"),
    ("ImagePositionPatient", ["nan", "0", "0"]), ("Rows", 0), ("SOPInstanceUID", "bad.uid")])
def test_filling_missing_tag_with_unusable_value_cannot_close(case, field, value):
    case.write(0, PixelSpacing=[.7, .7])
    case.write(0, **{field: None})
    baseline = before(case, "已处理待复检")
    issue = next(item for item in baseline.issues if item.code == "MISSING_" + field)
    if field.endswith("UID") or field == "ImagePositionPatient":
        with pytest.warns(UserWarning):
            case.write(0, **{field: value})
    else:
        case.write(0, **{field: value})
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.issue_id == issue.issue_id)
    assert row.observation == "无法确认" and row.action == ""
    assert case.store.get_handling(baseline.scan_run.run_id)[issue.issue_id].state == "已处理待复检"


def test_read_failure_is_resolved_only_after_same_path_is_readable(case):
    case.paths[1].write_bytes(b"synthetic corrupt DICOM")
    baseline = before(case, "已处理待复检")
    case.write(1)
    case.write(0, PixelSpacing=[.7, .7])
    current = case.scan()
    case.store.save(current)
    report = compare(case, baseline, current)
    row = next(item for item in report.issue_changes if item.code == "READ_FAILED")
    assert report.comparable and row.observation == "已解决" and row.action == "关闭"
    assert any(item.status == "读取状态变化" for item in report.file_changes)


def test_processed_note_after_scan_does_not_auto_close(case):
    baseline = before(case)
    current = fixed(case)
    case.mark(baseline)
    report = compare(case, baseline, current)
    assert report.issue_changes[0].observation == "已解决"
    assert not report.issue_changes[0].action and "晚于复检" in report.issue_changes[0].reason
    assert next(iter(case.store.get_handling(baseline.scan_run.run_id).values())).state == "已处理待复检"


def test_later_observation_prevents_older_comparison_changing_current_state(case):
    baseline = before(case, "已处理待复检")
    corrected = fixed(case)
    case.write(0, PixelSpacing=None)
    latest = case.scan()
    case.store.save(latest)
    report = compare(case, baseline, corrected)
    assert report.comparable and report.issue_changes[0].action == ""
    assert "最新" in "".join(report.limits)
    assert next(iter(case.store.get_handling(latest.scan_run.run_id).values())).state == "已处理待复检"


def test_recurrence_reopens_exact_closed_track_with_audit_and_no_replay(case):
    baseline = before(case, "已处理待复检")
    corrected = fixed(case)
    closed = compare(case, baseline, corrected)
    issue_id = closed.issue_changes[0].issue_id
    case.write(0, PixelSpacing=None)
    recurrence = case.scan()
    case.store.save(recurrence)
    reopened = compare(case, corrected, recurrence)
    row = next(item for item in reopened.issue_changes if item.issue_id == issue_id)
    assert row.status == "再次出现" and row.action == "重开" and row.handling_after == "待确认"
    history = case.store.get_history(baseline.scan_run.run_id, issue_id)
    assert history[-1].old_state == "已关闭" and history[-1].new_state == "待确认"
    case.mark(recurrence, "可忽略")
    assert compare(case, corrected, recurrence) == reopened  # Historical report, no replay.
    assert case.store.get_handling(recurrence.scan_run.run_id)[issue_id].state == "可忽略"


def test_second_transition_failure_rolls_back_report_states_and_audit(case):
    case.write(1, SliceThickness=None)
    baseline = before(case, "已处理待复检")
    old = case.store.get_handling(baseline.scan_run.run_id)
    case.write(0, PixelSpacing=[.7, .7])
    case.write(1, SliceThickness="1.25")
    current = case.scan()
    case.store.save(current)
    second = old[sorted(old)[-1]].track_key
    with sqlite3.connect(case.store.db_path) as connection:
        connection.execute(f"""CREATE TRIGGER synthetic_transition_fail BEFORE INSERT ON handling_history
            WHEN NEW.new_state='已关闭' AND NEW.track_key='{second}'
            BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END""")
    with pytest.raises(StorageError):
        compare(case, baseline, current)
    assert case.store.get_handling(baseline.scan_run.run_id) == old
    assert case.store.list_comparisons() == ()
    assert all(len(case.store.get_history(baseline.scan_run.run_id, key)) == 2 for key in old)
    with sqlite3.connect(case.store.db_path) as connection:
        connection.execute("DROP TRIGGER synthetic_transition_fail")
    assert all(item.action == "关闭" for item in compare(case, baseline, current).issue_changes)


def legacy_v2(case):
    baseline = before(case, "可忽略")
    with sqlite3.connect(case.store.db_path) as connection:
        connection.execute("DROP TABLE comparison_transitions")
        connection.execute("DROP TABLE comparisons")
        connection.execute("PRAGMA user_version=2")
    return baseline


def snapshot_tables(store):
    with sqlite3.connect(store.db_path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall() for table in tables}


def test_v2_to_v3_migration_preserves_every_original_row(case):
    baseline = legacy_v2(case)
    original = snapshot_tables(case.store)
    case.store.initialize()
    migrated = snapshot_tables(case.store)
    assert {key: migrated[key] for key in original} == original
    assert case.store.load(baseline.scan_run.run_id) == baseline
    assert next(iter(case.store.get_handling(baseline.scan_run.run_id).values())).state == "可忽略"
    with sqlite3.connect(case.store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3


def test_v2_migration_failure_rolls_back_new_tables_and_version(case, monkeypatch):
    legacy_v2(case)
    original = snapshot_tables(case.store)
    def failure(connection):
        connection.execute("CREATE TABLE comparisons (synthetic TEXT)")
        raise sqlite3.OperationalError("synthetic migration failure")
    monkeypatch.setattr(case.store, "_migrate_2_to_3", failure)
    with pytest.raises(StorageError):
        case.store.initialize()
    assert snapshot_tables(case.store) == original
    with sqlite3.connect(case.store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("partial", [True, False])
def test_comparison_csv_scope_bom_no_overwrite_and_source_guards(case, partial):
    baseline = before(case, "已处理待复检")
    current = fixed(case)
    if partial:
        current = replace(current, scan_run=replace(current.scan_run, status="已取消"))
    report = compare_scans(baseline, current)
    file = export_comparison_csv(report, baseline, current, case.output)
    assert file.name == ("partial_comparison.csv" if partial else "comparison.csv")
    assert file.read_bytes().startswith(b"\xef\xbb\xbf")
    with file.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == COMPARISON_COLUMNS
        rows = list(reader)
    assert {row["RecordType"] for row in rows} == {"比较范围", "数量变化", "文件变化", "问题变化"}
    assert all(row["Comparable"] == ("否" if partial else "是") and row["Limitations"] for row in rows)
    assert "PRIVATE-ID" not in file.read_text(encoding="utf-8-sig")
    assert export_comparison_csv(report, baseline, current, case.output) != file
    with pytest.raises(ValueError):
        export_comparison_csv(report, baseline, current, case.root)


def test_malformed_comparison_payload_is_rejected(case):
    baseline = before(case)
    current = fixed(case)
    report = compare(case, baseline, current)
    with sqlite3.connect(case.store.db_path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM comparisons").fetchone()[0])
        payload["PatientName"] = "forbidden synthetic field"
        connection.execute("UPDATE comparisons SET payload=?", (json.dumps(payload),))
    with pytest.raises(StorageError):
        case.store.get_comparison(report.comparison_id)
