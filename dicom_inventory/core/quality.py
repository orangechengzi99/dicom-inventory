"""Small, explicit metadata profiles; not a full DICOM IOD validator."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from pydicom.uid import CTImageStorage, MRImageStorage, MediaStorageDirectoryStorage

from .models import InventoryRecord, QualityIssue, Severity
from .cancellation import checked

RULE_VERSION = "metadata-1.2"
FIELD_ATTRIBUTES = {
    "SOPClassUID": "sop_class_uid", "Modality": "modality",
    "StudyInstanceUID": "study_instance_uid", "SeriesInstanceUID": "series_instance_uid",
    "SOPInstanceUID": "sop_instance_uid", "Rows": "rows", "Columns": "columns",
    "PixelSpacing": "pixel_spacing", "SliceThickness": "slice_thickness",
    "ImagePositionPatient": "image_position_patient",
    "ImageOrientationPatient": "image_orientation_patient", "InstanceNumber": "instance_number",
    "RescaleSlope": "rescale_slope", "RescaleIntercept": "rescale_intercept",
    "NumberOfFrames": "number_of_frames",
    "FrameOfReferenceUID": "frame_of_reference_uid", "ImageType": "image_type",
}


def valid_uid(value: str) -> bool:
    """PS3.5 9.1 syntax only, not registration/uniqueness certification."""
    return 0 < len(value) <= 64 and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*", value) is not None


def supported_profile(record: InventoryRecord) -> str | None:
    expected = {str(CTImageStorage): "CT", str(MRImageStorage): "MR"}.get(record.sop_class_uid)
    if expected is None or record.modality != expected:
        return None
    # A classic single-frame IOD normally has no NumberOfFrames. Malformed values
    # must not accidentally enter the single-frame profile.
    if record.number_of_frames not in ("", "1"):
        return None
    return expected


def field_requirements(record: InventoryRecord) -> dict[str, str]:
    """Tool policy for selected fields: required / recommended / not_applicable.

    Required here is not a claim that all DICOM Type 1/2/conditional attributes
    are validated. In particular SliceThickness/InstanceNumber may be empty.
    """
    requirements = {name: "not_applicable" for name in FIELD_ATTRIBUTES}
    if not record.sop_class_uid and record.media_storage_sop_class_uid == str(MediaStorageDirectoryStorage):
        return requirements  # DICOMDIR is not an image Storage SOP instance.
    for name in ("SOPClassUID", "SOPInstanceUID"):
        requirements[name] = "required"
    profile = supported_profile(record)
    if profile:
        for name in ("Modality", "StudyInstanceUID", "SeriesInstanceUID", "Rows", "Columns",
                     "PixelSpacing", "ImagePositionPatient", "ImageOrientationPatient"):
            requirements[name] = "required"
        for name in ("SliceThickness", "InstanceNumber"):
            requirements[name] = "recommended"
        if profile == "CT":
            requirements["RescaleSlope"] = requirements["RescaleIntercept"] = "required"
    return requirements


def stable_issue_id(code: str, scope: tuple[str, ...]) -> str:
    """Deterministic across ordering/runs; explicitly versioned, no random IDs."""
    value = json.dumps([RULE_VERSION, code, *scope], ensure_ascii=False, separators=(",", ":"))
    return "QI-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_scope(path: str, source_root: str | Path | None) -> tuple[str, ...]:
    resolved = Path(path).resolve()
    if source_root is not None:
        try:
            value = str(resolved.relative_to(Path(source_root).resolve()))
        except ValueError:
            value = str(resolved)
    else:
        value = str(resolved)
    return ("file", os.path.normcase(value).replace("\\", "/"))


def _issue(code: str, kind: str, level: Severity, records: list[InventoryRecord],
           scope: tuple[str, ...], description: str, impact: str, advice: str,
           field: str = "", evidence: tuple[tuple[str, str], ...] = ()) -> QualityIssue:
    return QualityIssue(
        stable_issue_id(code, scope), code, RULE_VERSION, kind, level,
        tuple(sorted({item.file_path for item in records}, key=str.casefold)),
        tuple(sorted({item.study_instance_uid for item in records if item.study_instance_uid})),
        tuple(sorted({item.series_instance_uid for item in records if item.series_instance_uid})),
        description, impact, advice, field, scope[0], evidence,
    )


def evaluate_record(record: InventoryRecord, source_root: str | Path | None = None) -> tuple[QualityIssue, ...]:
    scope = _file_scope(record.file_path, source_root)
    record.missing_tags = ()
    record.series_checks = ()
    record.slice_sort_position = None
    if not record.read_success:
        record.assessment_status = "读取失败"
        record.issues = (_issue("READ_FAILED", "读取失败", Severity.ERROR, [record], scope,
                               "无法解析此候选文件的DICOM元数据。", "该文件不能参与可靠的元数据归组和检查。",
                               "核对读取权限及文件格式，联系数据提供方取得可读副本；不要直接删除原文件。",
                               evidence=(("observation", "所选元数据解析失败"),)),)
        return record.issues
    supported = supported_profile(record) is not None
    record.assessment_status = "已检查所选字段" if supported else "未评估"
    issues: list[QualityIssue] = []
    missing: list[str] = []
    for keyword, requirement in field_requirements(record).items():
        if requirement == "not_applicable" or getattr(record, FIELD_ATTRIBUTES[keyword]).strip():
            continue
        missing.append(keyword)
        if requirement == "recommended":
            level = Severity.INFO
            impact = "可能缺少学习或排序参照；空值本身不证明文件不符合DICOM标准。"
        elif keyword in ("PixelSpacing", "ImagePositionPatient", "ImageOrientationPatient"):
            level = Severity.WARNING
            impact = "可能影响空间定位、尺寸测量或后续三维重建；缺失时不能完成相关序列几何检查。"
        else:
            level = Severity.ERROR
            impact = "缺少所选关键标识、矩阵或CT变换参数，无法可靠完成相关数据解释。"
        issues.append(_issue("MISSING_" + keyword, "标签缺失", level, [record], scope,
                             f"所选{('建议' if requirement == 'recommended' else '关键')}字段 {keyword} 无可用值。",
                             impact, "向数据提供方核对原始记录及导出设置；本工具不填补或修复标签。", keyword,
                             (("observation", "无可用值"), ("requirement", "建议参照" if requirement == "recommended" else "关键用途字段"))))
    if not supported:
        issues.append(_issue("PROFILE_NOT_EVALUATED", "未评估", Severity.INFO, [record], scope,
                             "对象未进入传统单帧CT/MR所选字段检查范围。",
                             "SR、PDF、波形、多帧、其他SOP类或分类信息不匹配均未进行影像字段完整性评估；不代表通过。",
                             "确认SOP类、模态和帧数，使用适合该对象的检查工具。",
                             evidence=(("observation", "对象分类条件未匹配传统单帧CT/MR范围"),)))
    record.missing_tags = tuple(missing)
    for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "SOPClassUID", "FrameOfReferenceUID"):
        value = getattr(record, FIELD_ATTRIBUTES[keyword])
        if value and not valid_uid(value):
            issues.append(_issue("INVALID_" + keyword, "UID格式", Severity.ERROR, [record], scope,
                f"{keyword} 不符合所选UID形式检查。", "不能作为可靠的归组或空间参照标识；不检查注册归属。",
                "向数据提供方核对导出；不要自行重生成UID。", keyword,
                (("constraint", "ASCII数字与点分段、无多余前导零、长度不超过64"),)))
    record.issues = tuple(issues)
    return record.issues


def evaluate_quality(records: list[InventoryRecord], source_root: str | Path | None = None, *,
                     cancel_check=None, include_series=True) -> tuple[QualityIssue, ...]:
    """Independently callable; resets old observations instead of accumulating."""
    issues: list[QualityIssue] = []
    grouped: dict[str, list[InventoryRecord]] = defaultdict(list)
    for record in checked(records, cancel_check, "检查质量"):
        record.is_duplicate = False
        issues.extend(evaluate_record(record, source_root))
        if record.read_success and record.sop_instance_uid.strip():
            grouped[record.sop_instance_uid].append(record)
    for uid, members in checked(sorted(grouped.items()), cancel_check, "检查质量"):
        if len(members) < 2:
            continue
        issue = _issue("DUPLICATE_SOP_UID", "SOPInstanceUID重复", Severity.WARNING, members,
                       ("sop_group", uid), f"同一SOPInstanceUID对应 {len(members)} 个文件。",
                       "可能是重复副本，也可能存在元数据或像素冲突；本版不比较文件内容。",
                       "核对全部关联文件及数据来源；UID相同不能作为删除文件的依据。", "SOPInstanceUID",
                       (("affected_count", str(len(members))), ("values", uid)))
        issues.append(issue)
        for record in checked(members, cancel_check, "检查质量"):
            record.is_duplicate = True
            record.issues = (*record.issues, issue)
    from .series import evaluate_series
    if include_series:
        issues.extend(evaluate_series(records, source_root, cancel_check=cancel_check))
    return tuple(sorted(issues, key=lambda item: (-item.severity.rank, item.code, item.issue_id)))
