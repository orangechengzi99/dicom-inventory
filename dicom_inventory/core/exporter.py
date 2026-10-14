"""Excel-friendly CSV export for inventory results."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from .models import InventoryRecord, InventoryResult

CSV_COLUMNS: tuple[str, ...] = (
    "FilePath",
    "Modality",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "Rows",
    "Columns",
    "PixelSpacing",
    "SliceThickness",
    "ImagePositionPatient",
    "ImageOrientationPatient",
    "InstanceNumber",
    "RescaleSlope",
    "RescaleIntercept",
    "Status",
    "AnomalyTypes",
    "MissingTags",
    "IsDuplicate",
    "ErrorMessage",
)


def _record_row(record: InventoryRecord) -> dict[str, str]:
    return {
        "FilePath": record.file_path,
        "Modality": record.modality,
        "StudyInstanceUID": record.study_instance_uid,
        "SeriesInstanceUID": record.series_instance_uid,
        "SOPInstanceUID": record.sop_instance_uid,
        "Rows": record.rows,
        "Columns": record.columns,
        "PixelSpacing": record.pixel_spacing,
        "SliceThickness": record.slice_thickness,
        "ImagePositionPatient": record.image_position_patient,
        "ImageOrientationPatient": record.image_orientation_patient,
        "InstanceNumber": record.instance_number,
        "RescaleSlope": record.rescale_slope,
        "RescaleIntercept": record.rescale_intercept,
        "Status": record.status,
        "AnomalyTypes": ";".join(record.anomaly_types),
        "MissingTags": ";".join(record.missing_tags),
        "IsDuplicate": "是" if record.is_duplicate else "否",
        "ErrorMessage": record.error_message,
    }


def is_output_directory_allowed(source_root: str | Path, output_dir: str | Path) -> bool:
    """Return false when output is the source directory or one of its children."""

    source = Path(source_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    return output != source and not output.is_relative_to(source)


def export_csv(
    result: InventoryResult,
    output_dir: str | Path,
    *,
    timestamp: datetime | None = None,
    source_root: str | Path | None = None,
) -> Path:
    """Export every inventory record to a new UTF-8-SIG CSV file."""

    directory = _validated_directory(result, output_dir, source_root)

    moment = timestamp or datetime.now()
    stem = _export_stem(result, f"dicom_inventory_{moment:%Y%m%d_%H%M%S}")
    destination = directory / f"{stem}.csv"
    suffix = 1
    while destination.exists():
        destination = directory / f"{stem}_{suffix}.csv"
        suffix += 1

    with destination.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows({key: _csv_safe(value) for key, value in _record_row(record).items()} for record in result.records)
    return _finish_export(result, destination)


SERIES_CSV_COLUMNS = (
    "SeriesKey", "SeriesInstanceUID", "StudyUIDs", "FileCount", "Modalities", "RowsValues", "ColumnsValues",
    "PixelSpacingValues", "SliceThicknessValues", "OrientationValues", "ProjectedRangeMM", "SortedPositions",
    "IssueCount", "HighestSeverity", "OverallStatus", "RuleAssessments", "Files", "FrameOfReferenceUIDs", "ImageTypes",
)


def export_series_csv(result: InventoryResult, output_dir: str | Path, *, source_root=None) -> Path:
    directory = _validated_directory(result, output_dir, source_root)
    stem = _export_stem(result, "series")
    suffix = 0
    while True:
        destination = directory / (f"{stem}.csv" if suffix == 0 else f"{stem}_{suffix}.csv")
        try:
            stream = destination.open("x", encoding="utf-8-sig", newline="")
            break
        except FileExistsError:
            suffix += 1
    with stream:
        writer = csv.DictWriter(stream, fieldnames=SERIES_CSV_COLUMNS)
        writer.writeheader()
        for series in result.series:
            arrays = (series.study_uids, series.modalities, series.rows, series.columns, series.pixel_spacings,
                      series.slice_thicknesses, series.orientations, series.spatial_range, series.sorted_positions)
            encoded = [json.dumps(value, ensure_ascii=False, allow_nan=False) for value in arrays]
            values = (series.group_key, series.series_instance_uid, encoded[0], str(series.file_count), *encoded[1:],
                      str(series.issue_count), str(series.highest_severity or ""), series.overall_status,
                      json.dumps([{"code": check.code, "status": check.status, "reason": check.reason}
                                  for check in series.assessments], ensure_ascii=False),
                      json.dumps(series.files, ensure_ascii=False), json.dumps(series.frame_of_reference_uids, ensure_ascii=False),
                      json.dumps(series.image_types, ensure_ascii=False))
            writer.writerow({key: _csv_safe(value) for key, value in zip(SERIES_CSV_COLUMNS, values)})
    return _finish_export(result, destination)


ISSUE_CSV_COLUMNS = (
    "IssueID", "RuleCode", "RuleVersion", "IssueType", "Severity", "StudyUIDs",
    "SeriesUIDs", "AffectedFiles", "FieldName", "Description", "PossibleImpact", "Recommendation", "Evidence",
)


def _csv_safe(value: str) -> str:
    # Spreadsheet software can execute formulas in cells sourced from filenames
    # or malformed metadata. Normal values keep the legacy representation.
    numeric = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    if re.fullmatch(numeric + r"(?:\\" + numeric + r")*", value):
        return value  # Preserve normal negative numeric metadata.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _validated_directory(result: InventoryResult, output_dir: str | Path,
                         source_root: str | Path | None) -> Path:
    directory = Path(output_dir).expanduser().resolve()
    source = source_root or (result.scan_run.source_root if result.scan_run else "")
    if source and not is_output_directory_allowed(source, directory):
        raise ValueError("输出不能位于原始DICOM目录或其子目录。")
    # Direct API calls without a root still must not write beside input files.
    if any(directory == Path(record.file_path).resolve().parent or
           directory.is_relative_to(Path(record.file_path).resolve().parent)
           for record in result.records):
        raise ValueError("输出不能位于输入文件所在目录或其子目录。")
    if not directory.exists():
        raise FileNotFoundError("输出目录不存在。")
    if not directory.is_dir():
        raise NotADirectoryError("输出路径不是文件夹。")
    return directory


def export_issues_csv(result: InventoryResult, output_dir: str | Path, *,
                      source_root: str | Path | None = None) -> Path:
    """One row per structured issue, all linked files as unambiguous JSON arrays."""
    directory = _validated_directory(result, output_dir, source_root)
    stem = _export_stem(result, "issues")
    suffix = 0
    while True:
        destination = directory / (f"{stem}.csv" if suffix == 0 else f"{stem}_{suffix}.csv")
        try:
            stream = destination.open("x", encoding="utf-8-sig", newline="")
            break
        except FileExistsError:
            suffix += 1
    with stream:
        writer = csv.DictWriter(stream, fieldnames=ISSUE_CSV_COLUMNS)
        writer.writeheader()
        for issue in result.issues:
            row = dict(zip(ISSUE_CSV_COLUMNS, (
                issue.issue_id, issue.code, issue.rule_version, issue.issue_type, str(issue.severity),
                json.dumps(issue.study_uids, ensure_ascii=False),
                json.dumps(issue.series_uids, ensure_ascii=False),
                json.dumps(issue.affected_files, ensure_ascii=False), issue.field_name,
                issue.description, issue.impact, issue.recommendation, json.dumps(dict(issue.evidence), ensure_ascii=False),
            )))
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    return _finish_export(result, destination)


def _export_stem(result, stem):
    return "partial_" + stem if result.scan_run and result.scan_run.status != "完成" else stem


def _finish_export(result, destination):
    """A partial CSV is only reported saved after its matching scope note exists."""
    run = result.scan_run
    if run and run.status != "完成":
        manifest = {
            "run_id": run.run_id, "status": run.status, "complete": False,
            "started_at": run.started_at, "finished_at": run.finished_at,
            "rule_version": run.rule_version, "csv_file": destination.name,
            "read_file_count": result.summary.file_count,
            "success_count": result.summary.success_count, "failure_count": result.summary.failure_count,
            "issue_count": result.summary.issue_count, "notes": list(run.notes),
            "scope": "仅覆盖已读取记录；不证明目录完整、序列检查完成或问题已解决。",
        }
        with destination.with_name(destination.name + ".scan.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return destination
