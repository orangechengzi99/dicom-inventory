"""Generated-only replay, ownership refusal, and allowlisted portable source ZIP."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest
from pydicom import dcmread
from tools.synthetic_demo import PHASES, create_demo, payloads, replay, switch_phase, validate_demo
from tools.build_source_bundle import ROOT_FILES, DOC_FILES, SCREEN_FILES, build_bundle


def hashes(root):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "source").iterdir() if path.is_file()}


@pytest.mark.parametrize("phase", PHASES)
def test_demo_phases_are_owned_identity_and_pixel_free(tmp_path, phase):
    root = create_demo(tmp_path / "demos")
    switch_phase(root, phase)
    _, marker = validate_demo(root)
    assert marker["phase"] == phase and hashes(root) == marker["files"]
    assert len(marker["files"]) == (12 if phase in ("changed", "recurrence") else 11)
    for name in marker["files"]:
        if phase == "baseline" and name == "invalid_demo.dcm":
            continue  # Deliberately corrupt fixture, not an attempted clinical object.
        data = dcmread(root / "source" / name)
        assert all(keyword not in data for keyword in ("PatientName", "PatientID", "PatientBirthDate", "PixelData"))
        assert "SYNTHETIC" in data.SeriesDescription
    assert not list((root / "work").iterdir())


def test_payloads_repeat_for_same_case_and_phase(tmp_path):
    root = create_demo(tmp_path)
    _, marker = validate_demo(root)
    assert payloads(marker["case_id"], "baseline") == payloads(marker["case_id"], "baseline")
    assert all((root / "source" / name).read_bytes() == content for name, content in payloads(marker["case_id"], "baseline").items())


def test_new_generation_never_overwrites_an_existing_demo(tmp_path):
    first = create_demo(tmp_path)
    before = hashes(first)
    second = create_demo(tmp_path)
    assert first != second and hashes(first) == before and hashes(second) != before


@pytest.mark.parametrize("kind", ["modified", "extra", "forged_manifest", "hardlink"])
def test_phase_switch_refuses_foreign_or_modified_inputs_before_any_write(tmp_path, kind):
    root = create_demo(tmp_path / "demos")
    file = root / "source" / "CT_001.dcm"
    if kind in ("modified", "forged_manifest"):
        file.write_bytes(file.read_bytes() + b"untrusted external change")
        if kind == "forged_manifest":
            manifest = json.loads((root / "demo.json").read_text())
            manifest["files"][file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
            (root / "demo.json").write_text(json.dumps(manifest), encoding="utf-8")
    elif kind == "extra":
        (root / "source" / "external.dcm").write_bytes(b"not created by this generator")
    else:
        os.link(file, tmp_path / "external-link.dcm")
    before = hashes(root)
    marker = (root / "demo.json").read_bytes()
    with pytest.raises(ValueError):
        switch_phase(root, "fixed")
    assert hashes(root) == before and (root / "demo.json").read_bytes() == marker


def test_non_demo_directory_is_not_touched(tmp_path):
    source = tmp_path / "not-a-demo"
    source.mkdir()
    file = source / "example.dcm"
    file.write_bytes(b"unowned example")
    with pytest.raises((ValueError, OSError)):
        switch_phase(source, "fixed")
    assert file.read_bytes() == b"unowned example" and list(source.iterdir()) == [file]


def test_replay_actual_close_reconfirm_reopen_and_removal_with_four_csvs(tmp_path):
    root = replay(tmp_path)
    data = json.loads((root / "replay.json").read_text(encoding="utf-8"))
    assert data["synthetic_only"]
    steps = {item["phase"]: item for item in data["steps"]}
    assert tuple(steps) == PHASES
    assert [steps[phase]["summary"]["file_count"] for phase in PHASES] == [11, 11, 12, 12, 11]
    assert [steps[phase]["summary"]["issue_count"] for phase in PHASES] == [5, 2, 3, 4, 3]
    assert sum(row["action"] == "关闭" for row in steps["fixed"]["conclusions"]) == 3
    assert {row["status"] for row in steps["fixed"]["conclusions"]} == {"已解决", "仍然存在", "已忽略"}
    assert any(row["status"] == "需重新确认" and row["action"] == "重新确认" for row in steps["changed"]["conclusions"])
    assert any(row["code"] == "MISSING_PixelSpacing" and row["action"] == "重开" for row in steps["recurrence"]["conclusions"])
    assert any(row["code"] == "MISSING_PixelSpacing" and row["status"] == "无法确认" and not row["action"] for row in steps["removed"]["conclusions"])
    from dicom_inventory.storage import SnapshotStore
    store = SnapshotStore(root / "work")
    assert len(store.list_scans()) == 5 and len(store.list_comparisons()) == 4
    for phase in PHASES[1:]:
        step = steps[phase]
        assert len(step["exports"]) == 4 and step["comparable"]
        assert store.load(step["run_id"]).summary == store.load(step["run_id"]).scan_run.summary
        for filename in step["exports"]:
            assert (root / filename).read_bytes().startswith(b"\xef\xbb\xbf")
    # Removed refers only to owned synthetic data and can be regenerated.
    switch_phase(root, "baseline")
    assert (root / "source" / "CT_001.dcm").exists()


@pytest.fixture
def public_project(tmp_path):
    root = tmp_path / "source-project"
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text("portable synthetic test", encoding="utf-8")
    for folder in ("dicom_inventory/gui", "tests", "tools", "docs/screenshots"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    for folder in ("dicom_inventory", "tests", "tools"):
        (root / folder / "example.py").write_text("# synthetic public code\n", encoding="utf-8")
    (root / "dicom_inventory/gui/chevron.svg").write_text("<svg/>", encoding="utf-8")
    for name in DOC_FILES:
        (root / "docs" / name).write_text("portable public documentation", encoding="utf-8")
    for name in SCREEN_FILES:
        # Header fixture checks archive membership, not visual acceptance.
        (root / "docs/screenshots" / name).write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-header")
    return root


def test_bundle_contains_only_allowlisted_files_and_verified_manifest(public_project, tmp_path):
    for name in ("patient.dcm", "issues.csv", "comparison.csv", "dicom_inventory.sqlite3", ".env", "progress.md", "AGENTS.md"):
        (public_project / name).write_text("not public", encoding="utf-8")
    (public_project / "docs/private-note.md").write_text("not public", encoding="utf-8")
    file = build_bundle(public_project, tmp_path / "delivery")
    with zipfile.ZipFile(file) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("DICOMInventory/DELIVERY_MANIFEST.json"))
        assert not manifest["license_selected"]
        names = archive.namelist()
        assert len(names) == len(manifest["files"]) + 1
        assert all(name.startswith("DICOMInventory/") and ".." not in name for name in names)
        assert not any(name.endswith((".dcm", ".csv", ".sqlite3", ".env", "progress.md", "AGENTS.md", "private-note.md")) for name in names)
        for item in manifest["files"]:
            content = archive.read("DICOMInventory/" + item["path"])
            assert len(content) == item["bytes"] and hashlib.sha256(content).hexdigest() == item["sha256"]


def test_bundle_refuses_missing_required_material_before_output(public_project, tmp_path):
    (public_project / "docs/DEMO_GUIDE.md").unlink()
    output = tmp_path / "delivery"
    with pytest.raises(ValueError):
        build_bundle(public_project, output)
    assert not output.exists()


def test_bundle_refuses_private_local_paths(public_project, tmp_path):
    private = "C:" + "/" + "Users" + "/private/example"
    (public_project / "README.md").write_text(private, encoding="utf-8")
    with pytest.raises(ValueError, match="Private local path"):
        build_bundle(public_project, tmp_path / "delivery")


def test_bundle_refuses_hardlinks(public_project, tmp_path):
    os.link(public_project / "README.md", tmp_path / "linked.md")
    with pytest.raises(ValueError, match="Linked"):
        build_bundle(public_project, tmp_path / "delivery")


def test_bundle_never_overwrites_existing_archive(public_project, tmp_path):
    first = build_bundle(public_project, tmp_path / "delivery")
    content = first.read_bytes()
    second = build_bundle(public_project, tmp_path / "delivery")
    assert first != second and first.read_bytes() == content


def test_capture_does_not_overwrite_existing_screenshot(tmp_path):
    root = replay(tmp_path / "demos")
    output = tmp_path / "pictures"
    output.mkdir()
    file = output / "inventory.png"
    file.write_bytes(b"existing user picture")
    from tools.capture_demo import capture
    with pytest.raises(ValueError, match="already exists"):
        capture(root, output)
    assert file.read_bytes() == b"existing user picture"
