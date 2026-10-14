"""Visual contract for the V1.2 neutral GUI; all inputs are synthetic."""
from dataclasses import replace
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pydicom.uid import generate_uid

from dicom_inventory.core import inventory_files
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.display_helpers import FileDelegate, status_color
from dicom_inventory.gui.status_delegate import StatusDelegate


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def build_sample(tmp_path, make_dicom):
    root = tmp_path / "合成胸部CT"
    study, series, frame, dup = (generate_uid() for _ in range(4))
    paths = []
    for i in range(8):
        fields = {"FrameOfReferenceUID": frame, "InstanceNumber": i+1, "ImagePositionPatient": [0, 0, i*1.25]}
        if i == 2:
            fields["PixelSpacing"] = None
        paths.append(make_dicom(root / f"CT_{i+1:03}.dcm", study_uid=study, series_uid=series,
                                sop_uid=dup if i in (3, 4) else None, overrides=fields))
    invalid = root / "invalid_01.dcm"
    invalid.write_bytes(b"anonymous synthetic invalid DICOM")
    paths.append(invalid)
    result = inventory_files(paths, source_root=root)
    window = MainWindow()
    window.source_root = root
    window.path_edit.setText(str(root))
    window._inventory_completed(result)
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(3, 0)))
    duplicate_index = window.issue_selector.findData(next(issue.issue_id for issue in result.records[3].issues if issue.code == "DUPLICATE_SOP_UID"))
    window.issue_selector.setCurrentIndex(duplicate_index)
    return window, result


@pytest.mark.parametrize("width,height", [(1320,860), (980,860), (1320,660), (980,660)])
@pytest.mark.parametrize("view", [0, 1])
def test_neutral_layout_columns_fonts_disclosure_and_resize(app, tmp_path, make_dicom, width, height, view):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.resize(width, height)
        window.show()
        app.processEvents()
        assert [window.table_model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == [
            "文件", "模态", "矩阵", "状态", "异常摘要"]
        assert [i for i in range(window.table_model.columnCount()) if not window.table.isColumnHidden(i)] == list(range(5))
        assert isinstance(window.table.itemDelegateForColumn(0), FileDelegate)
        assert isinstance(window.table.itemDelegateForColumn(3), StatusDelegate)
        assert window.table.verticalHeader().defaultSectionSize() == 52
        assert window.table_model.index(3, 0).data(Qt.ItemDataRole.UserRole).startswith("序列 ")
        assert window.table_model.index(8, 0).data(Qt.ItemDataRole.UserRole) == "未归组"
        assert "重复实例 UID" in window.table_model.index(3, 4).data()
        assert window.parameter_values["像素间距"].text() == "0.7 × 0.7 mm"
        assert window.parameter_values["实例号"].text() == "4"
        assert window.parameter_values["矩阵"].text() == "512 × 512"
        assert window.start_button.text() == "开始扫描"
        export_actions = window.export_button.menu().actions()
        assert [action.text() for action in export_actions] == [
            "文件明细 CSV", "问题清单 CSV", "序列汇总 CSV", "复检对比 CSV"]
        assert all(action.isEnabled() for action in export_actions[:3])
        assert export_actions[3] is window.comparison_export_action
        assert not export_actions[3].isEnabled()  # No recheck report for this direct synthetic result.
        assert window.issue_selector.count() >= 1
        assert not window.metadata_toggle.isChecked() and not window.uid_toggle.isChecked()
        assert not window.evidence_toggle.isChecked()
        assert result.records[3].sop_instance_uid not in window.issue_description.text()
        assert result.records[3].sop_instance_uid in window.evidence_text.toPlainText()
        assert window.issue_description.font().weight() == 400
        assert window.parameter_values["矩阵"].font().weight() == 400
        assert window.detail_title.font().weight() == 600
        assert window.overview_title.font().pixelSize() <= 18
        QTest.keyClick(window.anomaly_checkbox, Qt.Key.Key_Space)
        assert window.anomaly_checkbox.isChecked()
        QTest.keyClick(window.anomaly_checkbox, Qt.Key.Key_Space)
        assert not window.anomaly_checkbox.isChecked()
        if view:
            window._jump_to_series()
            window.tree.expandAll()
            window.tree.setFocus()
            assert " × " in window.parameter_values["像素间距"].text()
            assert "未提供 mm" not in window.parameter_values["像素间距"].text()
        else:
            window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(3, 0)))
            window.issue_selector.setCurrentIndex(window.issue_selector.findData(next(issue.issue_id for issue in result.records[3].issues if issue.code == "DUPLICATE_SOP_UID")))
            window.table.setFocus()
        app.processEvents()
        QTest.qWait(20)
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        assert window.table.horizontalScrollBar().maximum() == 0
        for label in (window.issue_description, window.issue_impact, window.issue_advice, window.detail_path):
            assert label.height() >= label.heightForWidth(label.width())
        export_rect = window.export_button.rect()
        assert window.export_button.mapTo(window.centralWidget(), export_rect.bottomRight()).x() < window.centralWidget().width()
        assert window.start_button.isEnabled() is False  # No candidate discovery in this direct synthetic result.
        for row in range(window.proxy_model.rowCount()):
            for col in range(5):
                assert window.table.indexWidget(window.proxy_model.index(row, col)) is None
        output = os.environ.get("DICOM_VISUAL_OUTPUT")
        if output:
            kind = "tree" if view else "files"
            assert window.grab().save(str(Path(output) / f"dicom-neutral-{kind}-{width}x{height}.png"))
        # All disclosure content stays available, including checks and complete identifiers.
        for toggle, text in ((window.metadata_toggle, window.metadata_text), (window.uid_toggle, window.uid_text),
                             (window.evidence_toggle, window.evidence_text)):
            toggle.setChecked(True)
            app.processEvents()
            assert text.isVisible()
            assert text.toPlainText()
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("width", [1320,980])
def test_long_issue_text_and_multiple_issues_remain_available(app, tmp_path, make_dicom, width):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        record = result.records[3]
        long_text = "用于验证长文本可读性及安全边界的合成检查说明。" * 80
        changed = replace(record.issues[0], description=long_text, impact=long_text, recommendation=long_text)
        record.issues = (changed, *record.issues[1:])
        window._display_record(record)
        window.resize(width, 660)
        window.show()
        app.processEvents()
        assert long_text in window.issue_description.text()
        assert long_text in window.issue_impact.text()
        assert long_text in window.issue_advice.text()
        assert window.splitter.widget(1).verticalScrollBar().maximum() > 0
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        window._display_record(None)
        assert all(label.text() == "—" for label in window.parameter_values.values())
        assert window.evidence_text.toPlainText() == ""
        assert window.issue_selector.count() == 0
    finally:
        window.close()
        app.processEvents()


def test_status_fixed_slots_and_neutral_contrast(app, tmp_path, make_dicom):
    window, _ = build_sample(tmp_path, make_dicom)
    try:
        window.show()
        window.table.setFocus()
        app.processEvents()
        for row in (3, 8):  # WARNING triangle and ERROR circle occupy the same slot.
            window.table.scrollTo(window.proxy_model.index(row, 3))
            app.processEvents()
            image = window.table.viewport().grab().toImage()
            rect = window.table.visualRect(window.proxy_model.index(row, 3))
            expected = "#F0A422" if row == 3 else "#E5484D"  # Reference SVG glyph colors, not text colors.
            from PySide6.QtGui import QColor
            color = QColor(expected)
            points = [(x,y) for x in range(rect.left()+10, rect.left()+27)
                      for y in range(rect.center().y()-8, rect.center().y()+9)
                      if 0 <= x < image.width() and 0 <= y < image.height()
                      and sum(abs(image.pixelColor(x,y).getRgb()[c]-color.getRgb()[c]) for c in range(3)) < 50]
            assert points
            assert min(x for x,y in points) - rect.left() in range(10,14)
        def luminance(value):
            channels = [int(value[i:i+2],16)/255 for i in (1,3,5)]
            return sum(weight*(x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4)
                       for weight,x in zip((.2126,.7152,.0722),channels))
        for foreground, background in (("#666666","#FFFFFF"),("#292929","#ECECEC"),
                                       ("#BC3030","#FFFFFF"),("#995500","#FFFFFF"),("#FFFFFF","#29292E")):
            l1,l2 = sorted((luminance(foreground),luminance(background)))
            assert (l2+.05)/(l1+.05) >= 4.5
    finally:
        window.close()
        app.processEvents()
