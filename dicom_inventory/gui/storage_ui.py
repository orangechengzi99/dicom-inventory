"""Small workspace menu and background snapshot operations, using the existing GUI."""
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QObject,QThread,QTimer,Signal,Slot,QStringListModel,Qt
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QFileDialog,QHBoxLayout,QLabel,QListView,
                              QMenu,QMessageBox,QToolButton,QVBoxLayout,QAbstractItemView,QLineEdit)
from ..storage import SnapshotStore,StorageError,SavedScan
from .display_helpers import show_scan_state
from .theme import apply_palette
from .worker_lifecycle import BackgroundWorker


class SnapshotWorker(BackgroundWorker):
    completed=Signal(str,object)
    failed=Signal(str,str)

    def __init__(self,mode,directory,source_root=None,result=None,run_id=None):
        super().__init__()
        self.mode,self.directory,self.source_root,self.result,self.run_id=mode,directory,source_root,result,run_id

    @Slot()
    def run(self):
        try:
            store=SnapshotStore(self.directory,self.source_root)
            if self.mode=="prepare":
                store.initialize()
                store.list_scans()
                value=store.directory
            elif self.mode=="save":
                value=store.save(self.result)
            elif self.mode=="list":
                value=store.list_scans()
            elif self.mode=="load":
                value=store.load(self.run_id)
            else:
                raise StorageError("工作记录操作不受支持。")
            self.completed.emit(self.mode,value)
        except StorageError as error:
            self.failed.emit(self.mode,str(error))
        except Exception:
            self.failed.emit(self.mode,"工作记录操作未完成；当前结果仍保留，请重试或检查工作目录。")
        finally:
            self._finish_in_owner_thread()


class SavedScanListModel(QStringListModel):
    def __init__(self,scans,parent):
        self.scans=scans
        super().__init__([
            f"{self.display_time(scan.finished_at)}  |  {scan.status}  |  {scan.file_count} 文件，{scan.issue_count} 问题"
            for scan in scans],parent)

    @staticmethod
    def display_time(value):
        try:
            parsed=datetime.fromisoformat(value)
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %z") if parsed.tzinfo else value
        except ValueError:
            return value

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.ToolTipRole and index.isValid():
            return self.scans[index.row()].source_root
        return super().data(index,role)


class SavedScansDialog(QDialog):
    def __init__(self,parent,scans):
        super().__init__(parent)
        self.setWindowTitle("打开已保存扫描")
        self.resize(680,360)
        self.scans=scans
        layout=QVBoxLayout(self)
        label=QLabel("选择历史快照；打开不会重新读取原始文件。")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.list=QListView()
        self.list.setAccessibleName("已保存扫描批次")
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setModel(SavedScanListModel(scans,self))
        self.list.setCurrentIndex(self.list.model().index(0))
        layout.addWidget(self.list,1)
        self.source_path=QLineEdit()
        self.source_path.setReadOnly(True)
        self.source_path.setAccessibleName("所选快照的完整数据来源目录")
        source_label=QLabel("数据来源")
        source_label.setBuddy(self.source_path)
        source_row=QHBoxLayout()
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_path,1)
        layout.addLayout(source_row)
        self.list.selectionModel().currentChanged.connect(self._source_changed)
        self._source_changed(self.list.currentIndex())
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Open|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("打开记录")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.doubleClicked.connect(self.accept)
        layout.addWidget(buttons)
        self.setStyleSheet(parent.styleSheet())
        self.ensurePolished()
        apply_palette(self)

    def selected_id(self):
        index=self.list.currentIndex()
        return self.scans[index.row()].run_id if index.isValid() else None

    def _source_changed(self,index,*_args):
        scan=self.scans[index.row()] if index.isValid() else None
        self.source_path.setText(scan.source_root if scan else "")
        self.source_path.setToolTip((scan.finished_at+"\n"+scan.run_id) if scan else "")


class WorkspaceController(QObject):
    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.directory=None
        self.thread=None
        self.worker=None
        self.pending=None
        self.saved_key=None
        self.is_history=False
        self.label=QLabel("未选择工作目录")
        self.label.setObjectName("secondary")
        self.snapshot_label=QLabel("暂无扫描结果")
        self.snapshot_label.setObjectName("secondary")
        self.button=QToolButton()
        self.button.setText("工作记录")
        self.button.setAccessibleName("选择工作目录、保存或打开扫描")
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(self.button)
        self.choose_action=menu.addAction("选择独立工作目录…",self.choose)
        self.save_action=menu.addAction("保存本次扫描",self.save)
        self.open_action=menu.addAction("打开已保存扫描…",self.open)
        self.review_action=menu.addAction("问题处理清单…",lambda:window.review.refresh(show_list=True))
        self.review_action.setEnabled(False)
        self.button.setMenu(menu)
        self.save_action.setEnabled(False)
        self.open_action.setEnabled(False)

    @property
    def busy(self):
        return self.thread is not None

    def update_controls(self,busy):
        self.button.setEnabled(not busy)
        self.choose_action.setEnabled(not busy)
        run=self.window.result.scan_run if self.window.result else None
        self.save_action.setEnabled(not busy and self.directory is not None and run is not None
                                    and self.saved_key!=(self.directory,run.run_id))
        self.open_action.setEnabled(not busy and self.directory is not None)

    def reset(self):
        self.window.review.reset()
        self.is_history=False
        self.snapshot_label.setText("暂无扫描结果")
        self.snapshot_label.setToolTip("")

    def scan_ready(self,result):
        self.window.review.reset()
        self.is_history=False
        run=result.scan_run
        saved=bool(run and self.saved_key==(self.directory,run.run_id))
        self.snapshot_label.setText("本次扫描 · 已保存" if saved else "本次扫描 · 未保存")

    def choose(self):
        if self.window._is_busy():
            return
        selected=QFileDialog.getExistingDirectory(self.window,"选择独立工作目录（不能在DICOM目录内）",
                                                 str(self.directory or self.window.source_root or Path.home()))
        if selected:
            self.prepare(Path(selected))

    def prepare(self,directory):
        self._start("prepare",directory)

    def save(self):
        if self.directory is not None and self.window.result is not None:
            self._start("save",self.directory,result=self.window.result)

    def open(self):
        if self.directory is not None:
            self._start("list",self.directory)

    def load(self,run_id):
        if self.directory is not None:
            self._start("load",self.directory,run_id=run_id)

    def _start(self,mode,directory,**args):
        if self.window._is_busy():
            return
        thread=QThread(self)
        worker=SnapshotWorker(mode,directory,source_root=self.window.source_root,**args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        self.thread,self.worker=thread,worker
        self.window._set_controls_busy(True)
        names={"prepare":"正在准备工作目录…","save":"正在保存扫描记录…",
               "list":"正在读取扫描记录…","load":"正在打开历史快照…"}
        self.window.statusBar().showMessage(names[mode])
        thread.start()

    @Slot(str,object)
    def _completed(self,mode,value):
        if mode=="prepare":
            self.directory=value
            self.window.comparison.workspace_changed()
            self.window.review.reset()
            if self.window.review.saved_context():
                self.pending=self.window.review.refresh
            self.label.setText(self.label.fontMetrics().elidedText("工作目录："+value.name,Qt.TextElideMode.ElideMiddle,220))
            self.label.setToolTip(str(value))
            self.update_controls(True)
            self.window.statusBar().showMessage("工作目录已就绪；扫描结果需要手动保存")
        elif mode=="save":
            self.saved_key=(self.directory,value)
            self.snapshot_label.setText("本次扫描 · 已保存")
            self.pending=self.window.review.refresh
            partial = self.window.result.scan_run.status != "完成"
            self.window.statusBar().showMessage("未完成批次已保存，状态和已读范围保留" if partial else "扫描记录已完整保存；原始文件未修改")
        elif mode=="list":
            self.pending=lambda:self._select_and_open(value)
        elif mode=="load":
            window=self.window
            window._reset_results()
            window.anomaly_checkbox.setChecked(False)
            window.severity_combo.setCurrentIndex(0)
            window.type_combo.setCurrentIndex(0)
            window.candidate_paths=[]
            window.source_root=Path(value.scan_run.source_root)
            window.path_edit.setText(str(window.source_root))
            window._inventory_completed(value)
            self.saved_key=(self.directory,value.scan_run.run_id)
            self.is_history=True
            self.snapshot_label.setText("历史快照 · 已保存")
            self.snapshot_label.setToolTip(value.scan_run.finished_at+"\n"+value.scan_run.run_id)
            text=window.scan_status.text()
            show_scan_state(window,"历史快照 · "+text,
                            "ERROR" if "失败" in value.scan_run.status else "WARNING" if value.scan_run.status!="完成" else "")
            window.overview_detail.setText("历史记录，未重新读取原始文件。"+window.overview_detail.text())
            window.statusBar().showMessage("已打开历史快照；重新扫描请先重新选择数据目录")
            self.pending=window.review.refresh

    def _select_scan(self,scans):
        dialog=SavedScansDialog(self.window,scans)
        return dialog.selected_id() if dialog.exec()==QDialog.DialogCode.Accepted else None

    def _select_and_open(self,scans):
        if not scans:
            QMessageBox.information(self.window,"暂无记录","工作目录中尚未保存扫描，请先完成扫描并保存。")
            return
        run_id=self._select_scan(scans)
        if not run_id:
            return
        run=self.window.result.scan_run if self.window.result else None
        if run and self.saved_key!=(self.directory,run.run_id):
            response=QMessageBox.question(self.window,"当前结果尚未保存",
                "打开历史快照会替换当前未保存的结果。是否继续？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if response!=QMessageBox.StandardButton.Yes:
                return
        self.load(run_id)

    @Slot(str,str)
    def _failed(self,mode,message):
        self.window.statusBar().showMessage(message)
        self.window.error_text.insertPlainText("工作记录："+message+"\n")
        QMessageBox.warning(self.window,"工作记录未完成",message)

    @Slot()
    def _finished(self):
        thread=self.thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10,self._finished)
            return
        self.thread,self.worker=None,None
        thread.deleteLater()
        self.window._set_controls_busy(False)
        pending,self.pending=self.pending,None
        if pending:
            pending()


def build_workspace_footer(window):
    window.workspace=WorkspaceController(window)
    from .handling_ui import ReviewController
    window.review=ReviewController(window)
    from .comparison_ui import ComparisonController
    window.comparison=ComparisonController(window)
    row=QHBoxLayout()
    row.addWidget(window.log_toggle)
    row.addStretch()
    row.addWidget(window.workspace.snapshot_label)
    row.addSpacing(12)
    row.addWidget(window.workspace.button)
    row.addWidget(window.workspace.label)
    return row
