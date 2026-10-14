"""Strict version-1 metadata codec. No Qt, DICOM reads, pickle or dynamic classes."""
from dataclasses import fields, is_dataclass
from enum import StrEnum
import json
import math
from types import UnionType
from typing import get_args, get_origin, get_type_hints
from .core.models import (InventoryRecord, InventorySummary, QualityIssue, RuleAssessment,
                          StudySummary, SeriesSummary)
from .core.comparison import ComparisonReport, IssueChange, FileChange, CountChange

# Explicit allowlists: new model fields require a reviewed payload migration.
SCHEMA = {
    ComparisonReport: "comparison_id baseline_run current_run created_at version comparable reasons limits file_changes issue_changes count_changes",
    IssueChange: "issue_id code description observation status reason baseline_files current_files handling_before handling_after suggested_action action baseline_track_key current_track_key",
    FileChange: "relative_path status fields reason",
    CountChange: "metric before after",
    QualityIssue: "issue_id code rule_version issue_type severity affected_files study_uids series_uids description impact recommendation field_name scope_kind evidence",
    RuleAssessment: "code status reason",
    InventorySummary: "file_count success_count failure_count study_count series_count unique_instance_count duplicate_instance_count anomaly_file_count issue_count error_count warning_count info_count unevaluated_count",
    InventoryRecord: "file_path modality study_instance_uid series_instance_uid sop_instance_uid rows columns pixel_spacing slice_thickness image_position_patient image_orientation_patient instance_number rescale_slope rescale_intercept read_success missing_tags is_duplicate error_message sop_class_uid media_storage_sop_class_uid number_of_frames assessment_status issues frame_of_reference_uid image_type series_checks slice_sort_position",
    StudySummary: "group_key study_instance_uid series_keys files modalities issues overall_status highest_severity",
    SeriesSummary: "group_key series_instance_uid study_uids files modalities rows columns pixel_spacings slice_thicknesses orientations spatial_range sorted_positions issues assessments overall_status highest_severity frame_of_reference_uids image_types",
}
SCHEMA = {kind: tuple(names.split()) for kind,names in SCHEMA.items()}
HINTS = {kind: get_type_hints(kind) for kind in SCHEMA}


def _encode(value):
    if is_dataclass(value):
        kind = type(value)
        if kind not in SCHEMA or {f.name for f in fields(kind)} != set(SCHEMA[kind]):
            raise ValueError("模型字段与保存格式不兼容。")
        return {name: [issue.issue_id for issue in getattr(value,name)] if name == "issues"
                else _encode(getattr(value,name)) for name in SCHEMA[kind]}
    if isinstance(value,StrEnum):
        return str(value)
    if isinstance(value,(tuple,list)):
        return [_encode(item) for item in value]
    if isinstance(value,dict):
        if not all(isinstance(key,str) for key in value):
            raise ValueError("保存键必须为字符串。")
        return {key:_encode(item) for key,item in value.items()}
    if value is None or type(value) in (str,int,bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("保存数据含不支持的类型或非有限数值。")


def dumps(value):
    return json.dumps(_encode(value),ensure_ascii=False,allow_nan=False,separators=(",",":"))


def _object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("保存数据含重复字段。")
        result[key] = value
    return result


def loads(text):
    return json.loads(text,object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非有限数值。")))


def decode(value,kind,issues=None):
    origin,args = get_origin(kind),get_args(kind)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return decode(value,next(item for item in args if item is not type(None)),issues)
    if origin is tuple:
        if not isinstance(value,list):
            raise ValueError("保存列表格式无效。")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(item,args[0],issues) for item in value)
        if len(value) != len(args):
            raise ValueError("保存列表长度无效。")
        return tuple(decode(item,hint,issues) for item,hint in zip(value,args))
    if origin is list:
        if not isinstance(value,list):
            raise ValueError("保存列表格式无效。")
        return [decode(item,args[0],issues) for item in value]
    if origin is dict:
        if not isinstance(value,dict):
            raise ValueError("保存映射格式无效。")
        return {decode(key,args[0],issues):decode(item,args[1],issues) for key,item in value.items()}
    if kind in SCHEMA:
        if not isinstance(value,dict) or set(value) != set(SCHEMA[kind]):
            raise ValueError("保存字段不完整或包含未允许字段。")
        data = {}
        for name in SCHEMA[kind]:
            if name == "issues":
                refs = decode(value[name],tuple[str,...])
                if issues is None or any(key not in issues for key in refs):
                    raise ValueError("问题关联不完整。")
                data[name] = tuple(issues[key] for key in refs)
            else:
                data[name] = decode(value[name],HINTS[kind][name],issues)
        return kind(**data)
    if isinstance(kind,type) and issubclass(kind,StrEnum):
        if not isinstance(value,str):
            raise ValueError("保存级别格式无效。")
        return kind(value)
    if kind is float:
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError("保存数值无效。")
        return float(value)
    if type(value) is not kind:
        raise ValueError("保存字段类型无效。")
    return value
