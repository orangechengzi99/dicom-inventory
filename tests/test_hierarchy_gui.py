from __future__ import annotations
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtTest import QTest, QAbstractItemModelTester
from PySide6.QtWidgets import QApplication
from pydicom.uid import generate_uid
from dicom_inventory.core import inventory_files
from dicom_inventory.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def result_with_series(tmp_path, make_dicom, *, conflict=False):
    source = tmp_path / "source"
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    files = [make_dicom(source / f"{i+1:02}-序列样本.dcm", study_uid=generate_uid() if conflict and i == 3 else study,
                        series_uid=series, overrides={"FrameOfReferenceUID": frame, "ImagePositionPatient": [0, 0, i],
                        "InstanceNumber": i+1, "Rows": 256 if i == 2 else 512}) for i in range(4)]
    unrelated = make_dicom(source / "05-另一检查.dcm")
    return source, inventory_files([*files, unrelated], source_root=source), files


def select_series(window, path):
    window.view_combo.setCurrentIndex(1)
    node = window.hierarchy_model.path_nodes[str(path.resolve())].parent_node
    index = window.hierarchy_proxy.mapFromSource(window.hierarchy_model.index_for_node(node))
    window.tree.setCurrentIndex(index)
    window.tree.expand(index)
    return node, index


@pytest.fixture
def model_messages():
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    try:
        yield messages
        assert not any("FAIL!" in message for message in messages), messages
    finally:
        qInstallMessageHandler(previous)


def test_tree_model_structure_details_and_problem_navigation(app, tmp_path, make_dicom, model_messages):
    source, result, files = result_with_series(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    tester = QAbstractItemModelTester(window.hierarchy_model, QAbstractItemModelTester.FailureReportingMode.Warning)
    window._inventory_completed(result)
    assert window.hierarchy_model.rowCount() == 2
    assert len(window.hierarchy_model.path_nodes) == 5
    node, index = select_series(window, files[0])
    assert node.kind == "series" and len(node.files) == 4
    assert window.hierarchy_proxy.rowCount(index) == 4
    assert "256 | 512" in window.metadata_text.toPlainText()
    assert "SLICE_SPACING: 已检查" in window.metadata_text.toPlainText()
    assert window.issue_selector.count() == 1
    assert window.affected_model.rowCount() == 4
    assert window.issue_description.text() == node.issues[0].description
    assert window.evidence_toggle.text() == "检查证据"
    assert window.evidence_text.toPlainText()
    path = window.affected_model.index(2).data()
    window._jump_to_affected(window.affected_model.index(2))
    assert window._current_record.file_path == path and window.view_combo.currentIndex() == 1
    window._jump_to_series()
    assert window._current_node.kind == "series"
    window.view_combo.setCurrentIndex(0)
    assert window._current_record is not None
    window.table.setCurrentIndex(window.proxy_model.mapFromSource(window.table_model.index(0, 0)))
    window._jump_to_series()
    assert window._current_node.kind == "series" and window._current_record is None
    window.close()
    app.processEvents()


def test_scopes_and_filters_both_views_empty_and_reset(app, tmp_path, make_dicom):
    source, result, files = result_with_series(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    window._inventory_completed(result)
    node, _ = select_series(window, files[0])
    scope = window.scope_combo.findData(node.payload.group_key)
    window.scope_combo.setCurrentIndex(scope)
    assert window.proxy_model.rowCount() == 4
    assert window.hierarchy_proxy.rowCount() == 1
    window.view_combo.setCurrentIndex(0)
    assert window.proxy_model.rowCount() == 4
    window.severity_combo.setCurrentIndex(1)  # no ERROR
    window.view_combo.setCurrentIndex(1)
    assert window.hierarchy_proxy.rowCount() == 0
    assert window._current_record is None and window._current_node is None
    assert window.affected_model.rowCount() == 0
    assert window.issue_selector.count() == 0
    window.severity_combo.setCurrentIndex(0)
    window._reset_results()
    assert window.scope_combo.count() == 1 and window.hierarchy_model.rowCount() == 0
    assert window.metadata_text.toPlainText() == ""
    assert window.issue_selector.count() == 0
    assert not window.series_jump.isEnabled()
    window._inventory_completed(result)
    assert window.proxy_model.rowCount() == 5  # Old scope cannot survive a new scan.
    window.close()


def test_conflicting_uid_in_two_study_branches_no_duplicate_files(app, tmp_path, make_dicom):
    source, result, files = result_with_series(tmp_path, make_dicom, conflict=True)
    window = MainWindow()
    window.source_root = source
    window._inventory_completed(result)
    assert len(result.studies) == 3 and len(result.series) == 2
    assert len(window.hierarchy_model.path_nodes) == 5
    first_node, _ = select_series(window, files[0])
    assert len(first_node.files) == 3
    assert "全标识范围文件数: 4" in window.metadata_text.toPlainText()
    last_node, _ = select_series(window, files[3])
    assert len(last_node.files) == 1 and last_node.payload is first_node.payload
    assert last_node.highest_severity.value == "ERROR"
    assert "未评估" in window.metadata_text.toPlainText()
    assert window.affected_model.rowCount() == 4
    window.close()


@pytest.mark.parametrize("width", [1320, 980])
def test_real_tree_layout_keyboard_and_no_cell_widgets(app, tmp_path, make_dicom, width):
    source, result, files = result_with_series(tmp_path, make_dicom)
    window = MainWindow()
    window.source_root = source
    window.path_edit.setText(str(source))
    window._inventory_completed(result)
    window.resize(width, 860)
    window.show()
    node, index = select_series(window, files[0])
    window.tree.setFocus()
    app.processEvents()
    QTest.keyClick(window.tree, Qt.Key.Key_Down)
    assert window._current_record.file_path == str(files[0].resolve())
    window.tree.setCurrentIndex(index)
    app.processEvents()
    app.processEvents()
    assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
    assert window.tree.isVisible() and not window.table.isVisible()
    window.filter_toggle.setChecked(True)
    app.processEvents()
    assert window.scope_combo.geometry().right() <= window.splitter.widget(0).width()
    assert window.export_button.mapTo(window.centralWidget(), window.export_button.rect().bottomRight()).x() < window.centralWidget().width()
    assert window.export_button.isEnabled()
    for label in (window.issue_description, window.issue_impact, window.issue_advice):
        assert label.height() >= label.heightForWidth(label.width())
    for row in range(window.hierarchy_proxy.rowCount(index)):
        assert window.tree.indexWidget(window.hierarchy_proxy.index(row, 1, index)) is None
    output = os.environ.get("DICOM_VISUAL_OUTPUT")
    if output:
        assert window.grab().save(str(Path(output) / f"dicom-v12-tree-{width}.png"))
    window.close()
    app.processEvents()
