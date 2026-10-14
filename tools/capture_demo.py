"""Capture actual Qt widgets from the generator-owned replay, no image editing."""
import argparse
import json
import os
from pathlib import Path
import time
from .synthetic_demo import validate_demo


def capture(root, output):
    root, marker = validate_demo(root)
    history = json.loads((root / "replay.json").read_text(encoding="utf-8"))
    if history.get("schema") != marker["schema"] or history.get("synthetic_only") is not True:
        raise ValueError("Generate a synthetic replay before capturing screenshots.")
    output = Path(output).resolve()
    names = ("inventory.png", "hierarchy.png", "handling.png", "comparison.png", "comparison-changed.png", "comparison-reopened.png")
    if any((output / name).exists() for name in names):
        raise ValueError("Screenshot output already exists; choose a new empty directory.")
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.name == "nt" and Path("C:/Windows/Fonts").is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from dicom_inventory.gui.main_window import MainWindow
    from dicom_inventory.gui.comparison_dialogs import ComparisonDialog
    from dicom_inventory.gui.handling_ui import ReviewListDialog
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1440, 1040)  # Public capture viewport, not the application's default/minimum.
    window.comparison.show_report = lambda: None  # Capture the actual dialog explicitly, not a blocking exec.
    window.show()
    def wait():
        deadline = time.monotonic() + 20
        while window._is_busy() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        if window._is_busy():
            raise RuntimeError("Qt demo task did not finish in time.")
        app.processEvents()
    def save(widget, name):
        app.processEvents()
        QTest.qWait(20)
        if hasattr(widget, "details"):
            widget.details.verticalScrollBar().setValue(0)  # Same as user scrolling to the comparison explanation.
            app.processEvents()
        if not widget.grab().save(str(output / name)):
            raise OSError("Could not save actual Qt screenshot.")
    try:
        window.workspace.prepare(root / "work")
        wait()
        window.workspace.load(history["steps"][0]["run_id"])
        wait()
        if window.result is None or Path(window.result.scan_run.source_root).resolve() != (root / "source").resolve():
            raise ValueError("Replay snapshot does not belong to this synthetic source.")
        target = window._path_to_row[str(root / "source" / "CT_001.dcm")]
        window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(target, 0)))
        window.table.setFocus(Qt.FocusReason.MouseFocusReason)
        save(window, "inventory.png")
        window._jump_to_series()
        window.tree.expandAll()
        window.tree.setFocus(Qt.FocusReason.MouseFocusReason)
        save(window, "hierarchy.png")
        window.view_combo.setCurrentIndex(0)
        window.review.refresh()
        wait()
        if window.review.key != window.review.saved_context():
            raise ValueError("Synthetic handling records could not be loaded.")
        dialog = ReviewListDialog(window.review)
        try:
            dialog.show()
            row = next(i for i, issue in enumerate(dialog.model.issues) if issue.code == "SERIES_INCONSISTENT_Rows")
            dialog.table.setCurrentIndex(dialog.model.index(row, 1))
            dialog.table.setFocus(Qt.FocusReason.MouseFocusReason)
            save(dialog, "handling.png")
        finally:
            dialog.close()
            dialog.deleteLater()
        for phase, filename, wanted in (("fixed", "comparison.png", "MISSING_PixelSpacing"),
                                         ("changed", "comparison-changed.png", "DUPLICATE_SOP_UID"),
                                         ("recurrence", "comparison-reopened.png", "MISSING_PixelSpacing")):
            step = next(item for item in history["steps"] if item["phase"] == phase)
            window.comparison.load_report(step["comparison_id"])
            wait()
            if window.comparison.report is None:
                raise ValueError("Stored synthetic comparison could not be loaded.")
            dialog = ComparisonDialog(window.comparison)
            window.comparison.dialog = dialog
            try:
                dialog.resize(1080, 680)
                dialog.show()
                row = next(i for i, item in enumerate(dialog.model.items) if item.code == wanted)
                dialog.table.setCurrentIndex(dialog.proxy.mapFromSource(dialog.model.index(row, 0)))
                save(dialog, filename)
            finally:
                window.comparison.dialog = None
                dialog.close()
                dialog.deleteLater()
        (output / "capture.json").write_text(json.dumps({"synthetic_only": True, "mode": os.environ["QT_QPA_PLATFORM"],
            "screenshots": list(names), "note": "Actual Qt widget captures, no concept image or patient data."}, indent=2), encoding="utf-8")
    finally:
        wait()
        window.close()
        app.processEvents()
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    args = parser.parse_args(argv)
    destination = capture(args.demo_root, args.output)
    print("ACTUAL_QT_SCREENSHOTS=" + str(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
