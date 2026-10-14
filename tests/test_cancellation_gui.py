"""Exercise real Qt workers and buttons; event gates only pause synthetic work."""
import os
from pathlib import Path
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from dicom_inventory.gui.main_window import MainWindow
from dicom_inventory.gui.workers import ScanWorker
from dicom_inventory.core import inventory as inv
from test_cancellation import scan_sample, assert_partial, stop_after
from test_workbench import wait_until
from test_ui_gray import assert_no_purple


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def capture(window, name, widget=None):
    pixmap = (widget or window).grab()
    assert_no_purple(pixmap)
    directory = os.environ.get("DICOM_CANCEL_OUTPUT")
    if directory:
        assert pixmap.save(str(Path(directory) / f"dicom-cancel-{name}.png"))


def gate_stage(monkeypatch, stage, occurrence=1):
    reached, release = Event(), Event()
    main = get_ident()
    calls = []
    real = ScanWorker._checkpoint
    def checkpoint(worker, current):
        real(worker, current)
        if current == stage:
            calls.append(get_ident())
            if len(calls) == occurrence:
                assert get_ident() != main
                reached.set()
                assert release.wait(15), "Synthetic gate was not released"
                real(worker, current)
    monkeypatch.setattr(ScanWorker, "_checkpoint", checkpoint)
    return reached, release, calls


def test_clean_partial_rows_and_tree_do_not_claim_success(app, scan_sample):
    root, paths = scan_sample
    result = stop_after(root, paths, 1)
    window = MainWindow()
    try:
        window.source_root = root
        window._inventory_completed(result)
        window.show()
        app.processEvents()
        assert window.table_model.index(0, 3).data() == "未评估"
        assert window.table_model.index(0, 4).data() == "序列检查未完成"
        assert window.issue_heading.text() == "检查未完成"
        capture(window, "clean-partial")
        window._jump_to_series()
        window.tree.expandAll()
        app.processEvents()
        assert "未评估" in window.hierarchy_model.index(0, 1).data()
        capture(window, "clean-partial-tree")
    finally:
        window.close()


@pytest.mark.parametrize("stage,count,occurrence", [("发现文件", 0, 4), ("读取元数据", 3, 4),
                                                   ("检查空间位置", 6, 1), ("汇总分层", 6, 1)])
@pytest.mark.parametrize("width", [1320, 980])
def test_real_cancel_and_partial_presentation(app, scan_sample, monkeypatch, stage, count, occurrence, width):
    root, paths = scan_sample
    reached, release, calls = gate_stage(monkeypatch, stage, occurrence)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window = MainWindow()
    window.resize(width, 860 if width == 1320 else 660)
    window.show()
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    try:
        window._begin_discovery(root)
        if stage != "发现文件":
            wait_until(app, lambda: not window._is_busy())
            QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        wait_until(app, reached.is_set)
        assert window.cancel_button.isVisible() and window.cancel_button.isEnabled()
        assert not window.start_button.isEnabled() and window.progress_bar.isVisible()
        window.cancel_button.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        assert window.cancel_button.height() == window.start_button.height() == 40
        if stage == "读取元数据":
            capture(window, f"running-{width}")
        QTest.keyClick(window.cancel_button, Qt.Key.Key_Space)
        assert not window.cancel_button.isEnabled() and "停止" in window.cancel_button.text()
        assert window.result is None
        window.close()
        assert window.isVisible() and messages  # Close is rejected while a native thread is still busy.
        if stage == "读取元数据":
            capture(window, f"stopping-{width}")
        assert beats
        release.set()
        wait_until(app, lambda: not window._is_busy())
        assert_partial(window.result, count)
        assert "已取消" in window.scan_status.text() and "未完成" in window.overview_title.text()
        assert not window.progress_bar.isVisible() and not window.cancel_button.isVisible()
        assert not window.candidate_paths and not window.start_button.isEnabled()
        assert window.export_button.isEnabled() and window.choose_button.isEnabled()
        assert [window.table_model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == ["文件", "模态", "矩阵", "状态", "异常摘要"]
        assert all(window.table.isColumnHidden(i) for i in range(5, window.table_model.columnCount()))
        assert window.table.horizontalScrollBar().maximum() == 0
        assert window.splitter.widget(1).horizontalScrollBar().maximum() == 0
        if stage == "读取元数据":
            capture(window, f"partial-{width}")
            for label, combo in (("view", window.view_combo), ("scope", window.scope_combo)):
                combo.setFocus(Qt.FocusReason.TabFocusReason)
                app.processEvents()
                capture(window, f"focus-{label}-{width}")
                combo.showPopup()
                app.processEvents()
                capture(window, f"popup-{label}-{width}", combo.view().window())
                combo.hidePopup()
    finally:
        release.set()
        wait_until(app, lambda: not window._is_busy())
        timer.stop()
        window.close()


def test_partial_save_reopen_and_fresh_discovery(app, scan_sample, tmp_path, make_dicom, monkeypatch):
    root, paths = scan_sample
    from dicom_inventory.gui import workers
    real_discover = workers.discover_dicom_files
    discoveries = []
    def discover(*args, **kwargs):
        discoveries.append(args[0])
        return real_discover(*args, **kwargs)
    monkeypatch.setattr(workers, "discover_dicom_files", discover)
    reached, release, calls = gate_stage(monkeypatch, "读取元数据", 4)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    work = tmp_path / "work"
    work.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    window = MainWindow()
    window.show()
    reopened = None
    try:
        window._begin_discovery(root)
        wait_until(app, lambda: not window._is_busy())
        window._start_inventory()
        wait_until(app, reached.is_set)
        QTest.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
        release.set()
        wait_until(app, lambda: not window._is_busy())
        result = window.result
        window._cancel_scan()  # A late click cannot change a finished batch or start a new task.
        assert window.result is result
        window.workspace.prepare(work)
        assert not window.cancel_button.isVisible()
        wait_until(app, lambda: not window._is_busy())
        window.workspace.save()
        wait_until(app, lambda: not window._is_busy())
        for kind in ("files", "series", "issues"):
            window._begin_export(output, kind)
            assert not window.cancel_button.isVisible()
            wait_until(app, lambda: not window._is_busy())
        assert len(list(output.glob("partial_*.csv"))) == 3
        assert len(list(output.glob("*.scan.json"))) == 3
        window.close()
        reopened = MainWindow()
        reopened.show()
        reopened.workspace.prepare(work)
        wait_until(app, lambda: not reopened._is_busy())
        with monkeypatch.context() as patch:
            patch.setattr(inv, "_read_record", lambda *_: pytest.fail("History must not reread DICOM"))
            reopened.workspace.load(result.scan_run.run_id)
            wait_until(app, lambda: not reopened._is_busy())
        assert reopened.result == result and "已取消" in reopened.scan_status.text()
        assert "历史" in reopened.scan_status.text()
        assert not reopened.start_button.isEnabled()
        make_dicom(root / "new-after-cancel.dcm")
        reopened.path_edit.setFocus()
        QTest.keyClick(reopened.path_edit, Qt.Key.Key_Return)
        wait_until(app, lambda: not reopened._is_busy())
        assert len(reopened.candidate_paths) == len(paths) + 1
        assert discoveries == [root, root]  # Enter/disable must not re-enter and spawn a second worker.
        reopened._start_inventory()
        wait_until(app, lambda: not reopened._is_busy())
        assert reopened.result.scan_run.status == "完成"
        assert reopened.result.summary.file_count == 7
    finally:
        release.set()
        for item in (window, reopened):
            if item is not None:
                wait_until(app, lambda: not item._is_busy())
                item.close()
