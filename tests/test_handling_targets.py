"""Actionable file targets and pointer/keyboard cues in review dialogs."""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QItemSelectionModel
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QTableView, QAbstractItemView, QStyle, QStyleOptionViewItem

from dicom_inventory.gui.handling_ui import ReviewListDialog, ReviewModel, polish
from dicom_inventory.gui.review_targets import TargetFilesModel, file_preview
from test_handling_gui import app, messages, ready, editor_for, shot
from test_review_focus import logical_pixel
from test_workbench import wait_until


def issue_row(dialog, code):
    return next(i for i, issue in enumerate(dialog.model.issues) if issue.code == code)


@pytest.mark.parametrize("width,height", [(1060,640),(660,480)])
def test_handling_targets_and_mouse_frames(app, ready, width, height):
    window, result, _ = ready
    dialog = ReviewListDialog(window.review)
    try:
        dialog.resize(width, height)
        dialog.show()
        app.processEvents()
        assert dialog.table.font().families() == window.font().families()
        row = issue_row(dialog, "MISSING_PixelSpacing")
        issue = result.issues[row]
        expected = Path(issue.affected_files[0]).relative_to(window.source_root).as_posix()
        assert dialog.model.index(row,3).data() == expected
        for column in range(5):
            index = dialog.model.index(row,column)
            dialog.table.scrollTo(index)
            app.processEvents()
            rect = dialog.table.visualRect(index)
            assert dialog.table.viewport().rect().contains(rect.center())
            QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            app.processEvents()
            assert dialog.selected_ids() == (issue.issue_id,)
            option = QStyleOptionViewItem()
            option.widget = dialog.table
            option.state |= QStyle.StateFlag.State_HasFocus
            dialog.table.itemDelegate().initStyleOption(option,index)
            assert not option.state & QStyle.StateFlag.State_HasFocus
            image = dialog.table.viewport().grab().toImage()
            assert logical_pixel(image,rect.right()-6,rect.top()+2) == QColor("#EDF2FE")
        assert dialog.table.horizontalScrollBar().maximum() == 0
        assert dialog.targets.model.rowCount() == 1
        assert dialog.targets.path.toPlainText() == issue.affected_files[0]
        assert dialog.edit_button.isEnabled()
        QTest.mouseClick(dialog.targets.copy_button, Qt.MouseButton.LeftButton)
        assert app.clipboard().text() == issue.affected_files[0]
        assert "隐私" in dialog.targets.copy_button.toolTip()
        dialog.table.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(dialog.table, Qt.Key.Key_Down)
        app.processEvents()
        assert dialog.table.hasFocus() and dialog.table.property("reviewKeyboardFocus")
        current_rect = dialog.table.visualRect(dialog.table.currentIndex())
        image = dialog.table.viewport().grab().toImage()
        assert logical_pixel(image,current_rect.center().x(),current_rect.bottom()-3) == QColor("#667085")
        shot(dialog, f"handling-targets-{width}.png")
        assert window.result == result
    finally:
        dialog.close()


def test_bulk_scope_matches_selected_issue_ids_and_complete_union(app, ready):
    window, result, _ = ready
    dialog = ReviewListDialog(window.review)
    editor = None
    try:
        dialog.show()
        dup = issue_row(dialog,"DUPLICATE_SOP_UID")
        missing = issue_row(dialog,"MISSING_PixelSpacing")
        dialog.table.selectRow(dup)
        assert "2 个文件" in dialog.model.index(dup,3).data()
        assert dialog.targets.model.rowCount() == 2
        dialog.table.selectionModel().select(dialog.model.index(missing,0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        ids = dialog.selected_ids()
        assert len(ids) == 2
        wanted = set(result.issues[dup].affected_files) | set(result.issues[missing].affected_files)
        assert set(dialog.targets.model.paths) == wanted
        assert len(wanted) == 3 and "去重" in dialog.targets.title.text()
        editor = editor_for(app,window,ids)
        assert "2 项问题" in editor.targets_summary.text() and "3 个关联文件" in editor.targets_summary.text()
        assert not editor.targets.isVisible()
        QTest.keyClick(editor.targets_toggle,Qt.Key.Key_Space)
        app.processEvents()
        QTest.qWait(20)
        assert editor.targets.isVisible() and set(editor.targets.model.paths) == wanted
        assert editor.targets.path.height() <= editor.targets.height()
        shot(editor.targets_dialog,"handling-bulk-targets.png")
        editor.targets_dialog.close()
        assert not editor.targets.isVisible()
        shot(editor,"handling-bulk-editor.png")
        editor.state.setCurrentText("已确认")
        editor.submit()
        wait_until(app,lambda:not window._is_busy())
        assert all(window.review.records[key].state == "已确认" for key in ids)
        assert all(record.state == "待确认" for key,record in window.review.records.items() if key not in ids)
        assert window.result == result
    finally:
        if editor:
            editor.accept()
        dialog.close()


@pytest.mark.parametrize("view",[0,1])
def test_locate_target_in_main_review_clears_obstructing_filters(app,ready,view):
    window, result, _ = ready
    window.show()
    window.view_combo.setCurrentIndex(view)
    window.search_edit.setText("no-match-for-target")
    window.anomaly_checkbox.setChecked(True)
    dialog = ReviewListDialog(window.review)
    try:
        dialog.show()
        row = issue_row(dialog,"MISSING_PixelSpacing")
        dialog.table.selectRow(row)
        path = result.issues[row].affected_files[0]
        QTest.mouseClick(dialog.targets.locate_button,Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not dialog.isVisible()
        assert window.search_edit.text() == "" and not window.anomaly_checkbox.isChecked()
        assert window.detail_title.text() == Path(path).name
        assert window.full_path_text.toPlainText() == path
        assert window.result == result
    finally:
        dialog.close()


def test_same_basename_long_paths_and_all_group_members_remain_distinct(app,ready):
    window, result, _ = ready
    paths = tuple(str(window.source_root / f"子目录-{i:04}-完整路径" / "same.dcm") for i in range(5000))
    issue = replace(result.issues[0],affected_files=paths,scope_kind="series")
    model = ReviewModel((issue,),window.review.records,window,window.source_root)
    text = model.index(0,3).data()
    assert "5000 个文件" in text and "子目录-0000" in text and "子目录-0001" in text
    files = TargetFilesModel(window)
    files.replace(paths,window.source_root)
    assert files.rowCount() == 5000
    assert files.index(4999).data(Qt.ItemDataRole.UserRole) == paths[-1]
    assert "子目录-4999" in files.index(4999).data()
    assert files.index(0).data() != files.index(1).data()
    assert files.index(0).data(Qt.ItemDataRole.ToolTipRole) == paths[0]
    assert file_preview((),window.source_root) == "无关联文件"


def test_common_dialog_polish_removes_readonly_table_frames_only(app,ready):
    window, _, _ = ready
    dialog = QDialog(window)
    readonly = QTableView(dialog)
    readonly.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    model = QStandardItemModel(dialog)
    model.appendRow(QStandardItem("合成只读问题"))
    readonly.setModel(model)
    editable = QTableView(dialog)
    polish(dialog,window)
    assert readonly.property("reviewNoItemFrame")
    assert not editable.property("reviewNoItemFrame")
    option = QStyleOptionViewItem()
    option.state |= QStyle.StateFlag.State_HasFocus
    readonly.itemDelegate().initStyleOption(option,model.index(0,0))
    assert not option.state & QStyle.StateFlag.State_HasFocus
    dialog.close()
