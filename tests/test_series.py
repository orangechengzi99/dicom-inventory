"""Synthetic metadata and dynamically generated anonymous series only."""
from __future__ import annotations
import csv
import json

import pytest
from pydicom.uid import CTImageStorage, MRImageStorage, EnhancedCTImageStorage, BasicTextSRStorage, generate_uid
from dicom_inventory.core import (InventoryRecord, Severity, evaluate_quality, evaluate_series,
    inventory_files, export_series_csv, summarize_hierarchy)
from dicom_inventory.core.quality import valid_uid


def synthetic_records(tmp_path, count=4):
    return [InventoryRecord(str(tmp_path / f"{i:02}.dcm"), modality="CT",
        study_instance_uid="2.25.100", series_instance_uid="2.25.200", sop_instance_uid=f"2.25.{300+i}",
        sop_class_uid=str(CTImageStorage), rows="512", columns="512", pixel_spacing="0.7\\0.7",
        slice_thickness="1.25", image_position_patient=f"0\\0\\{i}", image_orientation_patient="1\\0\\0\\0\\1\\0",
        instance_number=str(i+1), rescale_slope="1", rescale_intercept="-1024", frame_of_reference_uid="2.25.400",
        image_type="ORIGINAL\\PRIMARY\\AXIAL") for i in range(count)]


def codes(records):
    return {issue.code: issue for issue in evaluate_quality(records)}


def check(records, code):
    return next(check for check in records[0].series_checks if check.code == code)


def test_real_series_grouping_and_projection_not_instance_order(tmp_path, make_dicom):
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    paths = [make_dicom(tmp_path / f"{i}.dcm", study_uid=study, series_uid=series,
        overrides={"FrameOfReferenceUID": frame, "ImagePositionPatient": [i*2, 0, 0],
                   "ImageOrientationPatient": [0, 1, 0, 0, 0, 1], "InstanceNumber": 4-i}) for i in range(4)]
    result = inventory_files(reversed(paths), source_root=tmp_path)
    assert not result.issues
    assert len(result.studies) == len(result.series) == 1
    assert result.studies[0].file_count == 4 and result.studies[0].series_count == 1
    assert result.series[0].spatial_range == (0.0, 6.0)
    assert [position for _, position in result.series[0].sorted_positions] == [0, 2, 4, 6]
    assert all(assessment.status == "已检查" for assessment in result.series[0].assessments)
    assert result.summary.issue_count == 0


@pytest.mark.parametrize("field,value,code", [
    ("rows", "256", "SERIES_INCONSISTENT_Rows"),
    ("columns", "256", "SERIES_INCONSISTENT_Columns"),
    ("pixel_spacing", "0.8\\0.7", "SERIES_INCONSISTENT_PixelSpacing"),
    ("slice_thickness", "2", "SERIES_INCONSISTENT_SliceThickness"),
    ("image_orientation_patient", "0\\1\\0\\-1\\0\\0", "SERIES_INCONSISTENT_ImageOrientationPatient"),
])
def test_consistency_rules_positive_and_preserve_all_values(tmp_path, field, value, code):
    records = synthetic_records(tmp_path)
    setattr(records[-1], field, value)
    issue = codes(records)[code]
    assert issue.severity == Severity.WARNING
    assert set(issue.affected_files) == {record.file_path for record in records}
    assert issue.description and issue.impact and issue.recommendation and issue.evidence
    _, summaries = summarize_hierarchy(records)
    values = getattr(summaries[0], {"rows": "rows", "columns": "columns", "pixel_spacing": "pixel_spacings",
        "slice_thickness": "slice_thicknesses", "image_orientation_patient": "orientations"}[field])
    assert len(values) == 2 and value in values
    if field == "image_orientation_patient":
        assert summaries[0].spatial_range is None
        assert check(records, "SLICE_SPACING").status == "未评估"


@pytest.mark.parametrize("delta,alert", [(0.001, False), (0.00101, True)])
def test_numeric_tolerance_boundary(tmp_path, delta, alert):
    records = synthetic_records(tmp_path)
    records[1].pixel_spacing = f"{0.7+delta}\\0.7"
    assert ("SERIES_INCONSISTENT_PixelSpacing" in codes(records)) == alert


def test_orientation_tolerance_and_nonorthogonal_skipped(tmp_path):
    records = synthetic_records(tmp_path)
    records[1].image_orientation_patient = "1\\0.00005\\0\\-0.00005\\1\\0"
    assert "SERIES_INCONSISTENT_ImageOrientationPatient" not in codes(records)
    assert check(records, "SPATIAL_ORDER").status == "已检查"
    records[1].image_orientation_patient = "1\\0\\0\\1\\0\\0"
    assert "SERIES_INVALID_ImageOrientationPatient" in codes(records)
    assert check(records, "SPATIAL_ORDER").status == "未评估"
    assert all(record.slice_sort_position is None for record in records)


def test_global_series_study_conflict_and_modality_conflict(tmp_path):
    records = synthetic_records(tmp_path)
    records[1].study_instance_uid = "2.25.101"
    records[1].modality = "MR"
    records[1].sop_class_uid = str(MRImageStorage)
    issues = codes(records)
    assert issues["SERIES_STUDY_CONFLICT"].severity == Severity.ERROR
    assert issues["SERIES_MODALITY_INCONSISTENT"].severity == Severity.WARNING
    assert set(issues["SERIES_STUDY_CONFLICT"].study_uids) == {"2.25.100", "2.25.101"}
    studies, series = summarize_hierarchy(records)
    assert len(studies) == 2 and len(series) == 1 and series[0].file_count == 4
    assert sum(study.file_count for study in studies) == 4
    assert check(records, "SPATIAL_ORDER").status == "未评估"


def test_duplicate_instance_and_large_gap_without_materializing_range(tmp_path):
    records = synthetic_records(tmp_path)
    records[0].instance_number = records[1].instance_number = "-5"
    records[2].instance_number = "1000000000"
    records[3].instance_number = "1000000001"
    issues = codes(records)
    repeated = issues["SERIES_DUPLICATE_INSTANCE_NUMBER"]
    assert repeated.severity == Severity.WARNING and len(repeated.affected_files) == 2
    gaps = issues["SERIES_INSTANCE_GAPS"]
    assert gaps.severity == Severity.INFO
    assert len(dict(gaps.evidence)["missing_ranges"]) < 50
    records[0].instance_number = ""
    assert "SERIES_INSTANCE_GAPS" not in codes(records)
    assert check(records, "INSTANCE_GAPS").status == "未评估"


@pytest.mark.parametrize("distance,duplicate", [(0.01, True), (0.0101, False)])
def test_duplicate_position_radius_boundary(tmp_path, distance, duplicate):
    records = synthetic_records(tmp_path)
    records[1].image_position_patient = f"{distance}\\0\\0"
    issues = codes(records)
    assert ("SERIES_DUPLICATE_POSITION" in issues) == duplicate
    assert check(records, "SLICE_SPACING").status == "未评估"  # same plane, even if different origin
    if duplicate:
        assert len(issues["SERIES_DUPLICATE_POSITION"].affected_files) == 2


@pytest.mark.parametrize("offset,alert", [(0.1, False), (0.1001, True)])
def test_slice_spacing_tolerance_boundary_and_not_thickness(tmp_path, offset, alert):
    records = synthetic_records(tmp_path, 5)
    for i, record in enumerate(records):
        record.image_position_patient = f"0\\0\\{i if i < 4 else 4+offset}"
        record.slice_thickness = "8"  # Not compared to centre-to-centre distances.
    assert ("SERIES_IRREGULAR_SPACING" in codes(records)) == alert


@pytest.mark.parametrize("reason", ["frame_missing", "frame_conflict", "localizer", "multiframe", "enhanced", "sr", "missing_position"])
def test_geometry_skip_conditions_are_visible_not_pass(tmp_path, reason):
    records = synthetic_records(tmp_path)
    if reason == "frame_missing": records[0].frame_of_reference_uid = ""
    if reason == "frame_conflict": records[0].frame_of_reference_uid = "2.25.401"
    if reason == "localizer": records[0].image_type = "ORIGINAL\\PRIMARY\\LOCALIZER"
    if reason == "multiframe": records[0].number_of_frames = "2"
    if reason == "enhanced": records[0].sop_class_uid = str(EnhancedCTImageStorage)
    if reason == "sr":
        records[0].sop_class_uid = str(BasicTextSRStorage)
        records[0].modality = "SR"
    if reason == "missing_position": records[0].image_position_patient = ""
    codes(records)
    assert check(records, "SPATIAL_ORDER").status == "未评估"
    assert check(records, "SPATIAL_ORDER").reason
    assert check(records, "SLICE_SPACING").status == "未评估"
    _, series = summarize_hierarchy(records)
    assert "未评估" in series[0].overall_status and series[0].spatial_range is None
    assert not series[0].sorted_positions


@pytest.mark.parametrize("field,value", [
    ("pixel_spacing", "NaN\\0.7"), ("slice_thickness", "-1"),
    ("image_position_patient", "1e308\\0\\0"), ("image_orientation_patient", "2\\0\\0\\0\\1\\0"),
    ("instance_number", "1.2"), ("rows", "-2"),
])
def test_invalid_values_do_not_abort_batch(tmp_path, field, value):
    records = synthetic_records(tmp_path)
    setattr(records[0], field, value)
    issues = codes(records)
    assert any(code.startswith("SERIES_INVALID_") for code in issues)
    assert records[1].read_success


@pytest.mark.parametrize("value,valid", [("", False), ("1.02.3", False), ("1..2", False),
    ("1.2.", False), ("1.２", False), ("1.2a", False), ("1."+"2"*63, False),
    ("1."+"2"*62, True), ("2.25.0", True), ("1.2.3", True)])
def test_uid_syntax(value, valid):
    assert valid_uid(value) == valid


def test_invalid_missing_uid_fallback_does_not_merge_and_stable_ids(tmp_path):
    records = synthetic_records(tmp_path)
    records[0].series_instance_uid = records[1].series_instance_uid = ""
    records[2].series_instance_uid = records[3].series_instance_uid = "1..2"
    first = evaluate_quality(records, tmp_path)
    second = evaluate_quality(list(reversed(records)), tmp_path)
    assert {issue.issue_id for issue in first} == {issue.issue_id for issue in second}
    studies, series = summarize_hierarchy(records, tmp_path)
    assert len(studies) == 1 and len(series) == 4
    assert all(summary.file_count == 1 and summary.spatial_range is None for summary in series)
    assert sum(issue.code == "INVALID_SeriesInstanceUID" for issue in first) == 2


def test_series_reassessment_preserves_file_observations_and_stable_group_ids(tmp_path):
    records = synthetic_records(tmp_path)
    records[1].rows = "256"
    records[2].columns = ""
    records[0].sop_instance_uid = records[1].sop_instance_uid
    first = evaluate_quality(records, tmp_path)
    assert any(issue.code == "DUPLICATE_SOP_UID" and len(issue.affected_files) == 2 for issue in first)
    duplicate_ids = [issue.issue_id for record in records for issue in record.issues]
    evaluate_series(records, tmp_path)
    assert [issue.issue_id for record in records for issue in record.issues] == duplicate_ids
    second = evaluate_quality(list(reversed(records)), tmp_path)
    assert {issue.issue_id for issue in first} == {issue.issue_id for issue in second}


def test_series_csv_bom_values_all_files_evaluation_and_no_identity(tmp_path, make_dicom):
    source, output = tmp_path / "source", tmp_path / "output"
    output.mkdir()
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    paths = [make_dicom(source / f"{i}.dcm", study_uid=study, series_uid=series,
        overrides={"FrameOfReferenceUID": frame, "ImagePositionPatient": [0, 0, i], "InstanceNumber": i+1,
                   "Rows": 256 if i == 1 else 512}) for i in range(3)]
    result = inventory_files(paths, source_root=source)
    destination = export_series_csv(result, output)
    assert destination.name == "series.csv" and destination.read_bytes().startswith(b"\xef\xbb\xbf")
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["FileCount"] == "3" and json.loads(row["RowsValues"]) == ["256", "512"]
    assert set(json.loads(row["Files"])) == {str(path.resolve()) for path in paths}
    assert json.loads(row["RuleAssessments"])[0]["status"] == "已检查"
    assert json.loads(row["ProjectedRangeMM"]) == [0.0, 2.0]
    assert json.loads(row["FrameOfReferenceUIDs"]) == [frame]
    assert json.loads(row["ImageTypes"]) == [""]
    assert not any(value in destination.read_text(encoding="utf-8-sig") for value in ("PatientName", "PatientID", "PRIVATE-ID"))
    assert export_series_csv(result, output).name == "series_1.csv"
    with pytest.raises(ValueError): export_series_csv(result, source)
    with export_series_csv(inventory_files([]), output).open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == []


def test_insufficient_samples_and_mixed_empty_summary_values(tmp_path):
    records = synthetic_records(tmp_path, 1)
    assert not codes(records)
    assert check(records, "SLICE_SPACING").status == "未评估"
    assert check(records, "INSTANCE_GAPS").status == "未评估"
    records = synthetic_records(tmp_path, 3)
    records[0].slice_thickness = ""
    codes(records)
    _, series = summarize_hierarchy(records)
    assert series[0].slice_thicknesses == ("", "1.25")
    assert check(records, "SLICE_THICKNESS").status == "未评估"


def test_single_row_zero_spacing_exception_and_negative_rejected(tmp_path):
    records = synthetic_records(tmp_path)
    for record in records:
        record.rows = "1"
        record.pixel_spacing = "0\\0.7"
    assert "SERIES_INVALID_PixelSpacing" not in codes(records)
    records[0].rows = "512"
    assert "SERIES_INVALID_PixelSpacing" in codes(records)
    records[0].pixel_spacing = "-0.1\\0.7"
    assert "SERIES_INVALID_PixelSpacing" in codes(records)


def test_many_exact_duplicate_positions_complete_links(tmp_path):
    records = synthetic_records(tmp_path, 2000)
    for record in records:
        record.image_position_patient = "0\\0\\0"
    issue = codes(records)["SERIES_DUPLICATE_POSITION"]
    assert len(issue.affected_files) == 2000
    assert check(records, "SLICE_SPACING").status == "未评估"


def test_invalid_matrix_severity_and_evidence(tmp_path):
    records = synthetic_records(tmp_path)
    records[0].rows = "-1"
    issue = codes(records)["SERIES_INVALID_Rows"]
    assert issue.severity == Severity.ERROR and dict(issue.evidence)["affected_count"] == "1"


@pytest.mark.parametrize("value,alert", [("1001", False), ("1001.002", True)])
def test_relative_numeric_tolerance(tmp_path, value, alert):
    records = synthetic_records(tmp_path)
    for record in records:
        record.slice_thickness = "1000"
    records[0].slice_thickness = value
    assert ("SERIES_INCONSISTENT_SliceThickness" in codes(records)) == alert


def test_mr_and_independent_series_no_false_cross_series_alerts(tmp_path):
    records = synthetic_records(tmp_path)
    for record in records:
        record.sop_class_uid = str(MRImageStorage)
        record.modality = "MR"
        record.rescale_slope = record.rescale_intercept = ""
    assert not codes(records)
    assert check(records, "SPATIAL_ORDER").status == "已检查"
    records[-1].series_instance_uid = "2.25.201"
    records[-1].rows = "256"
    assert "SERIES_INCONSISTENT_Rows" not in codes(records)
    _, series = summarize_hierarchy(records)
    assert len(series) == 2 and sorted(summary.file_count for summary in series) == [1, 3]
