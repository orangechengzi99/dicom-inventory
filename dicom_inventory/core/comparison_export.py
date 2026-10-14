"""Excel-friendly mixed comparison rows, including scope and unresolved evidence."""
import csv
import json
from .comparison import COMPARISON_VERSION, comparison_id
from .exporter import _validated_directory, _csv_safe

COMPARISON_COLUMNS = (
    "ComparisonID", "BaselineRun", "CurrentRun", "RecordType", "RecordKey", "Status", "Observation",
    "IssueID", "RuleCode", "Description", "BaselineFiles", "CurrentFiles", "Before", "After",
    "HandlingBefore", "HandlingAfter", "SystemAction", "Reason", "Comparable", "Limitations",
)


def export_comparison_csv(report, baseline, current, output_dir):
    if (report.version != COMPARISON_VERSION or
        (report.baseline_run, report.current_run) != (baseline.scan_run.run_id, current.scan_run.run_id) or
        report.comparison_id != comparison_id(report.baseline_run, report.current_run)):
        raise ValueError("对比结果与两个批次不匹配，未导出。")
    directory = _validated_directory(current, output_dir, current.scan_run.source_root)
    _validated_directory(baseline, output_dir, baseline.scan_run.source_root)
    stem = "comparison" if report.comparable else "partial_comparison"
    suffix = 0
    while True:
        destination = directory / (f"{stem}.csv" if suffix == 0 else f"{stem}_{suffix}.csv")
        try:
            stream = destination.open("x", encoding="utf-8-sig", newline="")
            break
        except FileExistsError:
            suffix += 1
    def row(kind, key, **values):
        data = dict.fromkeys(COMPARISON_COLUMNS, "")
        data.update(ComparisonID=report.comparison_id, BaselineRun=report.baseline_run, CurrentRun=report.current_run,
                    RecordType=kind, RecordKey=key, Comparable="是" if report.comparable else "否",
                    Limitations=json.dumps(report.limits, ensure_ascii=False))
        data.update(values)
        return {name: _csv_safe(str(value)) for name, value in data.items()}
    with stream:
        writer = csv.DictWriter(stream, fieldnames=COMPARISON_COLUMNS)
        writer.writeheader()
        writer.writerow(row("比较范围", report.comparison_id, Status="可比" if report.comparable else "无法完整比较",
            Before=baseline.scan_run.status, After=current.scan_run.status, Reason="；".join(report.reasons)))
        for item in report.count_changes:
            writer.writerow(row("数量变化", item.metric, Before=str(item.before), After=str(item.after),
                                Description=f"变化 {item.after-item.before:+d}"))
        for item in report.file_changes:
            writer.writerow(row("文件变化", item.relative_path, Status=item.status, Reason=item.reason,
                Before=json.dumps({name: before for name, before, after in item.fields}, ensure_ascii=False),
                After=json.dumps({name: after for name, before, after in item.fields}, ensure_ascii=False)))
        for item in report.issue_changes:
            writer.writerow(row("问题变化", item.issue_id, Status=item.status, Observation=item.observation,
                IssueID=item.issue_id, RuleCode=item.code, Description=item.description,
                BaselineFiles=json.dumps(item.baseline_files, ensure_ascii=False),
                CurrentFiles=json.dumps(item.current_files, ensure_ascii=False), HandlingBefore=item.handling_before,
                HandlingAfter=item.handling_after, SystemAction=item.action, Reason=item.reason))
    return destination
