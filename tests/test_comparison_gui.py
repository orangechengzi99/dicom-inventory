"""Real Qt rechecks, cancellation, persistence, export and neutral comparison views."""
import os
from pathlib import Path
from threading import get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.comparison_dialogs import ComparisonDialog, SavedComparisonsDialog
from dicom_inventory.gui import workers
from dicom_inventory.core import inventory as inv
from dicom_inventory.storage import SnapshotStore, StorageError
from test_comparison import case, before
from test_workbench import wait_until
from test_ui_gray import assert_no_purple
from test_cancellation_gui import gate_stage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ready(app, case, monkeypatch):
    messages = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    baseline = before(case, "已处理待复检")
    window = MainWindow()
    window.show()
    shown = []
    monkeypatch.setattr(window.comparison, "show_report", lambda: shown.append(window.comparison.report))
    window.workspace.prepare(case.work)
    wait_until(app, lambda: not window._is_busy())
    window.comparison.select_baseline(baseline.scan_run.run_id)
    wait_until(app, lambda: not window._is_busy())
    assert window.comparison.recheck_action.isEnabled()
    try:
        yield window, baseline, messages, shown
    finally:
        wait_until(app, lambda: not window._is_busy(), 15)
        if window.comparison.dialog:
            window.comparison.dialog.close()
            window.comparison.dialog = None
        window.close()
        app.processEvents()


def capture(widget, name):
    pixmap = widget.grab()
    assert_no_purple(pixmap)
    directory = os.environ.get("DICOM_COMPARISON_OUTPUT")
    if directory:
        assert pixmap.save(str(Path(directory) / f"dicom-v14-{name}.png"))


@pytest.mark.parametrize("kind", ["resolved", "persistent", "changed"])
@pytest.mark.parametrize("width", [1320, 980])
def test_actual_recheck_and_comparison_views(app, case, ready, monkeypatch, kind, width):
    window, baseline, messages, shown = ready
    if kind == "resolved":
        case.write(0, PixelSpacing=[.7, .7])
    elif kind == "changed":
        case.write(0, Rows=256)
    calls = []
    main = get_ident()
    real_discover, real_read, real_compare = workers.discover_dicom_files, inv._read_record, SnapshotStore.compare_and_save
    def track(name, function):
        def wrapped(*args, **kwargs):
            calls.append((name, get_ident() != main))
            return function(*args, **kwargs)
        return wrapped
    monkeypatch.setattr(workers, "discover_dicom_files", track("discover", real_discover))
    monkeypatch.setattr(inv, "_read_record", track("read", real_read))
    monkeypatch.setattr(SnapshotStore, "compare_and_save", track("compare", real_compare))
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    dialog = None
    try:
        window.resize(width, 860 if width == 1320 else 660)
        window.comparison.recheck_action.trigger()
        wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
        assert not messages and shown and beats
        assert {name for name, background in calls} == {"discover", "read", "compare"}
        assert all(background for name, background in calls)
        assert sum(name == "discover" for name, background in calls) == 1
        report = window.comparison.report
        row = next(item for item in report.issue_changes if item.issue_id == baseline.issues[0].issue_id)
        assert row.status == {"resolved": "已解决", "persistent": "仍然存在", "changed": "需重新确认"}[kind]
        assert row.action == {"resolved": "关闭", "persistent": "", "changed": "重新确认"}[kind]
        assert window.workspace.saved_key == (case.work, report.current_run)
        assert window.table.horizontalScrollBar().maximum() == 0
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        assert [window.table_model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == ["文件", "模态", "矩阵", "状态", "异常摘要"]
        capture(window, f"main-{kind}-{width}")
        dialog = ComparisonDialog(window.comparison)
        window.comparison.dialog = dialog
        dialog.resize(1080 if width == 1320 else 780, 680 if width == 1320 else 500)
        dialog.show()
        app.processEvents()
        for view in range(3):
            dialog.view.setCurrentIndex(view)
            app.processEvents()
            assert dialog.table.horizontalScrollBar().maximum() == 0
            assert dialog.details.horizontalScrollBar().maximum() == 0
            capture(dialog, f"report-{kind}-{view}-{width}")
        dialog.view.setCurrentIndex(0)
        dialog.view.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        capture(dialog, f"report-focus-{kind}-{width}")
        dialog.status.showPopup()
        app.processEvents()
        capture(dialog.status.view().window(), f"report-popup-{kind}-{width}")
        dialog.status.hidePopup()
        dialog.status.setCurrentText(row.status)
        assert dialog.proxy.rowCount() >= 1
        assert "处理状态" in dialog.details.toPlainText()
    finally:
        timer.stop()
        if dialog:
            window.comparison.dialog = None
            dialog.close()
            dialog.deleteLater()


@pytest.mark.parametrize("stage,occurrence", [("发现文件", 4), ("读取元数据", 2), ("检查空间位置", 1)])
def test_cancelled_real_recheck_is_saved_but_never_closes(app, case, ready, monkeypatch, stage, occurrence):
    window, baseline, messages, shown = ready
    case.write(0, PixelSpacing=[.7, .7])
    reached, release, calls = gate_stage(monkeypatch, stage, occurrence)
    try:
        window.comparison.start_recheck()
        wait_until(app, reached.is_set)
        QTest.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
        release.set()
        wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
        assert window.result.scan_run.status == "已取消"
        report = window.comparison.report
        assert not report.comparable and all(item.observation == "无法确认" and not item.action for item in report.issue_changes)
        assert case.store.get_comparison(report.comparison_id) == report
        assert next(iter(case.store.get_handling(baseline.scan_run.run_id).values())).state == "已处理待复检"
        assert not window.start_button.isEnabled() and not window.cancel_button.isVisible()
        if stage == "读取元数据":
            capture(window, "cancelled-recheck-main")
            dialog = ComparisonDialog(window.comparison)
            dialog.show()
            app.processEvents()
            capture(dialog, "cancelled-recheck-report")
            dialog.close()
            dialog.deleteLater()
    finally:
        release.set()
        wait_until(app, lambda: not window._is_busy(), 15)


def test_empty_rediscovery_is_compared_without_false_resolution(app, case, ready):
    window, baseline, messages, shown = ready
    for path in case.paths:
        path.unlink()  # Only these explicitly created synthetic test files.
    window.comparison.start_recheck()
    wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
    assert window.result.summary.file_count == 0 and window.result.scan_run.status == "完成"
    assert window.comparison.report.issue_changes[0].observation == "无法确认"
    assert window.comparison.report.issue_changes[0].action == ""


def test_comparison_failure_preserves_result_and_can_retry(app, case, ready, monkeypatch):
    window, baseline, messages, shown = ready
    case.write(0, PixelSpacing=[.7, .7])
    real_compare = SnapshotStore.compare_and_save
    monkeypatch.setattr(SnapshotStore, "compare_and_save", lambda *args: (_ for _ in ()).throw(StorageError("合成对比失败，状态未变更")))
    window.comparison.start_recheck()
    wait_until(app, lambda: messages and not window._is_busy(), 15)
    current = window.result
    assert current is not None and window.comparison.report is None
    assert case.store.list_comparisons() == ()
    assert next(iter(case.store.get_handling(baseline.scan_run.run_id).values())).state == "已处理待复检"
    monkeypatch.setattr(SnapshotStore, "compare_and_save", real_compare)
    window.comparison.compare_current()
    try:
        wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
    except AssertionError:
        pytest.fail(f"复检重试未完成：busy={window._is_busy()}；提示={messages!r}；日志={window.error_text.toPlainText()}")
    assert window.result == current and window.comparison.report.issue_changes[0].action == "关闭"
    assert len(case.store.list_scans()) == 2


def test_all_four_exports_and_offline_report_reopen(app, case, ready, monkeypatch):
    window, baseline, messages, shown = ready
    case.write(0, PixelSpacing=[.7, .7])
    window.comparison.start_recheck()
    wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
    report = window.comparison.report
    for kind in ("files", "issues", "series", "comparison"):
        window._begin_export(case.output, kind)
        wait_until(app, lambda: not window._is_busy(), 15)
    assert len(list(case.output.glob("*.csv"))) == 4
    assert (case.output / "comparison.csv").exists()
    reopened = MainWindow()
    reopened.show()
    monkeypatch.setattr(reopened.comparison, "show_report", lambda: None)
    try:
        reopened.workspace.prepare(case.work)
        wait_until(app, lambda: not reopened._is_busy())
        with monkeypatch.context() as patch:
            patch.setattr(inv, "_read_record", lambda *_: pytest.fail("History must not read DICOM"))
            reopened.comparison.load_report(report.comparison_id)
            wait_until(app, lambda: reopened.comparison.report is not None and not reopened._is_busy(), 15)
        assert reopened.comparison.report == report
        assert reopened.workspace.is_history and "历史快照" in reopened.scan_status.text()
        assert "未重新读取" in reopened.overview_detail.text()
        assert not reopened.start_button.isEnabled()
        dialog = SavedComparisonsDialog(reopened, case.store.list_comparisons())
        dialog.show()
        app.processEvents()
        assert dialog.selected_id() == report.comparison_id
        capture(dialog, "history-selector")
        dialog.close()
        dialog.deleteLater()
    finally:
        wait_until(app, lambda: not reopened._is_busy(), 15)
        reopened.close()


def test_closed_record_remains_read_only_and_audit_accessible(app, case, ready):
    window, baseline, messages, shown = ready
    case.write(0, PixelSpacing=[.7, .7])
    window.comparison.start_recheck()
    wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
    window.workspace.load(baseline.scan_run.run_id)
    wait_until(app, lambda: not window._is_busy(), 15)
    issue_id = baseline.issues[0].issue_id
    editor = window.review.create_editor((issue_id,), window)
    assert editor is not None and editor.read_only
    editor.show()
    wait_until(app, lambda: not window._is_busy(), 15)
    try:
        assert editor.note.isReadOnly() and not editor.state.isEnabled() and editor.save.isHidden()
        assert "已关闭" in editor.history.toPlainText()
        initial = case.store.get_history(baseline.scan_run.run_id, issue_id)
        editor.submit()
        assert case.store.get_history(baseline.scan_run.run_id, issue_id) == initial
        capture(editor, "closed-audit-readonly")
    finally:
        editor.reject()
        editor.deleteLater()


def test_report_export_and_audit_actions_use_background_tasks(app, case, ready, monkeypatch):
    window, baseline, messages, shown = ready
    case.write(0, PixelSpacing=[.7, .7])
    window.comparison.start_recheck()
    wait_until(app, lambda: window.comparison.report is not None and not window._is_busy(), 15)
    dialog = ComparisonDialog(window.comparison)
    window.comparison.dialog = dialog
    dialog.show()
    try:
        app.processEvents()
        QTest.mouseClick(dialog.audit_button, Qt.MouseButton.LeftButton)
        assert window._is_busy() and not dialog.close_button.isEnabled()
        wait_until(app, lambda: not window._is_busy(), 15)
        assert "处理审计" in dialog.details.toPlainText() and "已关闭" in dialog.details.toPlainText()
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(case.output))
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        wait_until(app, lambda: not window._is_busy(), 15)
        assert (case.output / "comparison.csv").exists() and dialog.close_button.isEnabled()
    finally:
        window.comparison.dialog = None
        dialog.close()
        dialog.deleteLater()


def test_repeated_baseline_load_while_old_dialogs_are_deferred_deleted(app, ready):
    window, baseline, messages, shown = ready
    for _ in range(25):
        # Reproduce the observed lock-order scenario, not just constructing
        # an idle window: real SQLite load/QThread retirement overlaps a
        # previous comparison's combo/delegate/widget deferred destruction.
        from dicom_inventory.core.comparison import compare_scans
        window.comparison.report = compare_scans(baseline, baseline)
        window.comparison.report_baseline = baseline
        window.comparison.report_current = baseline
        dialog = ComparisonDialog(window.comparison)
        dialog.show()
        app.processEvents()
        dialog.close()
        dialog.deleteLater()
        window.comparison.select_baseline(baseline.scan_run.run_id)
        wait_until(app, lambda: not window._is_busy(), 15)
        assert window.comparison.baseline == baseline
        assert window.comparison.recheck_action.isEnabled()
    assert not messages
