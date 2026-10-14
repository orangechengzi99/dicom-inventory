from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
import pydicom
from pydicom.uid import (MRImageStorage, SecondaryCaptureImageStorage, BasicTextSRStorage,
                         EncapsulatedPDFStorage, TwelveLeadECGWaveformStorage, EnhancedCTImageStorage)

from dicom_inventory.core import (DICOM_FIELDS, Severity, evaluate_quality, export_csv,
                                  export_issues_csv, field_requirements, inventory_files)
from dicom_inventory.core.inventory import DiscoveredPaths, discover_dicom_files


def test_one_file_many_issues_and_group_counts(tmp_path, make_dicom):
    a = make_dicom(tmp_path / "a.dcm", overrides={"PixelSpacing": None, "Rows": None})
    first = inventory_files([a]).records[0]
    b = make_dicom(tmp_path / "b.dcm", sop_uid=first.sop_instance_uid)
    result = inventory_files([a, b], source_root=tmp_path)
    assert len(result.records[0].issues) == 3
    assert result.summary.anomaly_file_count == 2
    assert result.summary.issue_count == 3
    assert result.summary.error_count == 1
    assert result.summary.warning_count == 2
    assert result.summary.info_count == 0
    assert result.summary.duplicate_instance_count == 1
    duplicate = next(item for item in result.issues if item.code == "DUPLICATE_SOP_UID")
    assert set(duplicate.affected_files) == {str(a.resolve()), str(b.resolve())}
    assert len(duplicate.study_uids) == 2  # Never arbitrarily select one Study.
    assert result.records[0].highest_severity == Severity.ERROR
    assert result.records[1].highest_severity == Severity.WARNING


def test_stable_keys_across_runs_order_and_evidence_change(tmp_path, make_dicom):
    a = make_dicom(tmp_path / "source" / "a.dcm", overrides={"Rows": None})
    b = make_dicom(tmp_path / "source" / "b.dcm", overrides={"PixelSpacing": None})
    one = inventory_files([a, b], source_root=a.parent)
    two = inventory_files([b, a], source_root=a.parent)
    assert {item.issue_id for item in one.issues} == {item.issue_id for item in two.issues}
    assert one.scan_run.run_id != two.scan_run.run_id
    moved = make_dicom(tmp_path / "relocated" / "a.dcm", overrides={"Rows": None})
    three = inventory_files([moved], source_root=moved.parent)
    assert three.issues[0].issue_id == next(item.issue_id for item in one.issues if item.field_name == "Rows")
    # These keys are dataset-local; identity evidence is NOT a solved recheck matcher.
    assert len(evaluate_quality(one.records, a.parent)) == len(one.issues)
    assert len(evaluate_quality(one.records, a.parent)) == len(one.issues)


def test_ct_and_mr_profiles_have_different_rescale_policy(tmp_path, make_dicom):
    ct = make_dicom(tmp_path / "ct.dcm", overrides={"RescaleSlope": None, "RescaleIntercept": None})
    mr = make_dicom(tmp_path / "mr.dcm", overrides={"SOPClassUID": MRImageStorage, "Modality": "MR",
                                                    "RescaleSlope": None, "RescaleIntercept": None})
    result = inventory_files([ct, mr])
    assert len(result.records[0].issues) == 2
    assert all(item.severity == Severity.ERROR for item in result.records[0].issues)
    assert result.records[1].issues == ()
    assert field_requirements(result.records[1])["RescaleSlope"] == "not_applicable"
    assert result.records[1].quality_status == "已检查"


@pytest.mark.parametrize("sop,modality", [
    (SecondaryCaptureImageStorage, "CT"), (BasicTextSRStorage, "SR"),
    (EncapsulatedPDFStorage, "DOC"), (TwelveLeadECGWaveformStorage, "ECG"),
    (EnhancedCTImageStorage, "CT"),
])
def test_unsupported_objects_do_not_receive_image_field_missing_alerts(tmp_path, make_dicom, sop, modality):
    path = make_dicom(tmp_path / "object.dcm", overrides={
        "SOPClassUID": sop, "Modality": modality, "Rows": None, "Columns": None,
        "PixelSpacing": None, "SliceThickness": None, "ImagePositionPatient": None,
        "ImageOrientationPatient": None, "RescaleSlope": None, "RescaleIntercept": None,
    })
    result = inventory_files([path])
    record = result.records[0]
    assert record.assessment_status == "未评估"
    assert record.missing_tags == ()
    assert [item.code for item in record.issues] == ["PROFILE_NOT_EVALUATED"]
    assert result.summary.unevaluated_count == 1
    assert result.summary.info_count == 1
    assert record.quality_status == "未评估"


@pytest.mark.parametrize("overrides", [{"NumberOfFrames": 2}, {"Modality": "MR"}])
def test_multiframe_or_mismatched_classification_not_assumed_single_frame(tmp_path, make_dicom, overrides):
    record = inventory_files([make_dicom(tmp_path / "a.dcm", overrides=overrides)]).records[0]
    assert record.assessment_status == "未评估"
    assert any(item.code == "PROFILE_NOT_EVALUATED" for item in record.issues)


def test_empty_reference_fields_are_info_not_type1_errors(tmp_path, make_dicom):
    record = inventory_files([make_dicom(tmp_path / "a.dcm", overrides={"SliceThickness": "", "InstanceNumber": None})]).records[0]
    assert len(record.issues) == 2
    assert all(item.severity == Severity.INFO for item in record.issues)
    assert record.highest_severity == Severity.INFO
    assert record.quality_status == "提示"


def test_missing_identifiers_and_read_failure_have_stable_fallback_keys(tmp_path, make_dicom):
    a = make_dicom(tmp_path / "a.dcm", overrides={"SOPInstanceUID": None, "StudyInstanceUID": None})
    broken = tmp_path / "bad.dcm"
    broken.write_bytes(b"not dicom")
    one = inventory_files([a, broken])
    two = inventory_files([a, broken])
    assert one.summary.failure_count == 1
    assert one.summary.error_count == 3
    assert one.summary.unique_instance_count == 0
    assert {item.issue_id for item in one.issues} == {item.issue_id for item in two.issues}
    assert all(item.description and item.impact and item.recommendation for item in one.issues)
    assert all(item.evidence for item in one.issues)


def test_scan_summary_and_discovery_incomplete_evidence(tmp_path, make_dicom, monkeypatch):
    path = make_dicom(tmp_path / "a.dcm")
    def denied_walk(root, *, onerror):
        onerror(PermissionError("private path must not be exposed"))
        yield str(tmp_path), [], [path.name]
    monkeypatch.setattr("dicom_inventory.core.inventory.os.walk", denied_walk)
    paths = discover_dicom_files(tmp_path)
    assert isinstance(paths, DiscoveredPaths) and not paths.complete
    result = inventory_files(paths, source_root=tmp_path)
    assert result.scan_run.status == "未完成"
    assert result.scan_run.summary == result.summary
    assert result.scan_run.started_at <= result.scan_run.finished_at
    assert "private" not in str(result.scan_run.notes)


def test_duplicate_input_paths_do_not_create_false_duplicate_issues(tmp_path, make_dicom):
    a = make_dicom(tmp_path / "a.dcm")
    result = inventory_files([a, a])
    assert result.summary.file_count == 1
    assert not result.duplicates and not result.issues


def test_reader_requests_no_identity_or_pixel_tags_and_does_not_change_source(tmp_path, make_dicom, monkeypatch):
    path = make_dicom(tmp_path / "a.dcm", overrides={"PixelData": b"\0\0", "PatientBirthDate": "20000101"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = pydicom.dcmread
    calls = []
    def checked_reader(*args, **kwargs):
        assert kwargs["stop_before_pixels"] is True
        assert set(kwargs["specific_tags"]) == set(DICOM_FIELDS)
        assert not {"PatientName", "PatientID", "PatientBirthDate", "PixelData"} & set(kwargs["specific_tags"])
        dataset = original(*args, **kwargs)
        assert not {"PatientName", "PatientID", "PatientBirthDate", "PixelData"} & set(dataset.dir())
        calls.append(True)
        return dataset
    monkeypatch.setattr(pydicom, "dcmread", checked_reader)
    result = inventory_files([path], source_root=tmp_path)
    assert calls == [True] and result.summary.success_count == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_exception_message_does_not_leak_raw_parser_values(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("PatientName=PRIVATE-ID hidden value")
    monkeypatch.setattr(pydicom, "dcmread", fail)
    result = inventory_files([tmp_path / "a.dcm"])
    assert "PRIVATE-ID" not in str(result)
    assert "ValueError" in result.records[0].error_message


def test_issue_csv_all_links_encoding_identity_and_collision(tmp_path, make_dicom):
    source, output = tmp_path / "source", tmp_path / "output"
    output.mkdir()
    a = make_dicom(source / "a.dcm", overrides={"PixelSpacing": None})
    uid = inventory_files([a]).records[0].sop_instance_uid
    b = make_dicom(source / "b.dcm", sop_uid=uid)
    result = inventory_files([a, b], source_root=source)
    destination = export_issues_csv(result, output)
    assert destination.name == "issues.csv"
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    text = destination.read_text(encoding="utf-8-sig")
    assert all(word not in text for word in ("PatientName", "PatientID", "PatientBirthDate", "PRIVATE-ID"))
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == result.summary.issue_count
    duplicate = next(row for row in rows if row["RuleCode"] == "DUPLICATE_SOP_UID")
    assert set(json.loads(duplicate["AffectedFiles"])) == {str(a.resolve()), str(b.resolve())}
    assert export_issues_csv(result, output).name == "issues_1.csv"
    assert destination.read_text(encoding="utf-8-sig") == text


@pytest.mark.parametrize("exporter", [export_csv, export_issues_csv])
def test_core_exports_reject_source_and_descendants(tmp_path, make_dicom, exporter):
    source = tmp_path / "source"
    a = make_dicom(source / "nested" / "a.dcm")
    result = inventory_files([a], source_root=source)
    child = source / "exports"
    child.mkdir()
    for directory in (source, child, a.parent):
        with pytest.raises(ValueError):
            exporter(result, directory)
    assert list(child.iterdir()) == []


def test_empty_issue_export_keeps_header(tmp_path):
    output = export_issues_csv(inventory_files([]), tmp_path)
    with output.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert "IssueID" in reader.fieldnames
        assert list(reader) == []


def test_dicomdir_is_not_missing_image_or_instance_tags(tmp_path, make_dicom):
    from pydicom.uid import MediaStorageDirectoryStorage
    path = make_dicom(tmp_path / "DICOMDIR")
    dataset = pydicom.dcmread(path)
    for keyword in ("SOPClassUID", "SOPInstanceUID", "Rows", "Columns", "PixelSpacing"):
        del dataset[keyword]
    dataset.file_meta.MediaStorageSOPClassUID = MediaStorageDirectoryStorage
    dataset.save_as(path, enforce_file_format=True)
    record = inventory_files([path]).records[0]
    assert record.missing_tags == ()
    assert [issue.code for issue in record.issues] == ["PROFILE_NOT_EVALUATED"]


def test_unreadable_prefix_probe_marks_discovery_incomplete(tmp_path, monkeypatch):
    from dicom_inventory.core import inventory as module
    def denied_probe(path, on_error=None):
        on_error()
        return False
    monkeypatch.setattr(module, "_has_dicom_prefix", denied_probe)
    (tmp_path / "unreadable_without_extension").write_bytes(b"synthetic")
    paths = discover_dicom_files(tmp_path)
    assert not paths and not paths.complete
    result = inventory_files(paths, source_root=tmp_path)
    assert result.scan_run.status == "未完成"


def test_csv_formula_escaping_preserves_normal_numeric_metadata(tmp_path, make_dicom):
    source, output = tmp_path / "source", tmp_path / "output"
    output.mkdir()
    path = make_dicom(source / "a.dcm")
    result = inventory_files([path])
    result.records[0].modality = "=HYPERLINK(\"unsafe\")"
    result.records[0].image_position_patient = "-50.5\\-10\\0"
    exported = export_csv(result, output)
    with exported.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["Modality"].startswith("'=HYPERLINK")
    assert row["RescaleIntercept"] == "-1024"
    assert row["ImagePositionPatient"] == "-50.5\\-10\\0"
