"""History baseline and real recheck workflow; all storage/comparison runs in workers."""
from pathlib import Path
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QDialog, QMessageBox
from ..storage import SnapshotStore, StorageError
from ..core.comparison import source_key, path_key
from ..core.quality import RULE_VERSION
from .storage_ui import SavedScansDialog, SavedScanListModel
from .display_helpers import show_scan_state
from .worker_lifecycle import BackgroundWorker


class ComparisonWorker(BackgroundWorker):
    completed = Signal(str, object)
    failed = Signal(str)

    def __init__(self, directory, source, mode, args=()):
        super().__init__()
        self.directory, self.source, self.mode, self.args = directory, source, mode, args

    @Slot()
    def run(self):
        try:
            store = SnapshotStore(self.directory, self.source)
            if self.mode == "baselines":
                value = tuple(scan for scan in store.list_scans() if scan.status == "完成")
            elif self.mode == "baseline":
                value = store.load(self.args[0])
                if value.scan_run.status != "完成":
                    raise StorageError("复检基线必须是已保存的完整批次。")
            elif self.mode == "history":
                value = store.list_comparisons()
            elif self.mode == "audit":
                value = store.get_history(*self.args)
            else:
                if self.mode == "compare":
                    baseline_id, current = self.args
                    ids = {scan.run_id for scan in store.list_scans()}
                    if current.scan_run.run_id not in ids:
                        store.save(current)
                    elif store.load(current.scan_run.run_id) != current:
                        raise StorageError("当前结果与保存批次不同，未比较或改变人工状态。")
                    report = store.compare_and_save(baseline_id, current.scan_run.run_id)
                else:
                    report = store.get_comparison(self.args[0])
                baseline, current = store.load(report.baseline_run), store.load(report.current_run)
                records = store.get_handling(current.scan_run.run_id)
                index = {path_key(record.file_path, current.scan_run.source_root): record for record in current.records}
                value = (report, baseline, current, records, index)
            self.completed.emit(self.mode, value)
        except StorageError as error:
            prefix = "复检对比未完成；扫描可能已保存，当前结果保留，可核对记录后重试。\n" if self.mode == "compare" else ""
            self.failed.emit(prefix + str(error))
        except Exception:
            self.failed.emit("复检对比未完成；当前结果仍保留。扫描可能已经保存，请核对工作目录后重试对比。")
        finally:
            self._finish_in_owner_thread()


class ComparisonController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.thread = self.worker = None
        self.pending = None
        self.baseline = self.baseline_key = None
        self.report = self.report_baseline = self.report_current = None
        self.record_index = {}
        self.dialog = None
        self.armed = False
        self.pending_scan = None
        menu = window.workspace.button.menu()
        menu.addSeparator()
        self.choose_action = menu.addAction("选择复检基线…", self.choose_baseline)
        self.recheck_action = menu.addAction("按基线重新扫描", self.start_recheck)
        self.compare_action = menu.addAction("对比当前扫描…", self.compare_current)
        self.view_action = menu.addAction("查看本次复检对比…", self.show_report)
        self.history_action = menu.addAction("打开已保存复检对比…", self.open_history)
        self.update_controls(False)

    @property
    def busy(self):
        return self.thread is not None

    def reset_result(self):
        self.report = self.report_baseline = self.report_current = None
        self.record_index = {}
        self.pending_scan = None

    def workspace_changed(self):
        if self.baseline_key and self.baseline_key[0] != self.window.workspace.directory:
            self.baseline = self.baseline_key = None
            self.armed = False
            self.reset_result()

    def update_controls(self, busy):
        directory = self.window.workspace.directory
        ready = directory is not None and self.baseline is not None and self.baseline_key[0] == directory
        self.choose_action.setEnabled(not busy and directory is not None)
        self.recheck_action.setEnabled(not busy and ready and self.baseline.scan_run.rule_version == RULE_VERSION)
        self.compare_action.setEnabled(not busy and ready and self.window.result is not None and
            self.window.result.scan_run.run_id != self.baseline.scan_run.run_id)
        self.view_action.setEnabled(not busy and self.report is not None)
        self.history_action.setEnabled(not busy and directory is not None)
        self.window.comparison_export_action.setEnabled(not busy and self.report is not None)
        if self.dialog:
            self.dialog.set_busy(busy)

    def choose_baseline(self):
        self._start("baselines")

    def select_baseline(self, run_id):
        self._start("baseline", (run_id,))

    def _select_baseline(self, scans):
        if not scans:
            QMessageBox.information(self.window, "没有完整基线", "先保存一次完整扫描，再选择其作为复检基线。")
            return
        dialog = SavedScansDialog(self.window, scans)
        dialog.setWindowTitle("选择复检基线")
        dialog.source_path.setAccessibleName("基线来源目录")
        dialog.layout().itemAt(0).widget().setText("选择完整历史批次作为基线；重扫会重新发现来源目录，完整可比时验证待复检问题。")
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_id():
                self.select_baseline(dialog.selected_id())
        finally:
            dialog.deleteLater()

    def start_recheck(self):
        if self.window._is_busy() or self.baseline is None:
            return
        if self.baseline.scan_run.rule_version != RULE_VERSION:
            QMessageBox.warning(self.window, "基线规则不同", "该基线与当前扫描规则不同，请选择同版本基线；不会用新规则自动关闭旧问题。")
            return
        run = self.window.result.scan_run if self.window.result else None
        if run and self.window.workspace.saved_key != (self.window.workspace.directory, run.run_id):
            if QMessageBox.question(self.window, "当前结果尚未保存", "重新扫描将替换当前未保存的结果。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.armed = True
        self.pending_scan = None
        self.window._begin_discovery(Path(self.baseline.scan_run.source_root))
        if self.window.discovery_thread is None:
            self.armed = False

    def scan_ready(self, result):
        if self.armed:
            self.pending_scan = result

    def scan_failed(self):
        self.armed = False
        self.pending_scan = None

    def discovery_finished(self):
        if not self.armed:
            return
        if self.pending_scan is not None:
            self.scan_finished()  # Discovery cancellation produced a zero-read partial snapshot.
        else:
            self.window._start_inventory(allow_empty=True)

    def scan_finished(self):
        if self.armed and self.pending_scan is not None:
            current = self.pending_scan
            self.armed = False
            self.pending_scan = None
            self._start("compare", (self.baseline.scan_run.run_id, current))

    def compare_current(self):
        current = self.window.result
        if self.baseline is None or current is None or self.window._is_busy():
            return
        if current.scan_run.run_id == self.baseline.scan_run.run_id:
            QMessageBox.information(self.window, "需要新扫描", "当前批次就是基线，请按基线重新扫描后比较。")
            return
        self._start("compare", (self.baseline.scan_run.run_id, current))

    def open_history(self):
        self._start("history")

    def _select_history(self, items):
        from .comparison_dialogs import SavedComparisonsDialog
        if not items:
            QMessageBox.information(self.window, "暂无复检对比", "先选择基线并完成一次复检，结果将保存到工作目录。")
            return
        dialog = SavedComparisonsDialog(self.window, items)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_id():
                self.load_report(dialog.selected_id())
        finally:
            dialog.deleteLater()

    def load_report(self, key):
        if self.window._is_busy():
            return
        run = self.window.result.scan_run if self.window.result else None
        if run and self.window.workspace.saved_key != (self.window.workspace.directory, run.run_id):
            if QMessageBox.question(self.window, "当前结果尚未保存", "打开历史对比会替换当前未保存的结果。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self._start("load_report", (key,))

    def show_report(self):
        if self.report is None or self.window._is_busy():
            return
        from .comparison_dialogs import ComparisonDialog
        dialog = ComparisonDialog(self)
        self.dialog = dialog
        try:
            dialog.exec()
        finally:
            self.dialog = None
            dialog.deleteLater()

    def read_audit(self):
        if self.dialog is None or self.dialog.model.kind != 0:
            return
        item = self.dialog.selected_item()
        if item:
            run = self.report.baseline_run if item.baseline_files else self.report.current_run
            self._start("audit", (run, item.issue_id))

    def _start(self, mode, args=()):
        if self.window._is_busy() or self.window.workspace.directory is None:
            return
        thread = QThread(self)
        worker = ComparisonWorker(self.window.workspace.directory, self.window.source_root, mode, args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        self.thread, self.worker = thread, worker
        self.window._set_controls_busy(True)
        self.window.statusBar().showMessage("正在后台保存并比较复检，完整可比时验证待复检问题…" if mode == "compare" else "正在读取复检工作记录…")
        thread.start()

    @Slot(str, object)
    def _completed(self, mode, value):
        if mode == "baselines":
            self.pending = lambda: self._select_baseline(value)
        elif mode == "baseline":
            self.baseline = value
            self.baseline_key = (self.window.workspace.directory, value.scan_run.run_id)
            self.reset_result()
            self.window.statusBar().showMessage("已选择复检基线：" + SavedScanListModel.display_time(value.scan_run.finished_at) + "；从工作记录按基线重新扫描")
            self.recheck_action.setToolTip("重新发现并扫描：" + value.scan_run.source_root)
        elif mode == "history":
            self.pending = lambda: self._select_history(value)
        elif mode == "audit":
            if self.dialog:
                self.dialog.details.appendPlainText("\n\n处理审计（当前轨迹）：\n" + "\n\n".join(
                    f"{SavedScanListModel.display_time(item.changed_at)}  修订 {item.revision}\n"
                    f"{item.old_state or '创建'} → {item.new_state}\n原备注：{item.old_note}\n新备注：{item.new_note}\n批次：{item.run_id}"
                    for item in value))
        else:
            report, baseline, current, records, index = value
            window = self.window
            if mode == "load_report":
                self.armed = False
                window._reset_results()
                window.source_root = Path(current.scan_run.source_root)
                window.path_edit.setText(str(window.source_root))
                window.candidate_paths = []
                window._inventory_completed(current)
                window.workspace.is_history = True
                show_scan_state(window, "历史快照 · " + window.scan_status.text(), "WARNING" if current.scan_run.status != "完成" else "")
                window.overview_detail.setText("历史对比中的扫描快照，未重新读取原始文件。" + window.overview_detail.text())
            self.baseline, self.baseline_key = baseline, (window.workspace.directory, baseline.scan_run.run_id)
            self.report, self.report_baseline, self.report_current = report, baseline, current
            self.record_index = index
            window.workspace.saved_key = (window.workspace.directory, current.scan_run.run_id)
            window.workspace.snapshot_label.setText("历史快照 · 已保存" if mode == "load_report" else "本次扫描 · 已保存")
            window.review.records, window.review.key = records, window.review.saved_context()
            closed = sum(item.action == "关闭" for item in report.issue_changes)
            window.statusBar().showMessage(f"复检对比已保存 · 关闭 {closed} 项" if report.comparable else "复检对比已保存 · 无法完整比较，未关闭问题")
            self.pending = self.show_report

    @Slot(str)
    def _failed(self, message):
        self.pending = None
        self.armed = False
        self.window.statusBar().showMessage(message)
        self.window.error_text.insertPlainText("复检记录：" + message + "\n")
        QMessageBox.warning(self.window, "复检对比未完成", message)

    @Slot()
    def _finished(self):
        thread = self.thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, self._finished)
            return
        self.thread, self.worker = None, None
        thread.deleteLater()
        self.window._set_controls_busy(False)
        pending, self.pending = self.pending, None
        if pending:
            pending()
