"""Generate owned metadata-only DICOM stubs and replay the actual V1.4 core.

Only bytes generated from this demo's UUID and phase may be replaced. Real
DICOM, modified demo files, links, and unexpected input files are refused.
The application itself remains read-only; these tools are separate fixtures.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, MRImageStorage, BasicTextSRStorage, ExplicitVRLittleEndian

SCHEMA = "DICOMInventorySyntheticDemo/1"
PHASES = ("baseline", "fixed", "changed", "recurrence", "removed")


def _uid(case_id, label):
    return "2.25." + str(uuid5(UUID(case_id), label).int)


def _dicom_bytes(case_id, name, phase):
    is_mr, is_sr, repaired = name.startswith("MR_"), name == "SR_demo.dcm", name == "invalid_demo.dcm"
    if repaired and phase == "baseline":
        return b"SYNTHETIC DEMO: intentionally unreadable metadata fixture, not patient data."
    index = int(name[3:6]) if name.startswith(("CT_", "MR_")) else 1
    group = "mr" if is_mr else "sr" if is_sr else "repair" if repaired else "ct"
    sop_class = MRImageStorage if is_mr else BasicTextSRStorage if is_sr else CTImageStorage
    sop_label = "ct-duplicate" if name in ("CT_005.dcm", "CT_006.dcm") else name
    sop = _uid(case_id, "sop:" + sop_label)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = _uid(case_id, "demo-writer")
    meta.ImplementationVersionName = "DICOMINV_DEMO1"
    data = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    data.SOPClassUID = sop_class
    data.SOPInstanceUID = sop
    data.StudyInstanceUID = _uid(case_id, "study:mr" if is_mr else "study:ct")
    data.SeriesInstanceUID = _uid(case_id, "series:" + group)
    data.Modality = "MR" if is_mr else "SR" if is_sr else "CT"
    data.SeriesDescription = "SYNTHETIC DEMO - NOT CLINICAL"
    data.SpecificCharacterSet = "ISO_IR 192"
    if not is_sr:
        data.Rows = 256 if is_mr or (phase == "baseline" and name == "CT_003.dcm") else 512
        data.Columns = 256 if is_mr else 512
        if not (name == "CT_001.dcm" and phase in ("baseline", "recurrence")):
            data.PixelSpacing = [1, 1] if is_mr else [.7, .7]
        data.SliceThickness = 2 if is_mr else 2.5 if name == "CT_006.dcm" and phase in ("changed", "recurrence", "removed") else 1.25
        data.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        data.ImagePositionPatient = [0, 0, (index - 1) * (2 if is_mr else 1.25)]
        data.InstanceNumber = index
        data.FrameOfReferenceUID = _uid(case_id, "frame:" + group)
        data.ImageType = ["ORIGINAL", "PRIMARY"]
        if not is_mr:
            data.RescaleSlope = 1
            data.RescaleIntercept = -1024
    # No patient identity or PixelData. These are metadata fixtures, not
    # complete image/SR objects for a viewer, diagnosis, or IOD certification.
    stream = io.BytesIO()
    data.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def payloads(case_id, phase):
    if phase not in PHASES:
        raise ValueError("Unknown synthetic phase.")
    names = [f"CT_{i:03}.dcm" for i in range(1, 7)] + [f"MR_{i:03}.dcm" for i in range(1, 4)] + ["SR_demo.dcm", "invalid_demo.dcm"]
    if phase in ("changed", "recurrence", "removed"):
        names.append("CT_007.dcm")
    if phase == "removed":
        names.remove("CT_001.dcm")
    return {name: _dicom_bytes(case_id, name, phase) for name in names}


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _safe_path(root, path):
    # Inspect lexical parents too; resolving first would hide a symlink.
    current = path
    while current != root.parent:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Links/junctions are not allowed in a synthetic demo.")
        if current == root:
            break
        current = current.parent
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Synthetic path leaves its demo root.")
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("Hard-linked files are not owned demo files.")


def _manifest(case_id, phase, contents):
    return {"schema": SCHEMA, "case_id": case_id, "phase": phase,
            "files": {name: _digest(content) for name, content in contents.items()}}


def create_demo(output):
    parent = Path(output).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    case_id = str(uuid4())
    root = parent / ("demo-" + case_id)
    root.mkdir(exist_ok=False)
    for name in ("source", "work", "exports"):
        (root / name).mkdir()
    contents = payloads(case_id, "baseline")
    for name, content in contents.items():
        (root / "source" / name).write_bytes(content)
    (root / "demo.json").write_text(json.dumps(_manifest(case_id, "baseline", contents), indent=2), encoding="utf-8")
    return root


def validate_demo(root):
    root = Path(root).expanduser().absolute()
    marker = root / "demo.json"
    _safe_path(root, marker)
    _safe_path(root, root / "source")
    _safe_path(root, root / "work")
    _safe_path(root, root / "exports")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"schema", "case_id", "phase", "files"} or data["schema"] != SCHEMA
        or not isinstance(data["case_id"], str) or root.name != "demo-" + str(UUID(data["case_id"]))):
        raise ValueError("Not a supported generator-owned synthetic demo.")
    expected = payloads(data["case_id"], data["phase"])
    if data["files"] != {name: _digest(content) for name, content in expected.items()}:
        raise ValueError("Demo manifest does not match reproducible synthetic bytes.")
    actual = {path.name for path in (root / "source").iterdir()}
    if actual != set(expected):
        raise ValueError("Unexpected or missing source files; refusing to change this directory.")
    for name, content in expected.items():
        path = root / "source" / name
        _safe_path(root, path)
        if not path.is_file() or _digest(path.read_bytes()) != _digest(content):
            raise ValueError("A source file is modified/not owned; refusing all phase changes.")
    return root, data


def switch_phase(root, phase):
    root, data = validate_demo(root)  # Preflight every file before any write.
    contents = payloads(data["case_id"], phase)
    for name in set(data["files"]) - set(contents):
        # Exact generator-created synthetic file, known hash and bounded path.
        (root / "source" / name).unlink()
    for name, content in contents.items():
        path = root / "source" / name
        _safe_path(root, path)
        path.write_bytes(content)
    (root / "demo.json").write_text(json.dumps(_manifest(data["case_id"], phase, contents), indent=2), encoding="utf-8")
    return root


def replay(output):
    from dicom_inventory.core import discover_dicom_files, inventory_files, export_csv, export_series_csv
    from dicom_inventory.core.comparison_export import export_comparison_csv
    from dicom_inventory.storage import SnapshotStore
    from dicom_inventory.handling_export import export_handling_csv
    root = create_demo(output)
    source = root / "source"
    store = SnapshotStore(root / "work", source)
    def scan():
        result = inventory_files(discover_dicom_files(source), source_root=source)
        store.save(result)
        return result
    def mark(result, predicate, state, note):
        records = store.get_handling(result.scan_run.run_id)
        expected = {issue.issue_id: (records[issue.issue_id].track_key, records[issue.issue_id].revision)
                    for issue in result.issues if predicate(issue)}
        if expected:
            store.update_handling(result.scan_run.run_id, expected, state, note)
    baseline = scan()
    mark(baseline, lambda i: i.code in {"MISSING_PixelSpacing", "SERIES_INCONSISTENT_Rows", "READ_FAILED"},
         "已处理待复检", "仅合成演示：模拟外部处理，等待软件复检；非真实患者。")
    mark(baseline, lambda i: i.code == "DUPLICATE_SOP_UID", "已确认", "合成重复UID保留，用于展示仍然存在；不能据此删除。")
    mark(baseline, lambda i: i.code == "PROFILE_NOT_EVALUATED", "可忽略", "仅演示所选CT/MR规则不适用于SR；不声称SR合规。")
    steps = [{"phase": "baseline", "run_id": baseline.scan_run.run_id, "summary": asdict(baseline.summary)}]
    previous = baseline
    for phase in PHASES[1:]:
        switch_phase(root, phase)
        current = scan()
        report = store.compare_and_save(previous.scan_run.run_id, current.scan_run.run_id)
        destination = root / "exports" / phase
        destination.mkdir()
        exported = [export_csv(current, destination, source_root=source),
                    export_series_csv(current, destination, source_root=source),
                    export_handling_csv(current, destination, root / "work", source_root=source),
                    export_comparison_csv(report, previous, current, destination)]
        steps.append({"phase": phase, "run_id": current.scan_run.run_id, "comparison_id": report.comparison_id,
                      "summary": asdict(current.summary), "comparable": report.comparable,
                      "conclusions": [{"code": i.code, "status": i.status, "observation": i.observation,
                                       "action": i.action} for i in report.issue_changes],
                      "exports": [path.relative_to(root).as_posix() for path in exported]})
        if phase == "fixed":
            mark(current, lambda i: i.code == "DUPLICATE_SOP_UID", "可忽略", "合成演示：本次暂忽略，下次改变关联元数据应重新确认。")
        previous = current
    (root / "replay.json").write_text(json.dumps({"synthetic_only": True, "schema": SCHEMA, "steps": steps},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic metadata demo only; never repair patient DICOM.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "replay"):
        item = sub.add_parser(command)
        item.add_argument("--output", type=Path, default=Path(".demo"))
    change = sub.add_parser("switch")
    change.add_argument("--demo-root", type=Path, required=True)
    change.add_argument("--phase", choices=PHASES, required=True)
    args = parser.parse_args(argv)
    try:
        root = switch_phase(args.demo_root, args.phase) if args.command == "switch" else replay(args.output) if args.command == "replay" else create_demo(args.output)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print("SYNTHETIC ONLY - metadata fixtures without pixels or patient identity")
    print("DEMO_ROOT=" + str(root))
    print("SOURCE=" + str(root / "source"))
    print("WORK=" + str(root / "work"))
    if args.command == "switch" and args.phase == "removed":
        print("Removed only owned synthetic CT_001.dcm; switch to baseline to regenerate it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
