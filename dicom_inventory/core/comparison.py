"""Compare immutable metadata snapshots without Qt, database or DICOM reads."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .quality import field_requirements, supported_profile, valid_uid, FIELD_ATTRIBUTES

COMPARISON_VERSION = "comparison-1"
METADATA_FIELDS = tuple(dict.fromkeys((*FIELD_ATTRIBUTES.values(), "media_storage_sop_class_uid", "read_success")))
LIMITS = (
    "文件按相同来源目录内的相对路径匹配；改名/移动表示减少与新增，不推测为同一文件。",
    "仅比较保存的所选元数据；未比较文件字节、像素、未读取字段或扫描期间的数据稳定性。",
    "已解决只指关联所选规则本次已执行且不再报告；不代表完整DICOM合规或临床可用。",
)
SERIES_RULES = {
    "SERIES_STUDY_CONFLICT": "STUDY_ASSOCIATION", "SERIES_MODALITY_INCONSISTENT": "MODALITY",
    "SERIES_DUPLICATE_INSTANCE_NUMBER": "INSTANCE_NUMBER", "SERIES_INSTANCE_GAPS": "INSTANCE_GAPS",
    "SERIES_DUPLICATE_POSITION": "DUPLICATE_POSITION", "SERIES_IRREGULAR_SPACING": "SLICE_SPACING",
    "SERIES_FRAME_CONFLICT": "SPATIAL_ORDER", "SERIES_INVALID_ImagePositionPatient": "SPATIAL_ORDER",
}
for field, assessment in (("Rows", "MATRIX"), ("Columns", "MATRIX_COLUMNS"), ("PixelSpacing", "PIXEL_SPACING"),
                           ("SliceThickness", "SLICE_THICKNESS"), ("ImageOrientationPatient", "ORIENTATION"),
                           ("InstanceNumber", "INSTANCE_NUMBER")):
    SERIES_RULES["SERIES_INVALID_" + field] = assessment
    SERIES_RULES["SERIES_INCONSISTENT_" + field] = assessment


@dataclass(frozen=True, slots=True)
class ReviewState:
    state: str = "待确认"
    updated_at: str = ""
    track_key: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class FileChange:
    relative_path: str
    status: str
    fields: tuple[tuple[str, str, str], ...]
    reason: str


@dataclass(frozen=True, slots=True)
class IssueChange:
    issue_id: str
    code: str
    description: str
    observation: str
    status: str
    reason: str
    baseline_files: tuple[str, ...]
    current_files: tuple[str, ...]
    handling_before: str
    handling_after: str
    suggested_action: str = ""
    action: str = ""
    baseline_track_key: str = ""
    current_track_key: str = ""


@dataclass(frozen=True, slots=True)
class CountChange:
    metric: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    comparison_id: str
    baseline_run: str
    current_run: str
    created_at: str
    version: str
    comparable: bool
    reasons: tuple[str, ...]
    limits: tuple[str, ...]
    file_changes: tuple[FileChange, ...]
    issue_changes: tuple[IssueChange, ...]
    count_changes: tuple[CountChange, ...]


def comparison_id(baseline_run, current_run):
    text = json.dumps([COMPARISON_VERSION, baseline_run, current_run], separators=(",", ":"))
    return "CMP-" + hashlib.sha256(text.encode()).hexdigest()


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.timestamp() if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def source_key(source):
    return str(Path(source).resolve()).casefold()


def path_key(path, source):
    try:
        return Path(path).resolve().relative_to(Path(source).resolve()).as_posix().casefold()
    except ValueError:
        return "outside:" + str(Path(path).resolve()).casefold()


def _records(result):
    records = {}
    for record in result.records:
        key = path_key(record.file_path, result.scan_run.source_root)
        if key in records:
            raise ValueError("路径归一化后冲突，无法建立可靠的文件对比。")
        records[key] = record
    return records


def _evidence(issue):
    # Canonical complete issue evidence, independent of tuple/set ordering.
    return (issue.code, issue.rule_version, issue.issue_type, str(issue.severity),
            tuple(sorted(issue.affected_files, key=str.casefold)), tuple(sorted(issue.study_uids)),
            tuple(sorted(issue.series_uids)), issue.description, issue.impact, issue.recommendation,
            issue.field_name, issue.scope_kind, tuple(sorted(issue.evidence)))


def _proof(issue, baseline, current, old_files, new_files):
    required = {path_key(path, baseline.scan_run.source_root) for path in issue.affected_files}
    old_groups = [series for series in baseline.series if any(item.issue_id == issue.issue_id for item in series.issues)]
    if issue.scope_kind == "series":
        if not old_groups:
            return "基线没有可靠的序列范围。"
        required.update(path_key(path, baseline.scan_run.source_root) for group in old_groups for path in group.files)
    if not required or any(path not in new_files for path in required):
        return "关联文件减少、改名或离开范围；不能以未再发现判断已解决。"
    for path in required:
        previous, record = old_files[path], new_files[path]
        if not record.read_success:
            return "关联文件本次读取失败，相关规则没有可靠证据。"
        # Retain known object identity. Correcting the UID field under test is
        # allowed; arbitrary substitution or regrouping requires confirmation.
        for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
            attribute = FIELD_ATTRIBUTES[keyword]
            value = getattr(previous, attribute)
            if valid_uid(value) and value != getattr(record, attribute) and keyword != issue.field_name:
                return "关联对象标识或归组变化，无法证明原问题已解决。"
    if issue.scope_kind == "series":
        rule = SERIES_RULES.get(issue.code)
        groups = {series.group_key: series for series in current.series}
        if rule is None:
            return "该序列规则没有已验证的复检映射。"
        for previous in old_groups:
            group = groups.get(previous.group_key)
            if group is None or not any(check.code == rule and check.status == "已检查" for check in group.assessments):
                return "关联序列规则本次未执行通过或被跳过。"
        return ""
    if issue.code == "READ_FAILED":
        return ""
    if issue.code == "DUPLICATE_SOP_UID":
        if any(old_files[path].sop_instance_uid != new_files[path].sop_instance_uid for path in required):
            return "实例UID变化使重复组范围改变，需要人工重新确认。"
        return ""  # Absence can only be accepted after full, readable scope.
    if issue.code.startswith("MISSING_"):
        if any(field_requirements(new_files[path]).get(issue.field_name, "not_applicable") == "not_applicable"
               for path in required):
            return "原字段规则本次不适用，不能把跳过解释为修复。"
        if issue.field_name not in FIELD_ATTRIBUTES or any(not getattr(new_files[path], FIELD_ATTRIBUTES[issue.field_name]).strip()
                                                          for path in required):
            return "原字段仍没有可用值，无法确认已解决。"
        if issue.field_name.endswith("UID") and any(not valid_uid(getattr(new_files[path], FIELD_ATTRIBUTES[issue.field_name]))
                                                    for path in required):
            return "原缺失UID已有值，但形式无效，不能确认已解决。"
        from .series import _integer, _numbers, _pixel_spacing, _orientation
        validators = {
            "Rows": lambda r: _integer(r.rows, positive=True) is not None,
            "Columns": lambda r: _integer(r.columns, positive=True) is not None,
            "PixelSpacing": lambda r: _pixel_spacing(r) is not None,
            "SliceThickness": lambda r: _numbers(r.slice_thickness, 1, positive=True) is not None,
            "ImagePositionPatient": lambda r: _numbers(r.image_position_patient, 3) is not None,
            "ImageOrientationPatient": lambda r: _orientation(r.image_orientation_patient) is not None,
            "InstanceNumber": lambda r: _integer(r.instance_number) is not None,
            "RescaleSlope": lambda r: _numbers(r.rescale_slope, 1) is not None,
            "RescaleIntercept": lambda r: _numbers(r.rescale_intercept, 1) is not None,
        }
        if issue.field_name in validators and any(not validators[issue.field_name](new_files[path]) for path in required):
            return "原字段已提供，但数值或几何条件无效，不能把补入无效值视为已解决。"
        return ""
    if issue.code.startswith("INVALID_") and issue.field_name in FIELD_ATTRIBUTES:
        if any(not valid_uid(getattr(new_files[path], FIELD_ATTRIBUTES[issue.field_name])) for path in required):
            return "原UID规则没有取得有效值，不能确认已解决。"
        return ""
    if issue.code == "PROFILE_NOT_EVALUATED":
        return "" if all(supported_profile(new_files[path]) for path in required) else "对象仍未进入所选规则范围。"
    return "该规则没有已验证的复检依据，需人工确认。"


def compare_scans(baseline, current, *, baseline_states=None, current_states=None, allow_state_changes=True):
    """Produce a report and proposed transitions; never mutate snapshots/states."""
    before, after = baseline.scan_run, current.scan_run
    if before is None or after is None:
        raise ValueError("比较需要两个有批次标识的扫描快照。")
    old_files, new_files = _records(baseline), _records(current)
    reasons = []
    if source_key(before.source_root) != source_key(after.source_root):
        reasons.append("来源目录不同，未确认属于同一数据集。")
    if before.rule_version != after.rule_version or any(issue.rule_version != run.rule_version
            for result, run in ((baseline, before), (current, after)) for issue in result.issues):
        reasons.append("规则版本不同或与问题观察不一致。")
    if before.run_id == after.run_id:
        reasons.append("不能将同一批次作为复检。")
    old_time, new_time = stamp(before.finished_at), stamp(after.started_at)
    if old_time is None or new_time is None or new_time < old_time:
        reasons.append("批次时间无效、重叠或复检早于基线。")
    if before.status != "完成" or after.status != "完成":
        reasons.append("基线或当前批次未完成；只可参考已读观察。")
    if current.summary.failure_count or any(not record.read_success for record in current.records):
        reasons.append("当前批次存在读取失败，不能确认完整检查范围。")
    if any(path.startswith("outside:") for path in (*old_files, *new_files)):
        reasons.append("快照包含来源目录外的文件，范围不可可靠对齐。")
    if any(check.code == "SCAN_INCOMPLETE" for record in current.records for check in record.series_checks):
        reasons.append("当前快照含未完成规则执行标记。")
    comparable = not reasons
    changes, changed_paths = [], set()
    for path in sorted(old_files.keys() | new_files.keys()):
        old, new = old_files.get(path), new_files.get(path)
        if old is None or new is None:
            changes.append(FileChange(path, "新增文件" if old is None else "减少文件", (), "仅表示相对路径在两份已读快照中的差异。"))
        else:
            fields = tuple((name, str(getattr(old, name)), str(getattr(new, name))) for name in METADATA_FIELDS
                           if getattr(old, name) != getattr(new, name))
            if fields:
                changed_paths.add(path)
                changes.append(FileChange(path, "读取状态变化" if old.read_success != new.read_success else "所选元数据变化",
                                          fields, "不包含像素或未读取字段的变化检测。"))
    previous = {issue.issue_id: issue for issue in baseline.issues}
    present = {issue.issue_id: issue for issue in current.issues}
    baseline_states, current_states = baseline_states or {}, current_states or {}
    rows = []
    for key in sorted(previous.keys() | present.keys()):
        old, new = previous.get(key), present.get(key)
        issue = new or old
        old_state = baseline_states.get(key, ReviewState())
        new_state = current_states.get(key, ReviewState()) if new else old_state
        state = new_state.state
        action = ""
        if not comparable:
            observation, status, reason = "无法确认", "无法确认", "；".join(reasons)
        elif old and new:
            affected = {path_key(path, after.source_root) for path in (*old.affected_files, *new.affected_files)}
            evidence_changed = _evidence(old) != _evidence(new) or bool(affected & changed_paths)
            observation = "仍然存在"
            if evidence_changed:
                status, reason = "需重新确认", "问题证据、关联范围或所选元数据变化；不能沿用原确认或忽略结论。"
                if state != "待确认":
                    action = "重开" if state == "已关闭" else "重新确认"
            else:
                status, reason = "已忽略" if state == "可忽略" else "仍然存在", "同一稳定问题键及所选证据再次被观察到。"
                if state == "已关闭":
                    action, status, reason = "重开", "再次出现", "相同证据的已关闭问题再次出现，恢复待确认并保留审计。"
        elif new:
            observation, status, reason = "新出现", "新出现", "完整可比批次中新增问题观察。"
            if state == "已关闭":
                action, status, reason = "重开", "再次出现", "该同证据轨迹有已关闭记录，本次重新观察到。"
            elif state == "可忽略":
                status = "已忽略"
        else:
            proof = _proof(old, baseline, current, old_files, new_files)
            if proof:
                observation, status, reason = "无法确认", "无法确认", proof
            else:
                observation, status = "已解决", "已忽略" if state == "可忽略" else "已解决"
                reason = "关联文件仍在且可读，原所选规则本次已执行，不再报告该问题。"
                if state == "已处理待复检":
                    reviewed = stamp(old_state.updated_at)
                    if reviewed is not None and reviewed <= new_time:
                        action = "关闭"
                    else:
                        reason += " 人工处理记录晚于复检或时间未知，不能自动关闭。"
        if not allow_state_changes:
            action = ""
        rows.append(IssueChange(key, issue.code, issue.description, observation, status, reason,
            old.affected_files if old else (), new.affected_files if new else (), state, state,
            action, "", old_state.track_key if old else "", new_state.track_key if new else ""))
    counts = tuple(CountChange(name, getattr(baseline.summary, name), getattr(current.summary, name))
                   for name in ("file_count", "success_count", "failure_count", "study_count", "series_count",
                                "unique_instance_count", "issue_count", "error_count", "warning_count", "info_count"))
    limits = LIMITS + (() if allow_state_changes else ("当前批次不是最新或人工状态晚于扫描；此历史对比不变更当前处理状态。",))
    return ComparisonReport(comparison_id(before.run_id, after.run_id), before.run_id, after.run_id,
        datetime.now(timezone.utc).isoformat(), COMPARISON_VERSION, comparable, tuple(reasons), limits,
        tuple(changes), tuple(rows), counts)
