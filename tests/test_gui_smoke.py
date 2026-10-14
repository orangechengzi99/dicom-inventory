from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dicom_inventory.gui.main_window import MainWindow


def test_main_window_constructs() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.windowTitle() == "DICOM 数据清点"
    assert not window.start_button.isEnabled()
    assert not window.export_button.isEnabled()

    window.close()
    app.processEvents()
