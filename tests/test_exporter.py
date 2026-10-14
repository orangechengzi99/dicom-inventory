from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from dicom_inventory.core import CSV_COLUMNS, export_csv, inventory_files
from dicom_inventory.core.exporter import is_output_directory_allowed


def test_csv_is_excel_friendly_and_excludes_identity_fields(
    tmp_path: Path, make_dicom
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    output.mkdir()
    dicom_path = make_dicom(source / "中文影像.dcm")
    result = inventory_files([dicom_path])

    exported = export_csv(
        result,
        output,
        timestamp=datetime(2026, 8, 31, 12, 30, 45),
    )

    assert exported.name == "dicom_inventory_20260831_123045.csv"
    raw = exported.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "PatientName" not in text
    assert "PatientID" not in text
    assert "PRIVATE-ID" not in text
    with exported.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 1
    assert rows[0]["FilePath"].endswith("中文影像.dcm")
    assert rows[0]["Status"] == "成功"


def test_export_avoids_overwriting_existing_file(tmp_path: Path) -> None:
    result = inventory_files([])
    moment = datetime(2026, 8, 31, 12, 30, 45)
    first = export_csv(result, tmp_path, timestamp=moment)
    second = export_csv(result, tmp_path, timestamp=moment)

    assert first.name == "dicom_inventory_20260831_123045.csv"
    assert second.name == "dicom_inventory_20260831_123045_1.csv"


def test_output_directory_must_be_separate_from_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    child = source / "exports"
    child.mkdir()
    sibling = tmp_path / "exports"
    sibling.mkdir()

    assert not is_output_directory_allowed(source, source)
    assert not is_output_directory_allowed(source, child)
    assert is_output_directory_allowed(source, sibling)
