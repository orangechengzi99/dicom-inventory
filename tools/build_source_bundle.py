"""Build a reviewed allowlist source ZIP; never publish or initialize Git."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT_FILES = ("README.md", ".gitignore", "requirements.txt", "requirements-dev.txt", "pytest.ini")
DOC_FILES = ("USER_GUIDE.md", "TECHNICAL_REFERENCE.md", "DEMO_GUIDE.md", "PROJECT_OVERVIEW.md", "RELEASE_NOTES.md", "PUBLISH_CHECKLIST.md", "VALIDATION.md")
SCREEN_FILES = ("inventory.png", "hierarchy.png", "handling.png", "comparison.png", "comparison-changed.png", "comparison-reopened.png")
PRIVATE_PATH = re.compile(r"[A-Z]:[\\/](?:Users|GPT codex|PycharmProjects)[\\/]", re.IGNORECASE)


def public_files(project):
    project = Path(project).resolve()
    selected = [project / name for name in ROOT_FILES]
    selected += [project / "docs" / name for name in DOC_FILES]
    for directory in ("dicom_inventory", "tests", "tools"):
        folder = project / directory
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            raise ValueError("Source directories cannot be linked.")
        selected += list(folder.rglob("*.py"))
    selected.append(project / "dicom_inventory" / "gui" / "chevron.svg")
    selected += [project / "docs" / "screenshots" / name for name in SCREEN_FILES]
    # The user chooses licensing separately. Include only an existing license,
    # never write/infer an MIT grant on their behalf.
    if (project / "LICENSE").exists():
        selected.append(project / "LICENSE")
    result = []
    for path in sorted(set(selected)):
        if not path.is_file():
            raise ValueError("Required public file is missing: " + path.relative_to(project).as_posix())
        current = path
        while current != project.parent:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("Linked public files/directories are refused.")
            if current == project:
                break
            current = current.parent
        if not path.resolve().is_relative_to(project) or path.stat().st_nlink != 1:
            raise ValueError("Linked or out-of-project source file is refused.")
        if path.suffix.lower() in (".py", ".md", ".txt", ".ini") or path.name in (".gitignore", "LICENSE"):
            if PRIVATE_PATH.search(path.read_text(encoding="utf-8-sig")):
                raise ValueError("Private local path in public text: " + path.relative_to(project).as_posix())
        elif path.suffix.lower() == ".png":
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Screenshot is not PNG.")
        result.append(path)
    return project, tuple(result)


def build_bundle(project, output):
    project, files = public_files(project)  # Preflight before creating output.
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = "" if index == 0 else f"-{index}"
        destination = output / f"DICOMInventory-v1.4-source{suffix}.zip"
        try:
            stream = destination.open("xb")
            break
        except FileExistsError:
            index += 1
    manifest = {"format": "DICOMInventorySourceBundle/1", "application_version": "V1.4",
                "license_selected": (project / "LICENSE").is_file(),
                "scope": "Source, tests, portable docs and synthetic screenshots only; not an executable or a published repository.",
                "files": []}
    with stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            content = path.read_bytes()
            relative = path.relative_to(project).as_posix()
            archive.writestr("DICOMInventory/" + relative, content)
            manifest["files"].append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        archive.writestr("DICOMInventory/DELIVERY_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    # Independently reopen the finished archive and verify every advertised byte.
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP verification failed; do not publish this archive.")
        for item in manifest["files"]:
            content = archive.read("DICOMInventory/" + item["path"])
            if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Manifest verification failed; do not publish this archive.")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parents[1]
    try:
        destination = build_bundle(project, args.output)
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print("SOURCE_BUNDLE=" + str(destination))
    print("Not published. Review the license and PUBLISH_CHECKLIST before any GitHub upload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
