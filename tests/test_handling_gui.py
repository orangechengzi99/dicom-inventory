"""Actual Qt review/restart/failure/keyboard flows on anonymous generated input."""
import csv
import os
from pathlib import Path
import threading
import time
import pytest
from PySide6.QtCore import QTimer, Qt, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
from PySide6.QtTest import QTest
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.handling_ui import ReviewListDialog
from dicom_inventory.storage import SnapshotStore, StorageError
from test_ui_visual import build_sample
from test_ui_gray import assert_no_purple
from test_workbench import wait_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    result = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *args: result.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    return result


@pytest.fixture
def ready(app, tmp_path, make_dicom, messages):
    window, result = build_sample(tmp_path, make_dicom)
    work = tmp_path / "工作库"
    work.mkdir()
    window.workspace.prepare(work)
    wait_until(app, lambda: not window._is_busy())
    window.workspace.save()
    wait_until(app, lambda: not window._is_busy())
    assert window.review.records
    try:
        yield window, result, work
    finally:
        wait_until(app, lambda: not window._is_busy())
        if window.review.editor:
            window.review.editor.accept()
        window.close()
        app.processEvents()


def editor_for(app, window, ids=None):
    editor = window.review.create_editor(ids or (window.issue_selector.currentData(),))
    assert editor is not None
    editor.show()
    wait_until(app, lambda: not window._is_busy())
    return editor


def shot(widget, name):
    pixmap = widget.grab()
    assert_no_purple(pixmap)
    output = os.environ.get("DICOM_HANDLING_OUTPUT")
    if output:
        assert pixmap.save(str(Path(output) / name))


def test_single_edit_keyboard_background_and_real_restart(app, ready, monkeypatch, messages):
    window, result, work = ready
    selected = window.issue_selector.currentData()
    editor = editor_for(app, window)
    assert "创建" in editor.history.toPlainText()
    assert "首次批次" in editor.info.text()
    editor.state.setFocus()
    QTest.keyClick(editor.state, Qt.Key.Key_Down)
    assert editor.state.currentText() == "已确认"
    editor.note.setPlainText("人工核对：重复导出需后续确认")
    calls = []
    main = threading.get_ident()
    original = SnapshotStore.update_handling
    def tracked(self, *args, **kwargs):
        calls.append(threading.get_ident() != main)
        time.sleep(.03)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SnapshotStore, "update_handling", tracked)
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    try:
        QTest.mouseClick(editor.save, Qt.MouseButton.LeftButton)
        assert window._is_busy() and not editor.save.isEnabled()
        editor.reject()
        assert editor.isVisible()  # Can't dismiss an in-flight write.
        wait_until(app, lambda: not window._is_busy())
        assert editor.result() == QDialog.DialogCode.Accepted
        assert window.review.records[selected].state == "已确认"
        assert window.handling_label.text() == "人工状态：已确认"
        assert window.result == result
        assert calls == [True] and beats
    finally:
        timer.stop()
    restored = MainWindow()
    try:
        restored.workspace.prepare(work)
        wait_until(app, lambda: not restored._is_busy())
        restored.workspace.load(result.scan_run.run_id)
        wait_until(app, lambda: not restored._is_busy())
        assert restored.review.records[selected].note == "人工核对：重复导出需后续确认"
        assert restored.review.records[selected].state == "已确认"
        assert restored.result == result and restored.workspace.is_history
        assert not messages
    finally:
        restored.close()


def test_bulk_selection_saves_all_keeps_original_issue_counts(app, ready):
    window, result, work = ready
    dialog = ReviewListDialog(window.review)
    dialog.show()
    dialog.table.selectRow(0)
    dialog.table.selectionModel().select(dialog.model.index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert len(dialog.selected_ids()) == 2
    ids = dialog.selected_ids()
    editor = editor_for(app, window, ids)
    assert not editor.replace_note.isChecked() and not editor.note.isEnabled()
    editor.state.setCurrentText("可忽略")
    editor.submit()
    assert "理由" in editor.error.text() and not window._is_busy()
    editor.replace_note.setChecked(True)
    editor.note.setPlainText("演示数据问题，保留证据")
    editor.submit()
    wait_until(app, lambda: not window._is_busy())
    records = SnapshotStore(work).get_handling(result.scan_run.run_id)
    assert all(records[key].state == "可忽略" for key in ids)
    assert all(records[key].state == "待确认" for key in records if key not in ids)
    assert window.result.summary == result.summary
    assert SnapshotStore(work).load(result.scan_run.run_id).issues == result.issues
    dialog.close()


def test_save_failure_keeps_form_text_and_retry_succeeds(app, ready, monkeypatch):
    window, result, work = ready
    editor = editor_for(app, window)
    editor.state.setCurrentText("已处理待复检")
    editor.note.setPlainText("请保留这段尚未保存的中文输入")
    original = SnapshotStore.update_handling
    def fail(*args):
        raise StorageError("模拟权限失败；请检查工作目录后重试。")
    monkeypatch.setattr(SnapshotStore, "update_handling", fail)
    editor.submit()
    wait_until(app, lambda: not window._is_busy())
    assert editor.isVisible() and editor.save.isEnabled()
    assert editor.note.toPlainText() == "请保留这段尚未保存的中文输入"
    assert "重试" in editor.error.text()
    assert window.review.records[editor.ids[0]].state == "待确认"
    assert editor.note.toPlainText() not in window.error_text.toPlainText()
    monkeypatch.setattr(SnapshotStore, "update_handling", original)
    editor.submit()
    wait_until(app, lambda: not window._is_busy())
    assert not editor.isVisible()
    assert SnapshotStore(work).get_handling(result.scan_run.run_id)[editor.ids[0]].state == "已处理待复检"


def test_concurrent_editor_conflict_keeps_input(app, ready):
    window, result, work = ready
    editor = editor_for(app, window)
    record = window.review.records[editor.ids[0]]
    SnapshotStore(work).update_handling(result.scan_run.run_id, {record.issue_id:(record.track_key, record.revision)},
                                     "可忽略", "另一窗口先保存")
    editor.note.setPlainText("本窗口尚未保存")
    editor.state.setCurrentText("已确认")
    editor.submit()
    wait_until(app, lambda: not window._is_busy())
    assert "修订" in editor.error.text() and editor.note.toPlainText() == "本窗口尚未保存"
    assert SnapshotStore(work).get_handling(result.scan_run.run_id)[record.issue_id].note == "另一窗口先保存"
    editor.accept()


def test_cancel_dirty_editor_decline_preserves_text(app, ready, monkeypatch):
    window, _, _ = ready
    editor = editor_for(app, window)
    editor.note.setPlainText("尚未保存的输入")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert editor.isVisible() and editor.note.toPlainText() == "尚未保存的输入"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert not editor.isVisible()
    assert window.review.records[editor.ids[0]].note == ""


def test_long_note_validation_no_closed_option(app, ready):
    window, _, _ = ready
    editor = editor_for(app, window)
    assert [editor.state.itemText(i) for i in range(editor.state.count())] == ["待确认", "已确认", "可忽略", "已处理待复检"]
    editor.note.setPlainText("x" * 2001)
    editor.submit()
    assert "2000" in editor.error.text() and not window._is_busy()
    editor.accept()


def test_saved_gui_issue_export_reads_persisted_status_not_cached(app, ready, tmp_path):
    window, result, work = ready
    key = window.issue_selector.currentData()
    record = window.review.records[key]
    SnapshotStore(work).update_handling(result.scan_run.run_id, {key:(record.track_key, record.revision)}, "已确认", "导出真实库状态")
    assert window.review.records[key].state == "待确认"  # Intentionally stale cache.
    output = tmp_path / "exports"
    output.mkdir()
    window.anomaly_checkbox.setChecked(True)
    window._begin_export(output, "issues")
    wait_until(app, lambda: not window._is_busy())
    with (output / "issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(result.issues)
    row = next(item for item in rows if item["IssueID"] == key)
    assert row["HumanNote"] == "导出真实库状态" and row["HandlingState"] == "已确认"


@pytest.mark.parametrize("width,height", [(1320,860), (980,660)])
def test_actual_gray_review_dialogs_focus_popup_and_resize(app, ready, width, height):
    window, result, work = ready
    window.resize(width, height)
    window.show()
    app.processEvents()
    assert [window.table_model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == ["文件","模态","矩阵","状态","异常摘要"]
    assert window.handling_label.font().pixelSize() == 12
    assert window.handling_button.isEnabled()
    shot(window, f"dicom-handling-main-{width}.png")
    dialog = ReviewListDialog(window.review)
    dialog.resize(860 if width == 1320 else 660, 460)
    dialog.show()
    dialog.table.selectRow(0)
    dialog.table.setFocus()
    app.processEvents()
    assert dialog.edit_button.isEnabled()
    shot(dialog, f"dicom-handling-list-{width}.png")
    dialog.close()
    editor = editor_for(app, window)
    editor.note.setPlainText("人工核对记录；原始文件未修改，等待后续复检。")
    editor.state.setCurrentText("已处理待复检")
    editor.note.setFocus()
    app.processEvents()
    assert editor.note.font().pixelSize() == 14
    assert editor.save.geometry().height() == editor.cancel.geometry().height()
    shot(editor, f"dicom-handling-editor-{width}.png")
    editor.state.showPopup()
    app.processEvents()
    shot(editor.state.view().window(), f"dicom-handling-popup-{width}.png")
    editor.state.hidePopup()
    editor.submit()
    wait_until(app, lambda: not window._is_busy())
    app.processEvents()
    shot(window, f"dicom-handling-saved-{width}.png")


def test_unsaved_and_new_scan_do_not_show_old_states(app, tmp_path, make_dicom, ready):
    window, result, _ = ready
    assert window.workspace.review_action.isEnabled()
    window._reset_results()
    assert not window.workspace.review_action.isEnabled() and not window.review.records
    other, new_result = build_sample(tmp_path / "another", make_dicom)
    try:
        assert not other.handling_button.isEnabled()
        assert "保存扫描" in other.handling_label.text()
    finally:
        other.close()


def test_failed_refresh_disables_stale_edit_then_retry_recovers(app, ready, monkeypatch, messages):
    window, result, _ = ready
    original = SnapshotStore.get_handling
    def fail(*args):
        raise StorageError("模拟工作库暂时不可用，请检查后重试。")
    monkeypatch.setattr(SnapshotStore, "get_handling", fail)
    window.review.refresh()
    wait_until(app, lambda: not window._is_busy())
    assert window.result == result and window.review.key is None
    assert not window.handling_button.isEnabled() and window.workspace.review_action.isEnabled()  # Menu remains a retry route.
    assert "未就绪" in window.handling_label.text() and messages
    monkeypatch.setattr(SnapshotStore, "get_handling", original)
    window.review.refresh()
    wait_until(app, lambda: not window._is_busy())
    assert window.handling_button.isEnabled() and window.workspace.review_action.isEnabled()

