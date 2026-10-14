"""Reference assets on the real workbench, with native keyboard/disclosure QA."""
import os
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QComboBox
from shiboken6 import delete

from dicom_inventory.gui import reference_icons as icons
from dicom_inventory.gui.reference_controls import IconTextButton, TickCheckBox
from dicom_inventory.gui.reference_typography import resolve
from test_ui_visual import build_sample
from test_ui_gray import assert_no_purple


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width,height", [(1440, 1040), (1320, 860), (980, 660)])
def test_pyside_reference_card_typography_and_native_controls(app, tmp_path, make_dicom, monkeypatch, width, height):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.candidate_paths = [Path(record.file_path) for record in result.records]
        window._set_controls_busy(False)
        window.resize(width, height)
        window.show()
        app.processEvents()
        QTest.qWait(30)
        assert window.width() == width and window.height() == height
        assert isinstance(window.choose_button, IconTextButton)
        assert isinstance(window.start_button, IconTextButton)
        assert isinstance(window.export_button, IconTextButton)
        assert isinstance(window.anomaly_checkbox, TickCheckBox)
        assert isinstance(window.severity_combo, QComboBox)
        assert window.font().families() == resolve("ui")
        assert window.parameter_values["矩阵"].font().families() == resolve("mono")
        assert window.issue_description.font().pixelSize() == 14
        assert window.summary_icon.pixmap() and not window.summary_icon.pixmap().isNull()
        assert window.scan_status.text() == "扫描完成"
        assert not window.scan_notes.isVisible()
        assert "未评估" in window.scan_notes_toggle.text()
        assert not window.affected_panel.isVisible()
        assert not window.technical_panel.isVisible()
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        assert window.table.horizontalScrollBar().maximum() == 0
        card = window.splitter.widget(1).widget().findChild(type(window.issue_panel), "detailBody")
        assert card is not None
        assert card.height() == card.heightForWidth(card.width())
        if height == 1040:
            assert card.height() < window.splitter.widget(1).viewport().height()
            assert window.splitter.widget(1).verticalScrollBar().maximum() == 0
        output = os.environ.get("DICOM_PYSIDE_OUTPUT")
        if output:
            assert window.grab().save(str(Path(output)/f"pyside-files-{width}x{height}.png"))
        # Native disabled labels remain visible, rather than blank custom buttons.
        window.start_button.setEnabled(False)
        assert window.start_button.text() == "开始扫描"
        if output:
            assert window.start_button.grab().save(str(Path(output)/f"pyside-disabled-{width}.png"))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: "")
        window.choose_button.setFocus()
        QTest.keyClick(window.choose_button, Qt.Key.Key_Space)
        assert window.source_root is not None
        window.anomaly_checkbox.setFocus()
        QTest.keyClick(window.anomaly_checkbox, Qt.Key.Key_Space)
        assert window.anomaly_checkbox.isChecked()
        QTest.keyClick(window.anomaly_checkbox, Qt.Key.Key_Space)
        assert not window.anomaly_checkbox.isChecked()
        window.affected_toggle.setFocus()
        QTest.keyClick(window.affected_toggle, Qt.Key.Key_Space)
        assert window.affected_panel.isVisible() and window.affected_list.isVisible()
        assert window.affected_model.rowCount() == 2
        window.technical_toggle.setFocus()
        QTest.keyClick(window.technical_toggle, Qt.Key.Key_Space)
        assert window.uid_toggle.isVisible() and window.metadata_toggle.isVisible() and window.evidence_toggle.isVisible()
        assert not window.uid_text.isVisible() and not window.metadata_text.isVisible() and not window.evidence_text.isVisible()
        window.evidence_toggle.setChecked(True)
        assert window.evidence_text.isVisible() and window.evidence_text.toPlainText()
        window.scan_notes_toggle.setFocus()
        QTest.keyClick(window.scan_notes_toggle, Qt.Key.Key_Space)
        assert window.overview_title.isVisible() and window.overview_detail.isVisible()
        assert len(window.export_button.menu().actions()) == 4
        assert window.issue_description.text() == result.records[3].issues[0].description
        image = window.grab()
        assert_no_purple(image)
        if output:
            assert image.save(str(Path(output)/f"pyside-disclosures-{width}x{height}.png"))
    finally:
        window.close()
        # These synchronous tests do not enter app.exec(). Dispose their own
        # native widget tree while QApplication still exists, not at shutdown.
        assert not window._is_busy()
        delete(window)
        app.processEvents()


@pytest.mark.parametrize("svg", [icons.OK, icons.WARN, icons.ERROR, icons.INFO])
@pytest.mark.parametrize("dpr", [100, 200])
def test_reference_glyph_explicit_target_is_not_clipped(app, svg, dpr):
    source = icons._render(svg, 16, dpr)
    target = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.drawPixmap(QRect(4,4,16,16), source)
    painter.end()
    visible = [(x,y) for x in range(24) for y in range(24) if target.pixelColor(x,y).alpha()>30]
    assert len(visible)>40
    assert 4 <= min(x for x,y in visible) <= 6
    assert 17 <= max(x for x,y in visible) <= 19
    assert 4 <= min(y for x,y in visible) <= 6
    assert 17 <= max(y for x,y in visible) <= 19


def test_unassessed_and_cancelled_remain_explicit(app, tmp_path, make_dicom):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.show()
        # A completed synthetic result is injected directly, not delivered by
        # a worker. Leave the path editor before injecting cancellation so its
        # editingFinished on close cannot request a real rediscovery.
        window.table.setFocus()
        app.processEvents()
        partial = replace(result, scan_run=replace(result.scan_run, status="已取消"))
        window._inventory_completed(partial)
        app.processEvents()
        assert "已取消" in window.scan_status.text()
        assert window.scan_notes.isVisible()
        assert "未完成" in window.overview_title.text()
        assert "扫描完成" not in window.scan_status.text()
        window._display_record(None)
        assert not window.affected_toggle.isVisible()
    finally:
        window.close()
        assert not window._is_busy()
        delete(window)
        app.processEvents()
