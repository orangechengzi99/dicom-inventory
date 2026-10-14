"""Actual Qt reference-layout contracts; synthetic data, never patient images."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_ui_visual import build_sample
from test_ui_gray import assert_no_purple


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width,height", [(1440, 960), (1320, 860), (980, 660)])
def test_reference_layout_real_render_and_preserved_controls(app, tmp_path, make_dicom, width, height):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.resize(width, height)
        window.candidate_paths = [Path(record.file_path) for record in result.records]
        window._set_controls_busy(False)
        window.show()
        app.processEvents()
        QTest.qWait(20)
        assert window.width() == width
        assert window.height() == height
        assert window.view_tabs.isVisible() and window.view_combo.isHidden()
        assert window.view_tabs.tabText(0) == "文件明细"
        assert window.view_tabs.tabText(1) == "Study / Series"
        assert window.export_button.isVisible()
        assert window.export_button.mapTo(window, window.export_button.rect().topLeft()).y() < window.view_tabs.mapTo(window, window.view_tabs.rect().topLeft()).y()
        assert window.stats["anomaly_file_count"].text() == str(result.summary.anomaly_file_count)
        assert window.stats["unique_instance_count"].text() == str(result.summary.unique_instance_count)
        assert not window.advanced_filters.isVisible()
        assert window.search_edit.isVisible() and window.anomaly_checkbox.isVisible()
        assert window.issue_panel.property("tone") == "WARNING"
        assert window.issue_description.isVisible() and window.issue_advice.isVisible()
        assert window.table.horizontalScrollBar().maximum() == 0
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        image = window.grab()
        assert_no_purple(image)
        output = os.environ.get("DICOM_REFERENCE_OUTPUT")
        if output:
            assert image.save(str(Path(output) / f"reference-files-{width}x{height}.png"))
        window.view_tabs.setCurrentIndex(1)
        assert window.view_combo.currentIndex() == window.result_stack.currentIndex() == 1
        window.tree.expandAll()
        app.processEvents()
        if output:
            assert window.grab().save(str(Path(output) / f"reference-tree-{width}x{height}.png"))
        window.view_combo.setCurrentIndex(0)
        assert window.view_tabs.currentIndex() == 0
        window.filter_toggle.setFocus()
        QTest.keyClick(window.filter_toggle, Qt.Key.Key_Space)
        assert window.advanced_filters.isVisible()
        assert window.scope_combo.isVisible() and window.type_combo.isVisible()
        assert window.workspace.button.isVisible() and window.log_toggle.isVisible()
        assert len(window.export_button.menu().actions()) == 4
    finally:
        window.close()
        app.processEvents()


def test_search_is_view_only_and_keeps_hierarchy_and_jump_accessible(app, tmp_path, make_dicom):
    window, result = build_sample(tmp_path, make_dicom)
    try:
        window.show()
        window.search_edit.setText("ct_004")
        assert window.proxy_model.rowCount() == 1
        assert window._current_record.file_path.endswith("CT_004.dcm")
        assert len(window.result.records) == result.summary.file_count == 9
        assert window.stats["file_count"].text() == "9"
        window.view_tabs.setCurrentIndex(1)
        assert window.hierarchy_proxy.rowCount() > 0
        target = window.hierarchy_model.path_nodes[result.records[3].file_path]
        assert window.hierarchy_proxy.mapFromSource(window.hierarchy_model.index_for_node(target)).isValid()
        other = window.hierarchy_model.path_nodes[result.records[2].file_path]
        assert not window.hierarchy_proxy.mapFromSource(window.hierarchy_model.index_for_node(other)).isValid()
        window.search_edit.setText("no_such_file")
        assert window.proxy_model.rowCount() == 0 and window._current_record is None
        window.search_edit.clear()
        window.view_tabs.setCurrentIndex(0)
        window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(3, 0)))
        affected = window.affected_model.index(1)
        expected = affected.data()
        window.search_edit.setText("CT_004")
        window._jump_to_affected(affected)
        assert window.search_edit.text() == ""
        assert window._current_record.file_path == expected
        window.type_combo.setCurrentIndex(2)
        window.filter_toggle.setChecked(False)
        assert "1" in window.filter_toggle.text()
        assert window.proxy_model.rowCount() == 2
        window._display_record(result.records[-1])
        assert window.issue_panel.property("tone") == "ERROR"
        window._display_record(None)
        assert window.issue_panel.property("tone") == "neutral"
    finally:
        window.close()
        app.processEvents()
