"""Real Qt visual states for the UI-only gray pass; synthetic data only."""
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.theme import STYLE
from test_ui_visual import build_sample


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def assert_no_purple(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(image.constBits())
    assert not any(b > r+6 and r > g+2 and b > g+10
                   for r,g,b in zip(data[0::4], data[1::4], data[2::4])), "Purple pixels in actual Qt render"


def capture(window, name, widget=None):
    pixmap = (widget or window).grab()
    assert_no_purple(pixmap)
    output = os.environ.get("DICOM_GRAY_OUTPUT")
    if output:
        assert pixmap.save(str(Path(output) / f"dicom-gray-{name}.png"))
    return pixmap


@pytest.mark.parametrize("width", [1320,980])
def test_default_selection_focus_popup_and_resize(app, tmp_path, make_dicom, width):
    window = MainWindow()
    try:
        window.resize(width,860 if width == 1320 else 660)
        window.show()
        app.processEvents()
        window.path_edit.clearFocus()
        capture(window,f"default-{width}")
    finally:
        window.close()
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.candidate_paths = [Path(record.file_path) for record in result.records]
        window._set_controls_busy(False)
        assert window.start_button.isEnabled()
        window.resize(width,860 if width == 1320 else 660)
        window.show()
        window.path_edit.setFocus()
        app.processEvents()
        capture(window,f"selected-{width}")
        selected_rect = window.table.visualRect(window.table.currentIndex())
        selected = window.table.viewport().grab().toImage().pixelColor(
            selected_rect.right()-12, selected_rect.top()+3)
        assert selected == QColor("#EDF2FE")  # Latest user reference explicitly approves quiet blue selection.
        assert window.view_combo.height() == window.scope_combo.height() == window.path_edit.height() == 40
        assert window.table.horizontalScrollBar().maximum() == 0
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        for combo in window.findChildren(QComboBox):
            assert combo.font().weight() == 400
            assert combo.palette().color(QPalette.ColorRole.Text) == QColor("#292929")
        # Keyboard transfer: only the actual focused dropdown has a dark border.
        window.filter_toggle.setChecked(True)
        window.severity_combo.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        assert window.severity_combo.hasFocus()
        assert window.severity_combo.grab().toImage().pixelColor(20,0) == QColor("#555555")
        assert window.scope_combo.grab().toImage().pixelColor(20,0) == QColor("#D5D9E0")
        capture(window,f"focus-{width}")
        QTest.keyClick(window.severity_combo, Qt.Key.Key_Tab)
        app.processEvents()
        assert window.filter_toggle.hasFocus()
        QTest.keyClick(window.filter_toggle, Qt.Key.Key_Tab)
        app.processEvents()
        assert window.scope_combo.hasFocus()
        assert window.scope_combo.grab().toImage().pixelColor(20,0) == QColor("#555555")
        assert window.severity_combo.grab().toImage().pixelColor(20,0) == QColor("#D5D9E0")
        # Whole table perimeter does not gain an accent border when focused.
        outside = window.table.grab().toImage()
        window.table.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        inside = window.table.grab().toImage()
        assert [outside.pixelColor(x,0) for x in range(outside.width())] == [
            inside.pixelColor(x,0) for x in range(inside.width())]
        capture(window,f"table-focus-{width}")
        for name, combo in (("severity",window.severity_combo),("scope",window.scope_combo)):
            combo.setFocus()
            combo.showPopup()
            app.processEvents()
            QTest.qWait(20)
            popup = combo.view().window()
            assert popup.isVisible()
            assert combo.view().palette().color(QPalette.ColorRole.Highlight) == QColor("#ECECEC")
            rect = combo.view().visualRect(combo.model().index(1,0))
            popup_image = combo.view().viewport().grab().toImage()
            background = popup_image.pixelColor(popup_image.width()-12,rect.center().y())
            assert background == QColor("#FFFFFF")
            capture(window,f"dropdown-{name}-{width}",popup)
            capture(window,f"dropdown-context-{name}-{width}")
            combo.hidePopup()
            app.processEvents()
        window._jump_to_series()
        window.tree.expandAll()
        window.tree.setFocus()
        app.processEvents()
        capture(window,f"tree-{width}")
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        app.processEvents()


def test_system_accent_does_not_leak_into_popups(app):
    original = QPalette(app.palette())
    accent = QPalette(original)
    accent.setColor(QPalette.ColorRole.Highlight,QColor("#6550C9"))
    accent.setColor(QPalette.ColorRole.Accent,QColor("#6550C9"))
    app.setPalette(accent)
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        for combo in window.findChildren(QComboBox):
            for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive):
                assert combo.palette().color(group,QPalette.ColorRole.Highlight) == QColor("#ECECEC")
                assert combo.view().window().palette().color(group,QPalette.ColorRole.Highlight) == QColor("#ECECEC")
        capture(window,"system-accent-isolated")
        assert "#6550C9" not in STYLE and "#F0ECFF" not in STYLE
        assert "QTableView:focus" not in STYLE
    finally:
        window.close()
        app.setPalette(original)
        app.processEvents()


@pytest.mark.parametrize("state,label", [
    ("完成","扫描完成"),("未完成","扫描未完成"),("已取消","扫描已取消"),("失败","扫描失败")])
def test_compact_terminal_state_not_false_success(app,tmp_path,make_dicom,state,label):
    window,result = build_sample(tmp_path,make_dicom)
    try:
        window.show()
        window._inventory_completed(replace(result,scan_run=replace(result.scan_run,status=state)))
        app.processEvents()
        assert window.progress_bar.isHidden()
        assert window.scan_status.isVisible()
        assert window.scan_status.text().startswith(label)
        assert window.statusBar().currentMessage().startswith(label)
        if state != "完成":
            assert "扫描完成" not in window.scan_status.text()
            assert "完整结果" in window.overview_detail.text() or "只覆盖" in window.overview_detail.text()
        capture(window,"terminal-"+state)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("failure", ["discovery","inventory"])
def test_task_failure_is_compact_and_explicit(app,failure):
    window = MainWindow()
    try:
        window.show()
        getattr(window,"_"+failure+"_failed")("合成任务失败")
        app.processEvents()
        assert window.progress_bar.isHidden()
        assert "失败" in window.scan_status.text()
        assert "完成" not in window.scan_status.text()
        assert not window.export_button.isEnabled()
        capture(window,"failure-"+failure)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("width",[1320,980])
def test_full_path_disclosure_copy_and_stale_clear(app,tmp_path,make_dicom,monkeypatch,width):
    window,result = build_sample(tmp_path,make_dicom)
    copies = []
    monkeypatch.setattr(QApplication,"clipboard",staticmethod(lambda: SimpleNamespace(setText=copies.append)))
    try:
        window.resize(width,660)
        window.show()
        record = replace(result.records[3],file_path=str(tmp_path / ("很长的合成目录名称"*12) / "CT_004.dcm"))
        window._display_record(record)
        app.processEvents()
        assert window.detail_path.text() != record.file_path
        assert len(window.detail_path.text()) < 100
        assert window.detail_path.toolTip() == record.file_path
        assert window.full_path_text.toPlainText() == record.file_path
        assert window.full_path_text.isHidden()
        QTest.mouseClick(window.copy_path_button,Qt.MouseButton.LeftButton)
        assert copies == [record.file_path]  # Stub clipboard, never overwrite the user's clipboard.
        window.path_toggle.setChecked(True)
        app.processEvents()
        assert window.full_path_text.isVisible() and window.full_path_text.isReadOnly()
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        capture(window,f"path-expanded-{width}")
        for issue in record.issues:
            window.issue_selector.setCurrentIndex(window.issue_selector.findData(issue.issue_id))
            assert window.issue_description.text() == issue.description
            assert window.issue_impact.text() == "影响："+issue.impact
            assert window.issue_advice.text() == "建议："+issue.recommendation
            assert window.evidence_text.toPlainText()
        window._display_record(None)
        assert window.full_path_text.toPlainText() == ""
        assert window.detail_path.toolTip() == ""
        assert not window.path_toggle.isChecked()
        assert not window.copy_path_button.isEnabled()
        window._inventory_completed(result)
        window._jump_to_series()
        assert window.full_path_text.toPlainText() == ""
        assert not window.copy_path_button.isEnabled()
    finally:
        window.close()
        app.processEvents()
