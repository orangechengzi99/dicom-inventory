from __future__ import annotations

from pathlib import Path

from pydicom.uid import generate_uid

from dicom_inventory.core import discover_dicom_files, inventory_files


def test_discovery_uses_extensions_and_dicom_prefix(tmp_path: Path, make_dicom) -> None:
    extension_file = make_dicom(tmp_path / "nested" / "image.DCM")
    no_extension_file = make_dicom(tmp_path / "DICOMDIR")
    damaged_extension = tmp_path / "damaged.dicom"
    damaged_extension.write_bytes(b"not a dicom")
    (tmp_path / "notes.txt").write_text("ordinary file", encoding="utf-8")

    discovered = discover_dicom_files(tmp_path)

    assert discovered == sorted(
        [extension_file.resolve(), no_extension_file.resolve(), damaged_extension.resolve()],
        key=lambda item: str(item).casefold(),
    )


def test_inventory_counts_duplicates_missing_tags_and_failures(
    tmp_path: Path, make_dicom
) -> None:
    study_one = generate_uid()
    study_two = generate_uid()
    series_one = generate_uid()
    series_two = generate_uid()
    duplicate_sop = generate_uid()
    first = make_dicom(
        tmp_path / "one.dcm",
        study_uid=study_one,
        series_uid=series_one,
        sop_uid=duplicate_sop,
    )
    second = make_dicom(
        tmp_path / "two.dcm",
        study_uid=study_one,
        series_uid=series_one,
        sop_uid=duplicate_sop,
    )
    missing = make_dicom(
        tmp_path / "three.dcm",
        study_uid=study_two,
        series_uid=series_two,
        overrides={"SliceThickness": None},
    )
    damaged = tmp_path / "broken.dcm"
    damaged.write_bytes(b"broken")
    progress: list[tuple[int, int, str]] = []

    result = inventory_files(
        [first, second, missing, damaged],
        lambda done, total, record: progress.append((done, total, record.file_path)),
    )

    assert result.summary.file_count == 4
    assert result.summary.success_count == 3
    assert result.summary.failure_count == 1
    assert result.summary.study_count == 2
    assert result.summary.series_count == 2
    assert result.summary.unique_instance_count == 2
    assert result.summary.duplicate_instance_count == 1
    assert result.duplicates[duplicate_sop] == [str(first.resolve()), str(second.resolve())]
    assert result.records[0].is_duplicate
    assert result.records[1].is_duplicate
    assert "SliceThickness" in result.records[2].missing_tags
    assert result.records[2].is_anomaly
    assert not result.records[3].read_success
    assert result.records[3].error_message
    assert [item[0] for item in progress] == [1, 2, 3, 4]
    assert all(item[1] == 4 for item in progress)


def test_empty_inventory_has_zero_counts() -> None:
    result = inventory_files([])

    assert result.records == []
    assert result.duplicates == {}
    assert result.summary.file_count == 0
    assert result.summary.unique_instance_count == 0
