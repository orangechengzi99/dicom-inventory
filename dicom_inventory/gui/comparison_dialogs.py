"""Neutral model/view comparison dialogs using existing desktop controls."""
import json
import re
from PySide6.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel, QStringListModel
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QTableView, QPlainTextEdit,
    QPushButton, QSplitter, QWidget, QAbstractItemView, QHeaderView, QListView, QDialogButtonBox)
from .handling_ui import plain_label, polish
from .storage_ui import SavedScanListModel
from ..core.comparison import path_key

METRIC_LABELS = {"file_count": "已读文件", "success_count": "读取成功", "failure_count": "读取失败",
    "study_count": "Study", "series_count": "Series", "unique_instance_count": "唯一实例", "issue_count": "问题总数",
    "error_count": "错误", "warning_count": "警告", "info_count": "提示"}


class SavedComparisonsDialog(QDialog):
    def __init__(self, parent, comparisons):
        super().__init__(parent)
        self.items = comparisons
        self.setWindowTitle("打开已保存复检对比")
        self.resize(720, 380)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("历史对比记录保留当时的结论和系统动作；打开不会重新扫描或重放关闭动作。"))
        self.list = QListView()
        self.list.setAccessibleName("已保存的复检对比")
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setModel(QStringListModel([f"{SavedScanListModel.display_time(item.created_at)}  |  "
            + ("可比" if item.comparable else "无法完整比较") + f"  |  基线 {item.baseline_run[:8]} → 本次 {item.current_run[:8]}"
            for item in comparisons], self))
        self.list.setCurrentIndex(self.list.model().index(0))
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("打开对比")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("返回")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.doubleClicked.connect(self.accept)
        layout.addWidget(buttons)
        polish(self, parent)

    def selected_id(self):
        index = self.list.currentIndex()
        return self.items[index.row()].comparison_id if index.isValid() else None


class ComparisonModel(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.items = ()
        self.kind = 0
        self.headers = ()

    def replace(self, report, kind):
        self.beginResetModel()
        self.kind = kind
        self.items = (report.issue_changes, report.file_changes, report.count_changes)[kind]
        self.headers = (("结论", "问题", "对比时状态", "系统动作"), ("变化", "文件", "字段数"),
                        ("数量", "基线", "本次", "变化"))[kind]
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent and parent.isValid() else len(self.items)

    def columnCount(self, parent=None):
        return 0 if parent and parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.reason if self.kind < 2 else METRIC_LABELS.get(item.metric, item.metric)
        if role == Qt.ItemDataRole.DisplayRole:
            values = ((item.status, item.description, item.handling_after, item.action or "未变更") if self.kind == 0
                      else (item.status, item.relative_path, str(len(item.fields))) if self.kind == 1
                      else (METRIC_LABELS.get(item.metric, item.metric), str(item.before), str(item.after), f"{item.after-item.before:+d}"))
            return values[index.column()]
        return None


class ComparisonDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.report = controller.report
        self.setWindowTitle("复检对比")
        self.resize(1080, 680)
        self.setMinimumSize(780, 500)
        layout = QVBoxLayout(self)
        baseline, current = controller.report_baseline, controller.report_current
        self.summary = plain_label("完整可比的元数据复检" if self.report.comparable else "无法完整比较：" + "；".join(self.report.reasons))
        layout.addWidget(self.summary)
        self.batch_label = plain_label(f"基线：{SavedScanListModel.display_time(baseline.scan_run.finished_at)}  |  "
                                     f"本次：{SavedScanListModel.display_time(current.scan_run.finished_at)}")
        self.batch_label.setToolTip(f"基线 {self.report.baseline_run}\n本次 {self.report.current_run}\n比较 {self.report.comparison_id}")
        layout.addWidget(self.batch_label)
        self.limits = plain_label("仅比较所选元数据；处理状态和系统动作记录为对比当时，当前状态见审阅。无法确认不关闭。")
        self.limits.setToolTip("\n".join(self.report.limits))
        layout.addWidget(self.limits)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        filters.addWidget(plain_label("查看"))
        self.view = QComboBox()
        self.view.setAccessibleName("选择问题、文件或数量变化")
        self.view.addItems(("问题变化", "文件变化", "数量变化"))
        filters.addWidget(self.view, 1)
        filters.addWidget(plain_label("结论"))
        self.status = QComboBox()
        self.status.setAccessibleName("筛选对比结论")
        filters.addWidget(self.status, 1)
        left_layout.addLayout(filters)
        self.table = QTableView()
        self.table.setAccessibleName("复检对比清单")
        self.model = ComparisonModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(0)
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSortingEnabled(True)
        left_layout.addWidget(self.table, 1)
        self.splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("比较原因、关联文件与检查证据，可选择复制")
        self.details.setMinimumWidth(250)
        right_layout.addWidget(self.details, 1)
        self.audit_button = QPushButton("查看问题审计")
        self.audit_button.clicked.connect(controller.read_audit)
        right_layout.addWidget(self.audit_button)
        self.splitter.addWidget(right)
        self.splitter.setSizes([650, 360])
        layout.addWidget(self.splitter, 1)
        row = QHBoxLayout()
        self.export_button = QPushButton("导出对比 CSV")
        self.export_button.clicked.connect(lambda: controller.window._export_result("comparison"))
        row.addWidget(self.export_button)
        row.addStretch()
        self.close_button = QPushButton("返回审阅")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.view.currentIndexChanged.connect(self._view_changed)
        self.status.currentTextChanged.connect(self._filter_changed)
        self.table.selectionModel().currentChanged.connect(self._selected)
        self._view_changed(0)
        polish(self, controller.window)
        for control in (self.view, self.status, self.export_button, self.close_button, self.audit_button):
            control.setFixedHeight(40)

    def _view_changed(self, kind):
        self.proxy.setFilterRegularExpression("")
        self.model.replace(self.report, kind)
        self.status.blockSignals(True)
        self.status.clear()
        self.status.addItem("全部结论")
        self.status.addItems(sorted({item.status for item in self.model.items}) if kind < 2 else [])
        self.status.setEnabled(kind < 2)
        self.status.blockSignals(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        widths = (95, 180, 115, 90) if kind == 0 else (115, 200, 65) if kind == 1 else (100, 75, 75, 75)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(1 if kind < 2 else 0, QHeaderView.ResizeMode.Stretch)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._filter_changed(self.status.currentText())

    def _filter_changed(self, value):
        self.proxy.setFilterRegularExpression("" if value == "全部结论" else "^" + re.escape(value) + "$")
        self.table.setCurrentIndex(self.proxy.index(0, 0))
        self._selected(self.table.currentIndex())

    def selected_item(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.items[index.row()] if index.isValid() else None

    def _selected(self, *_):
        item = self.selected_item()
        self.audit_button.setEnabled(item is not None and self.model.kind == 0 and not self.controller.window._is_busy())
        if item is None:
            self.details.setPlainText("当前筛选没有记录；可选择其他结论。")
        elif self.model.kind == 0:
            self.audit_button.setText("查看基线轨迹审计" if item.baseline_files else "查看本次轨迹审计")
            origin = "当前问题：" if item.current_files else "基线问题："
            observation = f"观察：{item.observation}\n" if item.status != item.observation else ""
            self.details.setPlainText(f"{origin}{item.description}\n\n结论：{item.status}\n{observation}{item.reason}\n\n"
                f"处理状态：{item.handling_before} → {item.handling_after}\n系统动作：{item.action or '未变更'}\n\n"
                f"基线关联：\n" + "\n".join(item.baseline_files) + "\n\n本次关联：\n" + "\n".join(item.current_files))
            baseline, current = self.controller.report_baseline, self.controller.report_current
            for label, result in (("基线问题证据", baseline), ("本次问题证据", current)):
                issue = next((i for i in result.issues if i.issue_id == item.issue_id), None)
                self.details.appendPlainText("\n" + label + "：\n" + (json.dumps(dict(issue.evidence), ensure_ascii=False, indent=2) if issue else "本批次未再记录该问题；是否已解决见上方范围和执行依据。"))
            related = {path_key(path, baseline.scan_run.source_root) for path in (*item.baseline_files, *item.current_files)}
            for key in sorted(related):
                record = self.controller.record_index.get(key)
                if record is not None:
                    self.details.appendPlainText("\n本次文件：" + record.file_path + f"\n读取：{record.status}\n评估：{record.assessment_status}")
                    self.details.appendPlainText("\n".join(f"{check.code}: {check.status} {check.reason}" for check in record.series_checks))
        elif self.model.kind == 1:
            self.details.setPlainText(f"{item.relative_path}\n\n{item.status}\n{item.reason}\n\n" +
                "\n\n".join(f"{name}\n基线：{before}\n本次：{after}" for name, before, after in item.fields))
        else:
            self.details.setPlainText(f"{METRIC_LABELS.get(item.metric, item.metric)}\n\n基线：{item.before}\n本次：{item.after}\n变化：{item.after-item.before:+d}\n\n不可完整比较时，数量仅供参考已读范围。")

    def set_busy(self, busy):
        for control in (self.view, self.status, self.table, self.export_button, self.close_button):
            control.setEnabled(not busy)
        self.status.setEnabled(not busy and self.model.kind < 2)
        self.audit_button.setEnabled(not busy and self.model.kind == 0 and self.selected_item() is not None)

    def reject(self):
        if not self.controller.window._is_busy():
            super().reject()
