"""Anonymous DICOM fixtures generated entirely for automated tests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid


@pytest.fixture
def make_dicom():
    def factory(
        path: Path,
        *,
        study_uid: str | None = None,
        series_uid: str | None = None,
        sop_uid: str | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> Path:
        actual_sop_uid = sop_uid or generate_uid()
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = actual_sop_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = generate_uid()

        dataset = FileDataset(
            str(path),
            {},
            file_meta=file_meta,
            preamble=b"\0" * 128,
        )
        dataset.SOPClassUID = CTImageStorage
        dataset.SpecificCharacterSet = "ISO_IR 192"
        dataset.SOPInstanceUID = actual_sop_uid
        dataset.StudyInstanceUID = study_uid or generate_uid()
        dataset.SeriesInstanceUID = series_uid or generate_uid()
        dataset.Modality = "CT"
        dataset.Rows = 512
        dataset.Columns = 512
        dataset.PixelSpacing = ["0.7", "0.7"]
        dataset.SliceThickness = "1.25"
        dataset.ImagePositionPatient = ["0", "0", "10"]
        dataset.ImageOrientationPatient = ["1", "0", "0", "0", "1", "0"]
        dataset.InstanceNumber = "1"
        dataset.RescaleSlope = "1"
        dataset.RescaleIntercept = "-1024"
        dataset.PatientName = "测试^身份字段不得导出"
        dataset.PatientID = "PRIVATE-ID"

        for keyword, value in (overrides or {}).items():
            if value is None:
                if keyword in dataset:
                    del dataset[keyword]
            else:
                setattr(dataset, keyword, value)

        path.parent.mkdir(parents=True, exist_ok=True)
        dataset.save_as(path, enforce_file_format=True)
        return path

    return factory
