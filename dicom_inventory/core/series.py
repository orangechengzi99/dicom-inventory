"""GUI-independent grouping and conservative classic single-frame checks."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from itertools import product
from pathlib import Path
from statistics import median

from .models import InventoryRecord, QualityIssue, RuleAssessment, Severity, StudySummary, SeriesSummary
from .quality import FIELD_ATTRIBUTES, _file_scope, _issue, supported_profile, valid_uid
from .cancellation import checkpoint, checked

VALUE_ABS_TOL = 0.001  # mm for spacing/thickness comparison
VALUE_REL_TOL = 0.001
ORIENTATION_TOL = 0.0001  # component, unit-length and orthogonality tolerance
POSITION_TOL_MM = 0.01
GAP_ABS_TOL_MM = 0.1
GAP_REL_TOL = 0.05
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _numbers(value: str, count: int, *, positive: bool = False) -> tuple[float, ...] | None:
    parts = value.split("\\")
    if len(parts) != count or any(_NUMBER.fullmatch(part.strip()) is None for part in parts):
        return None
    try:
        values = tuple(float(part) for part in parts)
    except (ValueError, OverflowError):
        return None
    # Tool numeric safety bound, not a DICOM range requirement. Avoid overflow
    # in projection/grid math from extreme finite malformed coordinates.
    return values if all(math.isfinite(item) and abs(item) <= 1e12 and (not positive or item > 0) for item in values) else None


def _integer(value: str, *, positive: bool = False) -> int | None:
    if not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if not positive or number > 0 else None


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _preview(values, limit=12):
    items = list(values)
    return " | ".join(str(item) for item in items[:limit]) + (f"（另{len(items)-limit}个；完整值逐文件查看）" if len(items) > limit else "")


def _pixel_spacing(record):
    values = _numbers(record.pixel_spacing, 2)
    dimensions = (_integer(record.rows, positive=True), _integer(record.columns, positive=True))
    return values if values is not None and all(value > 0 or value == 0 and dimension == 1
                                                for value, dimension in zip(values, dimensions)) else None


def _orientation(value: str) -> tuple[float, ...] | None:
    values = _numbers(value, 6)
    if values is None:
        return None
    row, col = values[:3], values[3:]
    if any(abs(math.sqrt(_dot(vec, vec)) - 1) > ORIENTATION_TOL for vec in (row, col)):
        return None
    return values if abs(_dot(row, col)) <= ORIENTATION_TOL else None


def _different(values: list[tuple[float, ...]], *, orientation: bool = False) -> bool:
    for components in zip(*values):
        low, high = min(components), max(components)
        tolerance = ORIENTATION_TOL if orientation else max(VALUE_ABS_TOL, VALUE_REL_TOL * max(abs(low), abs(high)))
        if high - low > tolerance + 1e-12:
            return True
    return False


def group_key(record: InventoryRecord, kind: str, source_root=None) -> str:
    uid = getattr(record, kind + "_instance_uid")
    return kind + ":" + uid if record.read_success and valid_uid(uid) else kind + ":unassigned:" + _file_scope(record.file_path, source_root)[1]


def _groups(records, kind, source_root=None, cancel_check=None, stage="序列归组"):
    groups = defaultdict(list)
    for record in checked(records, cancel_check, stage):
        groups[group_key(record, kind, source_root)].append(record)
    return dict(sorted(groups.items()))


def _duplicate_positions(members, positions, cancel_check=None) -> list[InventoryRecord]:
    """Fixed-radius grid lookup: no all-pairs search for typical large series."""
    cells = defaultdict(list)
    occurrences = defaultdict(int)
    for point in checked(positions, cancel_check, "检查空间位置"):
        occurrences[point] += 1
    affected = {point for point, count in occurrences.items() if count > 1}
    for point in checked(occurrences, cancel_check, "检查空间位置"):
        cell = tuple(math.floor(value / POSITION_TOL_MM) for value in point)
        for shift in product((-1, 0, 1), repeat=3):
            neighbour = tuple(a+b for a, b in zip(cell, shift))
            for other in checked(cells[neighbour], cancel_check, "检查空间位置"):
                if math.dist(point, other) <= POSITION_TOL_MM + 1e-12:
                    affected.update((point, other))
        cells[cell].append(point)
    return [member for member, point in zip(members, positions) if point in affected]


def evaluate_series(records: list[InventoryRecord], source_root: str | Path | None = None, *, cancel_check=None) -> tuple[QualityIssue, ...]:
    """Re-evaluate series observations, preserving file/SOP observations.

    Missing/unsupported/insufficient evidence is retained in RuleAssessment,
    not represented as a successful test or a flood of image-tag alerts.
    """
    issues = []
    for record in checked(records, cancel_check, "检查序列"):
        record.issues = tuple(issue for issue in record.issues if issue.scope_kind != "series")
        record.series_checks = ()
        record.slice_sort_position = None
    for key, unsorted_members in checked(_groups(records, "series", source_root, cancel_check).items(), cancel_check, "检查序列"):
        members = sorted(unsorted_members, key=lambda item: item.file_path.casefold())
        checks = []

        def assessment(code, status, reason=""):
            checkpoint(cancel_check, "检查序列")
            checks.append(RuleAssessment(code, status, reason))

        def add(code, kind, severity, description, impact, advice, affected=None, field="", evidence=()):
            if not evidence:
                attribute = FIELD_ATTRIBUTES.get(field)
                subset = members if affected is None else affected
                evidence = (("affected_count", str(len(subset))),)
                if attribute:
                    evidence += (("values", _preview(sorted({getattr(member, attribute) for member in subset}))),)
            issue = _issue(code, kind, severity, members if affected is None else affected,
                           ("series", key), description, impact, advice, field, evidence)
            issues.append(issue)
            for record in checked(members if affected is None else affected, cancel_check, "检查序列"):
                record.issues = (*record.issues, issue)
            return issue

        valid_series = all(item.read_success and valid_uid(item.series_instance_uid) for item in members)
        studies = {item.study_instance_uid for item in members if valid_uid(item.study_instance_uid)}
        valid_study = all(valid_uid(item.study_instance_uid) for item in members) and len(studies) == 1
        if valid_series and len(studies) > 1:
            add("SERIES_STUDY_CONFLICT", "归组冲突", Severity.ERROR,
                "同一SeriesInstanceUID关联多个有效StudyInstanceUID。", "序列归属相互矛盾，无法可靠归组。",
                "核对全部关联检查的导出和来源；不自动改写UID。", field="StudyInstanceUID",
                evidence=(("StudyUIDs", " | ".join(sorted(studies))),))
        assessment("STUDY_ASSOCIATION", "发现问题" if valid_series and len(studies) > 1 else "已检查" if valid_series and valid_study else "未评估",
                   "归属标识无效、缺失或冲突" if not valid_series or not valid_study else "")
        modalities = {item.modality for item in members if item.modality}
        modality_problem = valid_series and len(modalities) > 1
        if modality_problem:
            add("SERIES_MODALITY_INCONSISTENT", "序列一致性", Severity.WARNING,
                "同一序列的Modality存在不同值。", "可能混入不同模态，需先核对归组再解释影像。",
                "核对序列来源与SOP类，不能仅据模态判断应移走哪些文件。", field="Modality",
                evidence=(("values", " | ".join(sorted(modalities))),))
        assessment("MODALITY", "发现问题" if modality_problem else "已检查" if valid_series and all(item.modality for item in members) else "未评估", "仅比较提供的模态值")
        supported = valid_series and valid_study and all(supported_profile(item) for item in members)
        unsupported_reason = "归属无效/冲突，或包含未支持的SOP类、模态、帧数；不进行图像序列推断。"
        if not supported:
            for code in ("MATRIX", "PIXEL_SPACING", "SLICE_THICKNESS", "ORIENTATION", "INSTANCE_NUMBER", "INSTANCE_GAPS", "SPATIAL_ORDER", "DUPLICATE_POSITION", "SLICE_SPACING"):
                assessment(code, "未评估", unsupported_reason)
            for record in members:
                record.series_checks = tuple(checks)
            continue

        fields = (
            ("MATRIX", "rows", "Rows", lambda value: (_integer(value, positive=True),), False),
            ("MATRIX_COLUMNS", "columns", "Columns", lambda value: (_integer(value, positive=True),), False),
            ("PIXEL_SPACING", "pixel_spacing", "PixelSpacing", lambda value: _numbers(value, 2, positive=True), False),
            ("SLICE_THICKNESS", "slice_thickness", "SliceThickness", lambda value: _numbers(value, 1, positive=True), False),
            ("ORIENTATION", "image_orientation_patient", "ImageOrientationPatient", _orientation, True),
        )
        parsed_fields = {}
        for code, attribute, keyword, parser, orientation in fields:
            parsed = [_pixel_spacing(item) if keyword == "PixelSpacing" else parser(getattr(item, attribute))
                      for item in checked(members, cancel_check, "检查序列")]
            invalid = [item for item, value in zip(members, parsed) if getattr(item, attribute) and (value is None or None in value)]
            if invalid:
                add("SERIES_INVALID_" + keyword, "字段值无效", Severity.ERROR if keyword in ("Rows", "Columns") else Severity.WARNING,
                    f"{keyword} 的数值、分量数或几何约束不满足所选规则。",
                    "无法可靠完成相关比较或空间解释；这不是完整VR/IOD验证。",
                    "向数据提供方核对原始标签，不自动填值。", affected=invalid, field=keyword)
            valid = all(value is not None and None not in value for value in parsed)
            parsed_fields[keyword] = parsed if valid else None
            inconsistent = valid and (_different(parsed, orientation=orientation) if keyword not in ("Rows", "Columns") else len(set(parsed)) > 1)
            if inconsistent:
                add("SERIES_INCONSISTENT_" + keyword, "序列一致性", Severity.WARNING,
                    f"同一序列的{keyword}超出所选一致性容差。", "可能是合法变化、混合采集或导出问题；可能影响统一解释及重建。",
                    "结合采集协议和全部关联文件核对；不自动重分组。", field=keyword,
                    evidence=(("values", _preview(sorted({getattr(item, attribute) for item in members}))),
                              ("tolerance", "精确比较" if keyword in ("Rows", "Columns") else "方向分量0.0001" if orientation else "max(0.001mm,0.1%)")))
            assessment(code, "未评估" if not valid else "发现问题" if inconsistent else "已检查", "缺失或无效数值" if not valid else "")

        numbers = [_integer(item.instance_number) for item in checked(members, cancel_check, "检查序列")]
        all_numbers = all(number is not None for number in numbers)
        invalid_numbers = [item for item, number in zip(members, numbers) if item.instance_number and number is None]
        if invalid_numbers:
            add("SERIES_INVALID_InstanceNumber", "字段值无效", Severity.WARNING,
                "InstanceNumber存在非整数值。", "无法执行所选序号重复及缺号检查。", "核对原导出序号；不用文件名代替。",
                affected=invalid_numbers, field="InstanceNumber")
        repeats = defaultdict(list)
        for item, number in checked(zip(members, numbers), cancel_check, "检查序列"):
            if number is not None:
                repeats[number].append(item)
        duplicate_members = [item for group in repeats.values() if len(group) > 1 for item in group]
        if duplicate_members:
            add("SERIES_DUPLICATE_INSTANCE_NUMBER", "序号重复", Severity.WARNING,
                "同一序列中多个文件使用相同InstanceNumber。", "序号排序存在歧义；不等同于重复像素或可删除副本。",
                "结合空间位置及采集维度人工核对。", affected=duplicate_members, field="InstanceNumber",
                evidence=(("numbers", _preview(number for number, group in sorted(repeats.items()) if len(group) > 1)),))
        assessment("INSTANCE_NUMBER", "发现问题" if duplicate_members else "已检查" if all_numbers else "未评估", "缺失或无效序号" if not all_numbers else "")
        gaps = []
        if all_numbers:
            ordered = sorted(set(numbers))
            gaps = [(a + 1, b - 1) for a, b in zip(ordered, ordered[1:]) if b-a > 1]
            if gaps:
                add("SERIES_INSTANCE_GAPS", "序号缺号线索", Severity.INFO,
                    "序列已提供的整数序号之间存在未出现区间。", "标准不保证序号连续；可能为选取、编号习惯或不同采集维度，不能证明缺片。",
                    "核对原始导出范围及采集协议，不自动补号。", field="InstanceNumber",
                    evidence=(("missing_ranges", _preview(f"{a}..{b}" for a, b in gaps)), ("range_count", str(len(gaps))),))
        assessment("INSTANCE_GAPS", "发现问题" if gaps else "已检查" if all_numbers and len(members) >= 2 else "未评估", "缺失/无效序号或不足两文件" if not all_numbers or len(members) < 2 else "连续仅是本次观察")

        orientations = parsed_fields["ImageOrientationPatient"]
        positions = [_numbers(item.image_position_patient, 3) for item in checked(members, cancel_check, "检查空间位置")]
        invalid_positions = [item for item, value in zip(members, positions) if item.image_position_patient and value is None]
        if invalid_positions:
            add("SERIES_INVALID_ImagePositionPatient", "字段值无效", Severity.WARNING,
                "ImagePositionPatient不是三个有限数值。", "不能进行可靠空间投影和重复位置检查。", "核对原始坐标，不以序号代替。",
                affected=invalid_positions, field="ImagePositionPatient")
        frames = {item.frame_of_reference_uid for item in members}
        geometry_reason = ""
        if any(not valid_uid(item.frame_of_reference_uid) for item in members) or len(frames) != 1:
            geometry_reason = "FrameOfReferenceUID缺失/无效/不一致，不能假定同一坐标系。"
            if all(valid_uid(item.frame_of_reference_uid) for item in members) and len(frames) > 1:
                add("SERIES_FRAME_CONFLICT", "空间参照冲突", Severity.WARNING, "同一序列提供多个空间参照UID。",
                    "位置数值可能不在同一坐标系，已跳过空间比较。", "先核对空间参照和采集来源。", field="FrameOfReferenceUID")
        elif orientations is None or any(value is None for value in positions):
            geometry_reason = "方向/位置缺失或无效。"
        elif _different(orientations, orientation=True):
            geometry_reason = "方向不一致，不能作为同一平行切片栈排序。"
        elif any(set(item.image_type.upper().split("\\")) & {"LOCALIZER", "SCOUT"} for item in members):
            geometry_reason = "包含显式定位像，不按常规切片栈推断。"
        if geometry_reason:
            for code in ("SPATIAL_ORDER", "DUPLICATE_POSITION", "SLICE_SPACING"):
                assessment(code, "未评估", geometry_reason)
        else:
            row, col = orientations[0][:3], orientations[0][3:]
            normal = (row[1]*col[2]-row[2]*col[1], row[2]*col[0]-row[0]*col[2], row[0]*col[1]-row[1]*col[0])
            length = math.sqrt(_dot(normal, normal))
            normal = tuple(value / length for value in normal)
            for item, position in checked(zip(members, positions), cancel_check, "检查空间位置"):
                item.slice_sort_position = _dot(position, normal)
            assessment("SPATIAL_ORDER", "已检查", "位置投影到统一方向法向；不使用InstanceNumber决定空间顺序。")
            duplicates = _duplicate_positions(members, positions, cancel_check)
            if duplicates:
                add("SERIES_DUPLICATE_POSITION", "空间位置重复", Severity.WARNING,
                    "同一空间参照下存在距离不超过0.01mm的位置。", "可能为重复副本或不同时间/回波，不能直接认定损坏或可删除。",
                    "结合采集维度核对；本版未读取时间点/回波等区分信息。", affected=duplicates, field="ImagePositionPatient")
            assessment("DUPLICATE_POSITION", "发现问题" if duplicates else "已检查" if len(members) >= 2 else "未评估", "不足两文件" if len(members) < 2 else "三维欧氏距离容差0.01mm")
            projected = sorted(item.slice_sort_position for item in members)
            distances = [b-a for a, b in zip(projected, projected[1:])]
            spacing_reason = "不足三个文件" if len(members) < 3 else "存在重叠/近重叠平面，不能评估单栈间距" if min(distances) <= POSITION_TOL_MM else ""
            irregular = False
            if not spacing_reason:
                central = median(distances)
                tolerance = max(GAP_ABS_TOL_MM, GAP_REL_TOL * central)
                irregular = any(abs(gap-central) > tolerance + 1e-12 for gap in distances)
                if irregular:
                    add("SERIES_IRREGULAR_SPACING", "层间距核对", Severity.WARNING,
                        "相邻投影位置间距偏离中位间距，超过所选容差。", "可能影响按均匀切片栈使用；合法可变间距也会触发，不证明缺片。",
                        "核对定位像、时间/回波和采集协议，不用SliceThickness代替中心间距。", field="ImagePositionPatient",
                        evidence=(("adjacent_mm", _preview(f"{gap:.8g}" for gap in distances)), ("interval_count", str(len(distances))),
                                  ("median_mm", f"{central:.8g}"), ("tolerance_mm", f"{tolerance:.8g}")))
            assessment("SLICE_SPACING", "未评估" if spacing_reason else "发现问题" if irregular else "已检查", spacing_reason or "max(0.1mm,中位间距5%)")
        for record in checked(members, cancel_check, "检查序列"):
            record.series_checks = tuple(checks)
    return tuple(sorted(issues, key=lambda item: (-item.severity.rank, item.code, item.issue_id)))


def _unique_issues(members, cancel_check=None):
    by_id = {issue.issue_id: issue for member in checked(members, cancel_check, "汇总分层") for issue in member.issues}
    return tuple(sorted(by_id.values(), key=lambda item: (-item.severity.rank, item.code, item.issue_id)))


def _values(members, attribute):
    # Empty is retained to avoid concealing mixed missing/present metadata.
    return tuple(sorted({getattr(member, attribute) for member in members}))


def _overall(members, issues):
    highest = max((issue.severity for issue in issues), key=lambda item: item.rank, default=None)
    partial = any(not member.read_success or member.assessment_status == "未评估" or
                  any(check.status == "未评估" for check in member.series_checks) for member in members)
    status = {Severity.ERROR: "错误", Severity.WARNING: "需核对", Severity.INFO: "提示"}.get(highest, "已检查所选规则")
    return status + (" · 部分未评估" if partial else ""), highest


def summarize_hierarchy(records: list[InventoryRecord], source_root=None, *, cancel_check=None) -> tuple[tuple[StudySummary, ...], tuple[SeriesSummary, ...]]:
    """Aggregate already-evaluated observations without choosing arbitrary values."""
    series = []
    for key, members in checked(_groups(records, "series", source_root, cancel_check, "汇总分层").items(), cancel_check, "汇总分层"):
        issues = _unique_issues(members, cancel_check)
        status, severity = _overall(members, issues)
        positions = tuple(sorted(((member.file_path, member.slice_sort_position) for member in members
                                  if member.slice_sort_position is not None), key=lambda item: (item[1], item[0].casefold())))
        spatial_range = (positions[0][1], positions[-1][1]) if positions and len(positions) == len(members) else None
        series.append(SeriesSummary(key, members[0].series_instance_uid,
            _values(members, "study_instance_uid"), tuple(sorted(member.file_path for member in members)),
            _values(members, "modality"), _values(members, "rows"), _values(members, "columns"),
            _values(members, "pixel_spacing"), _values(members, "slice_thickness"), _values(members, "image_orientation_patient"),
            spatial_range, positions, issues, members[0].series_checks, status, severity,
            _values(members, "frame_of_reference_uid"), _values(members, "image_type")))
    studies = []
    for key, members in checked(_groups(records, "study", source_root, cancel_check, "汇总分层").items(), cancel_check, "汇总分层"):
        issues = _unique_issues(members, cancel_check)
        status, severity = _overall(members, issues)
        studies.append(StudySummary(key, members[0].study_instance_uid,
            tuple(sorted({group_key(member, "series", source_root) for member in members})),
            tuple(sorted(member.file_path for member in members)), _values(members, "modality"), issues, status, severity))
    return tuple(studies), tuple(series)
