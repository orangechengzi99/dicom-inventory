"""Extended issue CSV with persisted human metadata; legacy exporters unchanged."""
import csv
import json
from .core.exporter import ISSUE_CSV_COLUMNS, _validated_directory, _csv_safe, _export_stem, _finish_export
from .handling import track_key
from .storage import SnapshotStore, StorageError

HANDLING_COLUMNS = ("HandlingState", "HumanNote", "HandlingUpdatedAt", "HandlingRevision",
                    "FirstSeenRun", "LatestSeenRun", "HandlingTrackKey", "ExportScanRun")


def export_handling_csv(result, output_dir, work_dir, *, source_root=None):
    directory = _validated_directory(result, output_dir, source_root)
    run = result.scan_run
    if run is None:
        raise StorageError("缺少保存批次，不能导出人工处理记录。")
    records = SnapshotStore(work_dir, source_root).get_handling(run.run_id)
    if set(records) != {item.issue_id for item in result.issues} or any(
            records[item.issue_id].track_key != track_key(run.source_root, run.rule_version, item)
            for item in result.issues):
        raise StorageError("当前结果与保存证据不同，未导出人工处理记录。")
    suffix = 0
    stem = _export_stem(result, "issues")
    while True:
        destination = directory / (f"{stem}.csv" if suffix == 0 else f"{stem}_{suffix}.csv")
        try:
            stream = destination.open("x", encoding="utf-8-sig", newline="")
            break
        except FileExistsError:
            suffix += 1
    with stream:
        writer = csv.DictWriter(stream, fieldnames=(*ISSUE_CSV_COLUMNS, *HANDLING_COLUMNS))
        writer.writeheader()
        for issue in result.issues:
            record = records[issue.issue_id]
            values = (issue.issue_id, issue.code, issue.rule_version, issue.issue_type, str(issue.severity),
                json.dumps(issue.study_uids, ensure_ascii=False), json.dumps(issue.series_uids, ensure_ascii=False),
                json.dumps(issue.affected_files, ensure_ascii=False), issue.field_name, issue.description,
                issue.impact, issue.recommendation, json.dumps(dict(issue.evidence), ensure_ascii=False),
                record.state, record.note, record.updated_at, str(record.revision), record.first_run,
                record.latest_run, record.track_key, run.run_id)
            writer.writerow({key: _csv_safe(value) for key, value in zip((*ISSUE_CSV_COLUMNS, *HANDLING_COLUMNS), values)})
    return _finish_export(result, destination)

