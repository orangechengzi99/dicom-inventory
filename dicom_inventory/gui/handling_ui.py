"""Local review dialogs and background tasks; scan observations stay immutable."""
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, QAbstractTableModel
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPlainTextEdit, QPushButton, QCheckBox, QTableView, QAbstractItemView, QHeaderView, QMessageBox, QToolButton)
from ..storage import SnapshotStore, StorageError
from ..handling import EDITABLE_STATES
from .theme import apply_palette
from .display_helpers import issue_label
from .worker_lifecycle import BackgroundWorker
from .review_focus import install_review_focus
from .review_targets import IssueTargetsPanel, file_preview


def plain_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def polish(dialog, parent):
    # Qt top-level dialogs do not automatically propagate the parent's font.
    dialog.setFont(parent.font())
    for table in dialog.findChildren(QTableView):
        if (table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
                and not table.property("reviewNoItemFrame")):
            install_review_focus(table)
    family = parent.font().families()[0].replace('"', '\\"')
    dialog.setStyleSheet(parent.styleSheet() + f'\nQDialog QWidget {{ font-family: "{family}"; }}\n')
    dialog.ensurePolished()
    apply_palette(dialog)


class ReviewWorker(BackgroundWorker):
    completed = Signal(str, object)
    failed = Signal(str)

    def __init__(self, directory, source, run_id, mode, args):
        super().__init__()
        self.directory, self.source, self.run_id, self.mode, self.args = directory, source, run_id, mode, args

    @Slot()
    def run(self):
        try:
            store = SnapshotStore(self.directory, self.source)
            if self.mode == "update":
                value = store.update_handling(self.run_id, *self.args)
            elif self.mode == "history":
                value = store.get_history(self.run_id, self.args[0])
            else:
                value = store.get_handling(self.run_id)
            self.completed.emit(self.mode, value)
        except StorageError as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit("处理记录未完成；输入仍保留，请检查工作目录后重试。")
        finally:
            self._finish_in_owner_thread()


class ReviewModel(QAbstractTableModel):
    HEADERS = ("级别", "问题", "处理状态", "关联文件", "备注")

    def __init__(self, issues, records, parent, source_root=None):
        super().__init__(parent)
        self.issues, self.records = issues, records
        self.source_root = source_root

    def rowCount(self, parent=None):
        return 0 if parent and parent.isValid() else len(self.issues)

    def columnCount(self, parent=None):
        return 0 if parent and parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        issue = self.issues[index.row()]
        record = self.records[issue.issue_id]
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 3:
                return file_preview(issue.affected_files, self.source_root) + "\n选中问题，在下方查看全部文件及完整路径。"
            return issue.description + "\n" + record.note
        if role == Qt.ItemDataRole.DisplayRole:
            severity = {"ERROR": "错误", "WARNING": "警告", "INFO": "提示"}[str(issue.severity)]
            return (severity, issue_label(issue), record.state, file_preview(issue.affected_files, self.source_root), record.note)[index.column()]
        return None

    def replace_records(self, records):
        self.beginResetModel()
        self.records = records
        self.endResetModel()


class ReviewListDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle("问题处理清单")
        self.resize(1060, 640)
        self.setMinimumSize(660, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("人工状态不改变扫描结论。按 Ctrl / Shift 多选可批量处理；已关闭记录仅可逐条查看审计。"))
        self.table = QTableView()
        self.table.setAccessibleName("当前批次的问题处理清单")
        self.model = ReviewModel(controller.window.result.issues, controller.records, self, controller.window.source_root)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setMinimumSectionSize(48)
        for column, width in enumerate((62, 220, 122, 280, 130)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.targets = IssueTargetsPanel(self, allow_locate=True)
        self.targets.locate_requested.connect(self.locate_file)
        layout.addWidget(self.targets)
        layout.addWidget(plain_label("关联文件是本条问题的检查范围；多文件不代表每个文件都需修改。路径可能识别来源，分享前请核对。"))
        row = QHBoxLayout()
        self.edit_button = QPushButton("编辑所选问题")
        self.edit_button.clicked.connect(self.edit)
        self.table.doubleClicked.connect(self.edit)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        row.addWidget(self.edit_button)
        row.addStretch()
        close = QPushButton("返回审阅")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.selection_changed()
        polish(self, controller.window)

    def selected_ids(self):
        return tuple(self.model.issues[index.row()].issue_id for index in self.table.selectionModel().selectedRows())

    def selection_changed(self, *_):
        ids = self.selected_ids()
        closed = any(self.controller.records[key].state == "已关闭" for key in ids)
        self.edit_button.setText("查看关闭记录" if len(ids) == 1 and closed else "编辑所选问题")
        self.edit_button.setEnabled(bool(ids) and not self.controller.busy and (not closed or len(ids) == 1))
        selected = set(ids)
        self.targets.set_issues(tuple(issue for issue in self.model.issues if issue.issue_id in selected), self.model.source_root)
        self.targets.setEnabled(not self.controller.busy)

    def locate_file(self, path):
        if not self.controller.window._is_busy() and self.controller.window._jump_to_path(path):
            self.accept()

    def edit(self, *_):
        if self.selected_ids():
            self.controller.edit(self.selected_ids(), self)


class ReviewEditor(QDialog):
    def __init__(self, controller, ids, parent):
        super().__init__(parent)
        self.controller, self.ids = controller, ids
        self.original = {key: controller.records[key] for key in ids}
        self.expected = {key: (record.track_key, record.revision) for key, record in self.original.items()}
        self.setWindowTitle("编辑问题处理记录")
        self.resize(640, 600)
        self.setMinimumSize(520, 520)
        layout = QVBoxLayout(self)
        single = len(ids) == 1
        record = self.original[ids[0]]
        self.read_only = single and record.state == "已关闭"
        if self.read_only:
            self.setWindowTitle("查看已关闭问题记录")
        layout.addWidget(plain_label(f"所选 {len(ids)} 项问题；人工处理不等于复检通过。"))
        selected_ids = set(ids)
        issues = tuple(issue for issue in controller.window.result.issues if issue.issue_id in selected_ids)
        self.targets_dialog = QDialog(self)
        self.targets_dialog.setWindowTitle("本次编辑的目标文件")
        self.targets_dialog.resize(760, 240)
        self.targets_dialog.setMinimumSize(620, 220)
        self.targets_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        targets_layout = QVBoxLayout(self.targets_dialog)
        self.targets = IssueTargetsPanel(self.targets_dialog)
        self.targets.set_issues(issues, controller.window.source_root)
        targets_layout.addWidget(self.targets)
        targets_layout.addWidget(plain_label("仅核对本次编辑的关联范围；不会修改原始文件。路径可能识别来源，分享前请核对。"))
        target_close = QPushButton("返回编辑")
        target_close.clicked.connect(self.targets_dialog.reject)
        targets_layout.addWidget(target_close, 0, Qt.AlignmentFlag.AlignRight)
        self.targets_summary = plain_label(self.targets.title.text() + "\n" + file_preview(self.targets.model.paths, controller.window.source_root))
        layout.addWidget(self.targets_summary)
        self.targets_toggle = QToolButton()
        self.targets_toggle.setText("查看全部目标文件与路径…")
        self.targets_toggle.clicked.connect(self.targets_dialog.show)
        layout.addWidget(self.targets_toggle)
        layout.addWidget(plain_label("处理状态"))
        self.state = QComboBox()
        self.state.setAccessibleName("人工处理状态")
        self.state.addItems(EDITABLE_STATES)
        if self.read_only:
            self.state.addItem("已关闭")
        if single:
            self.state.setCurrentText(record.state)
        layout.addWidget(self.state)
        self.replace_note = QCheckBox("批量替换备注（不勾选则保留各自备注）")
        self.replace_note.setChecked(single)
        self.replace_note.setVisible(not single)
        layout.addWidget(self.replace_note)
        layout.addWidget(plain_label("备注 / 忽略理由（最多2000字）"))
        layout.addWidget(plain_label("请勿填写患者姓名、患者编号、生日等身份信息。可忽略必须有理由。"))
        self.note = QPlainTextEdit(record.note if single else "")
        self.note.setAccessibleName("人工备注或忽略理由")
        self.note.setReadOnly(self.read_only)
        self.note.setMinimumHeight(90)
        self.note.setEnabled(single)
        self.replace_note.toggled.connect(self.note.setEnabled)
        layout.addWidget(self.note, 1)
        self.info = plain_label((f"首次批次：{record.first_run}\n最近批次：{record.latest_run}\n更新时间：{record.updated_at}"
                                 if single else "首次/最近批次和完整历史请逐条查看。"))
        if single:
            from .storage_ui import SavedScanListModel
            self.info.setText(f"首次批次：{record.first_run}\n最近批次：{record.latest_run}\n更新时间：{SavedScanListModel.display_time(record.updated_at)}")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setAccessibleName("处理状态与备注的完整变更记录")
        self.history.setMaximumHeight(120)
        self.history.setPlainText("正在读取变更记录…" if single else "批量保存会为每项问题分别保留变更记录。")
        layout.addWidget(self.history)
        self.error = plain_label("")
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel = QPushButton("取消")
        self.cancel.clicked.connect(self.reject)
        self.save = QPushButton("保存修改")
        self.save.clicked.connect(self.submit)
        if self.read_only:
            self.save.hide()
            self.cancel.setText("返回")
        row.addWidget(self.cancel)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.initial = (self.state.currentText(), self.note.toPlainText(), self.replace_note.isChecked())
        polish(self, parent)
        polish(self.targets_dialog, self)
        self.set_busy(False)

    def set_busy(self, busy):
        self.save.setEnabled(not busy and not self.read_only)
        self.cancel.setEnabled(not busy)
        self.state.setEnabled(not busy and not self.read_only)
        self.replace_note.setEnabled(not busy and not self.read_only)
        self.note.setEnabled(not busy and self.replace_note.isChecked())

    def submit(self):
        if self.controller.window._is_busy() or self.read_only:
            return
        note = self.note.toPlainText() if self.replace_note.isChecked() else None
        if note is not None and len(note) > 2000:
            self.error.setText("备注超过2000字，请精简后保存。")
            self.note.setFocus()
            return
        if self.state.currentText() == "可忽略" and any(not (note if note is not None else item.note).strip()
                                                        for item in self.original.values()):
            self.error.setText("可忽略必须有理由；批量操作可勾选替换备注并填写共同理由。")
            self.note.setFocus()
            return
        self.error.setText("")
        self.controller.update(self)

    def reject(self):
        if self.controller.busy:
            return
        current = (self.state.currentText(), self.note.toPlainText(), self.replace_note.isChecked())
        if current != self.initial and QMessageBox.question(self, "尚未保存修改", "放弃本次未保存的修改？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.targets_dialog.hide()
        super().reject()

    def accept(self):
        self.targets_dialog.hide()
        super().accept()


class ReviewController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.records = {}
        self.key = None
        self.thread = self.worker = None
        self.pending = None
        self.editor = self.list_dialog = None

    @property
    def busy(self):
        return self.thread is not None

    def saved_context(self):
        run = self.window.result.scan_run if self.window.result else None
        directory = self.window.workspace.directory
        return (directory, run.run_id) if run and directory and self.window.workspace.saved_key == (directory, run.run_id) else None

    def reset(self):
        self.key, self.records = None, {}
        self.update_controls(self.window._is_busy())

    def update_controls(self, busy):
        context = self.saved_context()
        ready = context is not None and self.key == context
        selected = self.window.issue_selector.currentData()
        record = self.records.get(selected) if ready else None
        self.window.handling_label.setText("人工状态：" + record.state if record else
            "人工记录未就绪，请重新打开处理清单" if context else "保存扫描后可记录人工处理")
        self.window.handling_label.setToolTip(("备注：" + record.note + "\n更新：" + record.updated_at) if record else "")
        self.window.handling_button.setText("查看记录…" if record and record.state == "已关闭" else "处理记录…")
        self.window.handling_button.setEnabled(not busy and record is not None)
        self.window.workspace.review_action.setEnabled(not busy and context is not None and bool(self.window.result.issues))
        if self.editor:
            self.editor.set_busy(busy)

    def refresh(self, show_list=False):
        if self.window._is_busy():
            return
        if self.saved_context():
            self.pending = self.open_list if show_list else None
            self._start("read")
        else:
            self.reset()

    def open_list(self):
        if self.window._is_busy() or self.key != self.saved_context():
            return
        dialog = ReviewListDialog(self)
        self.list_dialog = dialog
        try:
            dialog.exec()
        finally:
            self.list_dialog = None
            dialog.deleteLater()

    def edit_current(self):
        selected = self.window.issue_selector.currentData()
        if selected in self.records:
            self.edit((selected,), self.window)

    def create_editor(self, ids, parent=None):
        if self.window._is_busy() or self.key != self.saved_context() or not ids:
            return None
        if any(key not in self.records for key in ids) or (len(ids) > 1 and any(self.records[key].state == "已关闭" for key in ids)):
            return None
        self.editor = ReviewEditor(self, ids, parent or self.window)
        self.editor.finished.connect(self._editor_closed)
        if len(ids) == 1:
            self._start("history", (ids[0],))
        return self.editor

    def edit(self, ids, parent):
        editor = self.create_editor(ids, parent)
        if editor:
            editor.exec()
            editor.deleteLater()

    @Slot(int)
    def _editor_closed(self, _):
        self.editor = None

    def update(self, editor):
        note = editor.note.toPlainText() if editor.replace_note.isChecked() else None
        self._start("update", (editor.expected, editor.state.currentText(), note))

    def _start(self, mode, args=()):
        context = self.saved_context()
        if self.window._is_busy() or context is None:
            return
        thread = QThread(self)
        worker = ReviewWorker(context[0], self.window.source_root, context[1], mode, args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        self.thread, self.worker = thread, worker
        self.window._set_controls_busy(True)
        self.window.statusBar().showMessage("正在保存人工处理…" if mode == "update" else "正在读取人工处理记录…")
        thread.start()

    @Slot(str, object)
    def _completed(self, mode, value):
        if mode == "history":
            if self.editor:
                from .storage_ui import SavedScanListModel
                self.editor.history.setPlainText("\n\n".join(
                    f"{SavedScanListModel.display_time(item.changed_at)}  修订{item.revision}\n{item.old_state or '创建'} → {item.new_state}\n"
                    f"原备注：{item.old_note}\n新备注：{item.new_note}\n批次：{item.run_id}" for item in value))
        else:
            self.records, self.key = value, self.saved_context()
            if self.list_dialog:
                self.list_dialog.model.replace_records(value)
            if mode == "update" and self.editor:
                editor = self.editor
                self.pending = editor.accept
        self.window.statusBar().showMessage("人工处理已保存；原始检查结论未改变" if mode == "update" else "人工处理记录已读取")

    @Slot(str)
    def _failed(self, message):
        self.pending = None
        if self.worker and self.worker.mode == "read":
            self.key = None  # Failed refresh must not advertise an old cache as current.
        self.window.statusBar().showMessage(message)
        if self.editor:
            self.editor.error.setText(message)
        else:
            QMessageBox.warning(self.window, "处理记录未完成", message)

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
        if self.list_dialog:
            self.list_dialog.selection_changed()
        pending, self.pending = self.pending, None
        if pending:
            pending()

