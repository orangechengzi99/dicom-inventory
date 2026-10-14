"""DICOM discovery, metadata reading, validation, and statistics."""

from __future__ import annotations

import os
import warnings
from datetime import datetime, timezone
from uuid import uuid4
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pydicom
from pydicom.multival import MultiValue

from .models import InventoryRecord, InventoryResult, InventorySummary, ScanRun, Severity, RuleAssessment
from .cancellation import CancelCheck, ScanCancelled, checkpoint, checked
from .quality import RULE_VERSION, evaluate_record, evaluate_quality
from .series import summarize_hierarchy

DICOM_FIELDS: tuple[str, ...] = (
    "SOPClassUID",
    "NumberOfFrames",
    "FrameOfReferenceUID",
    "ImageType",
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
)

_FIELD_TO_ATTRIBUTE: dict[str, str] = {
    "SOPClassUID": "sop_class_uid",
    "NumberOfFrames": "number_of_frames",
    "FrameOfReferenceUID": "frame_of_reference_uid",
    "ImageType": "image_type",
    "Modality": "modality",
    "StudyInstanceUID": "study_instance_uid",
    "SeriesInstanceUID": "series_instance_uid",
    "SOPInstanceUID": "sop_instance_uid",
    "Rows": "rows",
    "Columns": "columns",
    "PixelSpacing": "pixel_spacing",
    "SliceThickness": "slice_thickness",
    "ImagePositionPatient": "image_position_patient",
    "ImageOrientationPatient": "image_orientation_patient",
    "InstanceNumber": "instance_number",
    "RescaleSlope": "rescale_slope",
    "RescaleIntercept": "rescale_intercept",
}

ProgressCallback = Callable[[int, int, InventoryRecord], None]


class DiscoveredPaths(list[Path]):
    """List-compatible result that retains discovery completeness evidence."""
    def __init__(self, paths: list[Path], notes: tuple[str, ...] = (), *, cancelled: bool = False) -> None:
        super().__init__(paths)
        self.notes = notes
        self.cancelled = cancelled
        self.complete = not notes and not cancelled


def _has_dicom_prefix(path: Path, on_error: Callable[[], None] | None = None) -> bool:
    try:
        with path.open("rb") as stream:
            stream.seek(128)
            return stream.read(4) == b"DICM"
    except OSError:
        if on_error is not None:
            on_error()
        return False


def _is_candidate(path: Path, on_error: Callable[[], None] | None = None) -> bool:
    if path.suffix.casefold() in {".dcm", ".dicom"}:
        return True
    return _has_dicom_prefix(path, on_error)


def discover_dicom_files(root: str | Path, *, cancel_check: CancelCheck = None) -> list[Path]:
    """Recursively return absolute DICOM candidate paths in stable order.

    Extension candidates are retained even if damaged so that the subsequent
    inventory run can report their read failure. Inaccessible directories are
    skipped without terminating discovery, but the list-compatible return value
    retains incomplete-scope notes. Unreadable prefix probes do the same.
    """

    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"文件夹不存在：{root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{root_path}")

    candidates: list[Path] = []
    notes: list[str] = []
    def inaccessible(error: OSError) -> None:
        notes.append("存在无法访问的目录，发现范围不完整。")
    cancelled = False
    try:
        checkpoint(cancel_check, "发现文件")
        for directory, _, filenames in checked(os.walk(root_path, onerror=inaccessible), cancel_check, "发现文件"):
            directory_path = Path(directory)
            for filename in checked(filenames, cancel_check, "发现文件"):
                path = directory_path / filename
                if _is_candidate(path, lambda: notes.append("存在无法检查文件头的文件，发现范围不完整。")):
                    candidates.append(path.resolve())
        candidates.sort(key=lambda item: str(item).casefold())
        checkpoint(cancel_check, "发现文件")
    except ScanCancelled:
        cancelled = True
        notes.append(f"发现文件时取消；已发现 {len(candidates)} 个候选，目录总数未知，尚未读取元数据。")
    return DiscoveredPaths(candidates, tuple(dict.fromkeys(notes)), cancelled=cancelled)


def _normalise_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (MultiValue, list, tuple)):
        return "\\".join(str(item) for item in value)
    return str(value)


def _read_record(path: Path) -> InventoryRecord:
    try:
        # Parser warnings can contain raw malformed attribute values. Do not send
        # them to logs; malformed-value validation is outside this small profile.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            dataset = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=list(DICOM_FIELDS))
            values = {keyword: _normalise_value(dataset.get(keyword)) for keyword in DICOM_FIELDS}
        kwargs = {
            attribute: values[keyword]
            for keyword, attribute in _FIELD_TO_ATTRIBUTE.items()
        }
        return InventoryRecord(
            file_path=str(path),
            media_storage_sop_class_uid=str(dataset.file_meta.get("MediaStorageSOPClassUID", "")),
            **kwargs,
        )
    except Exception as error:  # One bad file must not stop the batch.
        return InventoryRecord(
            file_path=str(path),
            read_success=False,
            error_message=f"{type(error).__name__}: 无法解析所选元数据，请核对权限和文件格式。",
        )


def inventory_files(
    paths: Iterable[str | Path],
    progress_callback: ProgressCallback | None = None,
    *,
    source_root: str | Path | None = None,
    discovery_complete: bool = True,
    discovery_notes: tuple[str, ...] = (),
    cancel_check: CancelCheck = None,
) -> InventoryResult:
    """Read candidate files and return records plus UID statistics."""

    started_at = datetime.now(timezone.utc).isoformat()
    if isinstance(paths, DiscoveredPaths):
        discovery_complete = discovery_complete and paths.complete
        discovery_notes = (*discovery_notes, *paths.notes)
    records: list[InventoryRecord] = []
    total = None
    try:
        if isinstance(paths, DiscoveredPaths) and paths.cancelled:
            raise ScanCancelled("发现文件")
        file_paths = list(dict.fromkeys(Path(path).expanduser().resolve()
                          for path in checked(paths, cancel_check, "准备文件")))
        total = len(file_paths)
        for processed, path in enumerate(file_paths, start=1):
            checkpoint(cancel_check, "读取元数据")
            record = _read_record(path)
            evaluate_record(record, source_root)
            records.append(record)
            if progress_callback is not None:
                progress_callback(processed, total, record)
        checkpoint(cancel_check, "检查质量")
        issues = evaluate_quality(records, source_root, cancel_check=cancel_check,
                                  include_series=discovery_complete)
        if not discovery_complete:
            _mark_incomplete(records)
        return _assemble_result(records, issues, started_at, source_root,
                                "完成" if discovery_complete else "未完成", discovery_notes, cancel_check)
    except ScanCancelled as cancelled:
        # Discard interrupted series evidence. Retain reproducible file facts and
        # SOP duplicates observed within the read subset, not a coverage claim.
        issues = evaluate_quality(records, source_root, include_series=False)
        _mark_incomplete(records)
        coverage = f"已读取 {len(records)} / {total} 个候选。" if total is not None else f"已读取 {len(records)} 个文件；候选总数尚未确认。"
        notes = (*discovery_notes, f"在{cancelled.stage}阶段取消。", coverage,
                 "本批次未完成；仅保留已读文件事实和已读范围内的SOP重复观察；序列检查未评估，不能判断问题已解决。")
        return _assemble_result(records, issues, started_at, source_root, "已取消", notes)


def _mark_incomplete(records):
    for record in records:
        record.slice_sort_position = None
        record.series_checks = (RuleAssessment("SCAN_INCOMPLETE", "未评估",
            "扫描范围或检查流程未完成；序列检查未评估，不能据此判断完整性或问题已解决。"),)


def _assemble_result(records, issues, started_at, source_root, status, notes, cancel_check=None):
    total = len(records)
    sop_records: dict[str, list[InventoryRecord]] = defaultdict(list)
    for record in checked(records, cancel_check, "汇总结果"):
        if record.read_success and record.sop_instance_uid:
            sop_records[record.sop_instance_uid].append(record)

    duplicates: dict[str, list[str]] = {}
    for sop_uid, grouped_records in checked(sop_records.items(), cancel_check, "汇总结果"):
        if len(grouped_records) > 1:
            duplicates[sop_uid] = [record.file_path for record in grouped_records]
            for record in grouped_records:
                record.is_duplicate = True

    successful = [record for record in records if record.read_success]
    studies = {
        record.study_instance_uid
        for record in successful
        if record.study_instance_uid
    }
    series = {
        record.series_instance_uid
        for record in successful
        if record.series_instance_uid
    }
    instances = {
        record.sop_instance_uid
        for record in successful
        if record.sop_instance_uid
    }
    summary = InventorySummary(
        file_count=total,
        success_count=len(successful),
        failure_count=total - len(successful),
        study_count=len(studies),
        series_count=len(series),
        unique_instance_count=len(instances),
        duplicate_instance_count=len(duplicates),
        anomaly_file_count=sum(record.is_anomaly for record in records),
        issue_count=len(issues),
        error_count=sum(issue.severity == Severity.ERROR for issue in issues),
        warning_count=sum(issue.severity == Severity.WARNING for issue in issues),
        info_count=sum(issue.severity == Severity.INFO for issue in issues),
        unevaluated_count=sum(record.assessment_status == "未评估" or
                              any(check.code == "SCAN_INCOMPLETE" for check in record.series_checks) for record in records),
    )
    study_summaries, series_summaries = summarize_hierarchy(records, source_root, cancel_check=cancel_check)
    checkpoint(cancel_check, "完成汇总")
    run = ScanRun(str(uuid4()), started_at, datetime.now(timezone.utc).isoformat(),
                  str(Path(source_root).resolve()) if source_root is not None else "",
                  status, RULE_VERSION, summary, notes)
    return InventoryResult(records, summary, duplicates, issues, run, study_summaries, series_summaries)
