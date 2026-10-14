"""Data models returned by the inventory core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"

    @property
    def rank(self) -> int:
        return {self.INFO: 1, self.WARNING: 2, self.ERROR: 3}[self]


@dataclass(frozen=True, slots=True)
class QualityIssue:
    issue_id: str
    code: str
    rule_version: str
    issue_type: str
    severity: Severity
    affected_files: tuple[str, ...]
    study_uids: tuple[str, ...]
    series_uids: tuple[str, ...]
    description: str
    impact: str
    recommendation: str
    field_name: str = ""
    scope_kind: str = "file"
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RuleAssessment:
    code: str
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StudySummary:
    group_key: str
    study_instance_uid: str
    series_keys: tuple[str, ...]
    files: tuple[str, ...]
    modalities: tuple[str, ...]
    issues: tuple[QualityIssue, ...]
    overall_status: str
    highest_severity: Severity | None

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def series_count(self) -> int:
        return len(self.series_keys)

    @property
    def issue_count(self) -> int:
        return len(self.issues)


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    group_key: str
    series_instance_uid: str
    study_uids: tuple[str, ...]
    files: tuple[str, ...]
    modalities: tuple[str, ...]
    rows: tuple[str, ...]
    columns: tuple[str, ...]
    pixel_spacings: tuple[str, ...]
    slice_thicknesses: tuple[str, ...]
    orientations: tuple[str, ...]
    spatial_range: tuple[float, float] | None
    sorted_positions: tuple[tuple[str, float], ...]
    issues: tuple[QualityIssue, ...]
    assessments: tuple[RuleAssessment, ...]
    overall_status: str
    highest_severity: Severity | None
    frame_of_reference_uids: tuple[str, ...] = ()
    image_types: tuple[str, ...] = ()

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def issue_count(self) -> int:
        return len(self.issues)


@dataclass(frozen=True, slots=True)
class ScanRun:
    run_id: str
    started_at: str
    finished_at: str
    source_root: str
    status: str
    rule_version: str
    summary: InventorySummary
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class InventoryRecord:
    """Metadata and validation state for one candidate file."""

    file_path: str
    modality: str = ""
    study_instance_uid: str = ""
    series_instance_uid: str = ""
    sop_instance_uid: str = ""
    rows: str = ""
    columns: str = ""
    pixel_spacing: str = ""
    slice_thickness: str = ""
    image_position_patient: str = ""
    image_orientation_patient: str = ""
    instance_number: str = ""
    rescale_slope: str = ""
    rescale_intercept: str = ""
    read_success: bool = True
    missing_tags: tuple[str, ...] = ()
    is_duplicate: bool = False
    error_message: str = ""
    sop_class_uid: str = ""
    media_storage_sop_class_uid: str = ""
    number_of_frames: str = ""
    assessment_status: str = "未评估"
    issues: tuple[QualityIssue, ...] = ()
    frame_of_reference_uid: str = ""
    image_type: str = ""
    series_checks: tuple[RuleAssessment, ...] = ()
    slice_sort_position: float | None = None

    @property
    def highest_severity(self) -> Severity | None:
        return max((issue.severity for issue in self.issues), key=lambda item: item.rank, default=None)

    @property
    def quality_status(self) -> str:
        if not self.read_success:
            return "读取失败"
        if self.highest_severity == Severity.ERROR:
            return "错误"
        if self.assessment_status == "未评估":
            return "未评估"
        if self.highest_severity == Severity.WARNING:
            return "需核对"
        if self.highest_severity == Severity.INFO:
            return "提示"
        if any(check.code == "SCAN_INCOMPLETE" for check in self.series_checks):
            return "未评估"
        return "已检查"

    @property
    def status(self) -> str:
        return "成功" if self.read_success else "读取失败"

    @property
    def anomaly_types(self) -> tuple[str, ...]:
        anomalies: list[str] = []
        if self.issues:
            return tuple(dict.fromkeys(issue.issue_type for issue in self.issues))
        if not self.read_success:
            anomalies.append("读取失败")
        if self.missing_tags:
            anomalies.append("标签缺失")
        if self.is_duplicate:
            anomalies.append("SOPInstanceUID重复")
        return tuple(anomalies)

    @property
    def is_anomaly(self) -> bool:
        return bool(self.anomaly_types)


@dataclass(frozen=True, slots=True)
class InventorySummary:
    """Aggregate counts for one inventory run."""

    file_count: int
    success_count: int
    failure_count: int
    study_count: int
    series_count: int
    unique_instance_count: int
    duplicate_instance_count: int
    anomaly_file_count: int = 0
    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    unevaluated_count: int = 0


@dataclass(slots=True)
class InventoryResult:
    """Complete inventory records, summary, and duplicate file mapping."""

    records: list[InventoryRecord]
    summary: InventorySummary
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()
    scan_run: ScanRun | None = None
    studies: tuple[StudySummary, ...] = ()
    series: tuple[SeriesSummary, ...] = ()
