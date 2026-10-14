"""Real cooperative cancellation with generated, anonymous DICOM only."""
import csv
import hashlib
import json
from pathlib import Path

import pytest
from pydicom.uid import generate_uid

from dicom_inventory.core import inventory_files, discover_dicom_files
from dicom_inventory.core import inventory as inv
from dicom_inventory.core.cancellation import CancellationToken, ScanCancelled
from dicom_inventory.core.exporter import export_csv, export_series_csv, export_issues_csv
from dicom_inventory.storage import SnapshotStore
from dicom_inventory.handling_export import export_handling_csv


@pytest.fixture
def scan_sample(tmp_path, make_dicom):
    root = tmp_path / "source"
    study, series, frame, sop = [generate_uid() for _ in range(4)]
    paths = []
    for i in range(6):
        values = {"FrameOfReferenceUID": frame, "InstanceNumber": i+1,
                  "ImagePositionPatient": [0, 0, i*1.25], "ImageType": ["ORIGINAL", "PRIMARY"]}
        if i == 2:
            values["SliceThickness"] = None
        if i == 3:
            values["Rows"] = 256
        paths.append(make_dicom(root / f"CT_{i:03}.dcm", study_uid=study, series_uid=series,
                                sop_uid=sop if i < 2 else None, overrides=values))
    return root, paths


def stop_after(root, paths, count):
    token = CancellationToken()
    if count == 0:
        token.request()
    def progress(processed, total, record):
        if processed == count:
            token.request()
    return inventory_files(paths, progress, source_root=root, cancel_check=token.checkpoint)


def assert_partial(result, count):
    assert result.scan_run.status == "已取消"
    assert result.summary.file_count == count == len(result.records)
    assert result.summary.success_count + result.summary.failure_count == count
    assert all(item.scope_kind != "series" for item in result.issues)
    assert all(record.slice_sort_position is None for record in result.records)
    assert all([(c.code, c.status) for c in record.series_checks] == [("SCAN_INCOMPLETE", "未评估")]
               for record in result.records)
    assert all("未评估" in series.overall_status and series.spatial_range is None for series in result.series)
    assert "不能判断问题已解决" in "".join(result.scan_run.notes)


@pytest.mark.parametrize("count", [0, 1, 3, 6])
def test_cancel_before_during_and_after_last_read(scan_sample, count):
    root, paths = scan_sample
    result = stop_after(root, paths, count)
    assert_partial(result, count)
    if count == 1:
        assert result.records[0].quality_status == "未评估"
    if count >= 2:
        assert len(result.duplicates) == 1
        duplicate = next(item for item in result.issues if item.code == "DUPLICATE_SOP_UID")
        assert len(duplicate.affected_files) == 2
        assert result.records[0].quality_status == "需核对"


@pytest.mark.parametrize("stage", ["准备文件", "读取元数据", "检查质量", "序列归组", "检查序列",
                                  "检查空间位置", "汇总结果", "汇总分层", "完成汇总"])
def test_cancel_each_real_processing_stage(scan_sample, stage):
    root, paths = scan_sample
    seen = []
    def check(current):
        seen.append(current)
        if current == stage:
            raise ScanCancelled(stage)
    result = inventory_files(paths, source_root=root, cancel_check=check)
    assert stage in seen
    assert_partial(result, 0 if stage in {"准备文件", "读取元数据"} else 6)
    assert stage in "".join(result.scan_run.notes)


def test_dense_position_inner_loop_can_stop(monkeypatch):
    from dicom_inventory.core import series
    token = CancellationToken()
    distances = []
    real_dist = series.math.dist
    def distance(a, b):
        distances.append((a, b))
        if len(distances) == 3:
            token.request()
        return real_dist(a, b)
    monkeypatch.setattr(series.math, "dist", distance)
    points = [(i * 0.000001, 0., 0.) for i in range(200)]
    with pytest.raises(ScanCancelled):
        series._duplicate_positions(list(range(200)), points, token.checkpoint)
    assert len(distances) == 3


@pytest.mark.parametrize("before", [True, False])
def test_discovery_cancel_retains_scope_but_does_not_read(scan_sample, monkeypatch, before):
    root, paths = scan_sample
    token = CancellationToken()
    probes = []
    real_candidate = inv._is_candidate
    def candidate(*args):
        found = real_candidate(*args)
        probes.append(args[0])
        if len(probes) == 2:
            token.request()
        return found
    monkeypatch.setattr(inv, "_is_candidate", candidate)
    if before:
        token.request()
    found = discover_dicom_files(root, cancel_check=token.checkpoint)
    assert found.cancelled and not found.complete
    assert len(found) == (0 if before else 2)
    monkeypatch.setattr(inv, "_read_record", lambda *_: pytest.fail("Cancelled discovery must not start reading"))
    result = inventory_files(found, source_root=root)
    assert_partial(result, 0)
    assert "目录总数未知" in "".join(result.scan_run.notes)


def test_incomplete_discovery_does_not_infer_series_completion(scan_sample):
    root, paths = scan_sample
    result = inventory_files(inv.DiscoveredPaths(paths[:3], ("无法访问部分目录",)), source_root=root)
    assert result.scan_run.status == "未完成"
    assert not any(issue.scope_kind == "series" for issue in result.issues)
    assert all(record.series_checks[0].code == "SCAN_INCOMPLETE" for record in result.records)


def test_noop_check_preserves_complete_results_and_late_request(scan_sample):
    root, paths = scan_sample
    baseline = inventory_files(paths, source_root=root)
    token = CancellationToken()
    actual = inventory_files(paths, source_root=root, cancel_check=token.checkpoint)
    token.request()  # A request after the final accepted checkpoint cannot undo completion.
    assert actual.scan_run.status == "完成"
    for field in ("records", "summary", "duplicates", "issues", "studies", "series"):
        assert getattr(actual, field) == getattr(baseline, field)


def test_cancel_privacy_and_source_hashes(scan_sample, monkeypatch):
    root, paths = scan_sample
    original = {p: hashlib.sha256(p.read_bytes()).digest() for p in paths}
    calls = []
    real_read = inv.pydicom.dcmread
    def read(*args, **kwargs):
        calls.append(kwargs)
        return real_read(*args, **kwargs)
    monkeypatch.setattr(inv.pydicom, "dcmread", read)
    result = stop_after(root, paths, 3)
    assert len(calls) == 3
    assert all(call["stop_before_pixels"] and "PatientName" not in call["specific_tags"]
               and "PatientID" not in call["specific_tags"] and "PixelData" not in call["specific_tags"] for call in calls)
    assert {p: hashlib.sha256(p.read_bytes()).digest() for p in paths} == original
    assert "PRIVATE-ID" not in repr(result)


def test_read_failure_does_not_prevent_later_safe_cancel(scan_sample):
    root, paths = scan_sample
    bad = root / "synthetic-bad.dcm"
    bad.write_bytes(b"synthetic invalid")
    result = stop_after(root, [bad, *paths], 2)
    assert_partial(result, 2)
    assert result.summary.failure_count == 1 and result.summary.success_count == 1


@pytest.mark.parametrize("count", [0, 3, 6])
def test_partial_snapshot_roundtrip(scan_sample, tmp_path, count):
    root, paths = scan_sample
    result = stop_after(root, paths, count)
    work = tmp_path / "work"
    work.mkdir()
    store = SnapshotStore(work)
    store.save(result)
    assert SnapshotStore(work).load(result.scan_run.run_id) == result
    assert store.list_scans()[0].status == "已取消"
    assert b"PRIVATE-ID" not in store.db_path.read_bytes()


def test_partial_observation_never_closes_or_deletes_human_history(scan_sample, tmp_path):
    root, paths = scan_sample
    work = tmp_path / "work"
    work.mkdir()
    store = SnapshotStore(work)
    complete = inventory_files(paths, source_root=root)
    store.save(complete)
    initial = store.get_handling(complete.scan_run.run_id)
    store.update_handling(complete.scan_run.run_id,
        {key: (record.track_key, record.revision) for key, record in initial.items()}, "已处理待复检", "测试处理说明")
    before = {key: store.get_history(complete.scan_run.run_id, key) for key in initial}
    partial = stop_after(root, paths, 3)
    store.save(partial)
    assert all(item.state == "已处理待复检" for item in store.get_handling(partial.scan_run.run_id).values())
    assert all(item.state == "已处理待复检" for item in store.get_handling(complete.scan_run.run_id).values())
    assert before == {key: store.get_history(complete.scan_run.run_id, key) for key in initial}
    assert store.load(complete.scan_run.run_id) == complete


@pytest.mark.parametrize("kind", ["files", "series", "issues", "handling"])
@pytest.mark.parametrize("count", [0, 3])
def test_partial_csv_scope_names_encoding_and_columns(scan_sample, tmp_path, kind, count):
    root, paths = scan_sample
    result = stop_after(root, paths, count)
    output = tmp_path / "output"
    output.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    store = SnapshotStore(work)
    store.save(result)
    exporters = {"files": export_csv, "series": export_series_csv, "issues": export_issues_csv,
                 "handling": lambda r, d, **kw: export_handling_csv(r, d, work, **kw)}
    exporter = exporters[kind]
    destination = exporter(result, output, source_root=root)
    assert destination.name.startswith("partial_")
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    sidecar = destination.with_name(destination.name + ".scan.json")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["complete"] is False and manifest["status"] == "已取消"
    assert manifest["read_file_count"] == count and manifest["csv_file"] == destination.name
    assert manifest["run_id"] == result.scan_run.run_id
    assert "PatientName" not in sidecar.read_text(encoding="utf-8")
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert len(reader.fieldnames) == {"files": 19, "series": 19, "issues": 13, "handling": 21}[kind]
        rows = list(reader)
    assert len(rows) == {"files": count, "series": len(result.series), "issues": len(result.issues), "handling": len(result.issues)}[kind]
    second = exporter(result, output, source_root=root)
    assert second != destination and second.exists() and sidecar.exists()
    with pytest.raises(ValueError):
        exporter(result, root, source_root=root)


def test_sidecar_failure_is_not_reported_as_success(scan_sample, tmp_path, monkeypatch):
    root, paths = scan_sample
    result = stop_after(root, paths, 3)
    output = tmp_path / "output"
    output.mkdir()
    real_open = Path.open
    def open_path(self, *args, **kwargs):
        if self.name.endswith(".scan.json"):
            raise PermissionError("synthetic sidecar failure")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_path)
    from dicom_inventory.gui.workers import ExportWorker
    worker = ExportWorker(result, output, root, "issues")
    completed, failed = [], []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert not completed and len(failed) == 1
    assert (output / "partial_issues.csv").exists()  # Recoverable, explicitly partial artifact.
