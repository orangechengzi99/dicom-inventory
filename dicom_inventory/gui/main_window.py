"""Main window for folder selection, inventory, filtering, and export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from ..core import InventoryResult
from ..core.exporter import is_output_directory_allowed
from .workers import DiscoveryWorker, InventoryWorker, ExportWorker
from .display_helpers import (set_parameters, status_icon, issue_label, format_spacing,
                              set_detail_path, show_scan_state, show_scan_progress)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DICOM 数据清点")

        self.source_root: Path | None = None
        self.candidate_paths: list[Path] = []
        self.result: InventoryResult | None = None
        self.discovery_thread: QThread | None = None
        self.discovery_worker: DiscoveryWorker | None = None
        self.inventory_thread: QThread | None = None
        self.inventory_worker: InventoryWorker | None = None

        self.export_thread: QThread | None = None
        self.export_worker: ExportWorker | None = None
        self.discovery_complete = True
        self._needs_rediscovery = False
        self._scan_terminal = True
        self.discovery_notes: tuple[str, ...] = ()
        self._current_record = None
        self._current_issues = ()
        self._current_node = None
        self._scope_paths = {}
        self._path_to_row: dict[str, int] = {}
        from .workspace_ui import build_workspace
        build_workspace(self)
        self._display_record(None)

    def _filters_changed(self, *_args) -> None:
        allowed = self._scope_paths.get(self.scope_combo.currentData())
        self.proxy_model.set_keyword(self.search_edit.text())
        self.hierarchy_proxy.set_keyword(self.search_edit.text())
        advanced_count = int(bool(self.scope_combo.currentData())) + int(bool(self.type_combo.currentData()))
        self.filter_toggle.setText(f"更多筛选 · {advanced_count}" if advanced_count else "更多筛选")
        self.proxy_model.set_allowed_paths(allowed)
        self.proxy_model.set_anomalies_only(self.anomaly_checkbox.isChecked())
        self.proxy_model.set_severity(self.severity_combo.currentData() or "")
        self.proxy_model.set_issue_type(self.type_combo.currentData() or "")
        self.hierarchy_proxy.set_allowed_paths(allowed)
        self.hierarchy_proxy.set_anomalies_only(self.anomaly_checkbox.isChecked())
        self.hierarchy_proxy.set_severity(self.severity_combo.currentData() or "")
        self.hierarchy_proxy.set_issue_type(self.type_combo.currentData() or "")
        self._ensure_selection()

    def _view_changed(self, index):
        self.result_stack.setCurrentIndex(index)
        self._ensure_selection()

    def _ensure_selection(self) -> None:
        count = self.proxy_model.rowCount()
        tree_mode = self.view_combo.currentIndex() == 1
        self.visible_count.setText(f"{'分层结果' if tree_mode else '文件清单'} · 显示 {count} / {self.table_model.rowCount()} 个文件")
        self.empty_hint.setVisible(count == 0)
        self.empty_hint.setText("筛选下没有记录，可切回全部级别与类型。" if self.table_model.rowCount() else "选择目录并开始清点，原文件不会被修改。")
        if tree_mode:
            if self.hierarchy_proxy.rowCount() and not self.tree.currentIndex().isValid():
                self.tree.setCurrentIndex(self.hierarchy_proxy.index(0, 0))
            self._tree_selection_changed(self.tree.currentIndex())
        else:
            if count and not self.table.currentIndex().isValid():
                self.table.setCurrentIndex(self.proxy_model.index(0, 0))
            self._selection_changed(self.table.currentIndex())

    def _selection_changed(self, index, *_args) -> None:
        if self.view_combo.currentIndex() != 0:
            return
        source_index = self.proxy_model.mapToSource(index)
        record = self.table_model.record_at(source_index.row()) if source_index.isValid() else None
        self._current_node = None
        self._display_record(record)

    def _display_record(self, record):
        from ..core.quality import FIELD_ATTRIBUTES, field_requirements
        self._current_record = record
        self._current_issues = record.issues if record else ()
        self.issue_selector.blockSignals(True)
        self.issue_selector.clear()
        if record is None:
            self.detail_title.setText("选择一条记录")
            set_detail_path(self, fallback="右侧会显示原因、影响和核对建议。")
            self.metadata_text.clear()
            self.uid_text.clear()
            set_parameters(self)
        else:
            self.detail_title.setText(Path(record.file_path).name)
            set_detail_path(self, record.file_path)
            set_parameters(self, (("模态", record.modality or "未提供"),
                                 ("序列", self.table_model.series_labels.get(record.file_path, "未归组")),
                                 ("实例号", record.instance_number or "未提供"),
                                 ("矩阵", f"{record.rows or '—'} × {record.columns or '—'}"),
                                 ("像素间距", format_spacing(record.pixel_spacing)),
                                 ("层厚", (record.slice_thickness + " mm") if record.slice_thickness else "未提供")))
            for issue in record.issues:
                text = f"{issue.severity} · {issue_label(issue)}"
                self.issue_selector.addItem(status_icon(issue.severity), text, issue.issue_id)
                self.issue_selector.setItemData(self.issue_selector.count()-1, issue.description, Qt.ItemDataRole.ToolTipRole)
            requirements = field_requirements(record)
            names = ("Modality", "Rows", "Columns", "PixelSpacing", "SliceThickness",
                     "ImagePositionPatient", "ImageOrientationPatient", "InstanceNumber", "RescaleSlope", "RescaleIntercept")
            policies = {"required": "关键字段", "recommended": "建议参照", "not_applicable": "不适用或未评估"}
            self.metadata_text.setPlainText("\n".join(
                f"{name}: {getattr(record, FIELD_ATTRIBUTES[name]) or '未提供'}"
                + (f"（{policies[requirements[name]]}）" if not getattr(record, FIELD_ATTRIBUTES[name]) else "")
                for name in names))
            self.uid_text.setPlainText("\n".join(
                f"{name}: {getattr(record, FIELD_ATTRIBUTES[name]) or '未提供'}"
                for name in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "SOPClassUID", "NumberOfFrames", "FrameOfReferenceUID", "ImageType"))
                + f"\n评估: {record.assessment_status}\n读取: {record.status}")
            self.metadata_text.appendPlainText("\n序列检查：\n" + "\n".join(
                f"{check.code}: {check.status} {check.reason}" for check in record.series_checks))
            if record.slice_sort_position is not None:
                self.metadata_text.appendPlainText(f"法向投影位置: {record.slice_sort_position:.8g} mm")
        self.issue_selector.blockSignals(False)
        self.issue_selector.setEnabled(bool(record and record.issues))
        self.issue_selector.setVisible(self.issue_selector.count() > 1)
        self._show_selected_issue()

    def _show_selected_issue(self, *_args) -> None:
        record = self._current_record
        selected = self.issue_selector.currentData()
        issue = next((item for item in self._current_issues if item.issue_id == selected), None)
        self.series_jump.setEnabled(self.result is not None and bool(record or self._current_node))
        if issue:
            self.issue_heading.setText(issue_label(issue))
            self.issue_icon.setPixmap(status_icon(issue.severity).pixmap(18, 18))
            labels = {"values": "观测值", "observation": "观测", "requirement": "用途策略", "tolerance": "比较容差", "affected_count": "关联文件数", "constraint": "形式要求",
                      "StudyUIDs": "关联检查UID", "numbers": "重复序号", "missing_ranges": "未出现序号区间",
                      "range_count": "区间数", "adjacent_mm": "相邻间距(mm)", "interval_count": "间隔数",
                      "median_mm": "中位间距(mm)", "tolerance_mm": "间距容差(mm)"}
            self.issue_description.setText(issue.description)
            self.evidence_text.setPlainText("\n".join(f"{labels.get(key, key)}: {value}" for key, value in issue.evidence))
            self.issue_impact.setText("影响：" + issue.impact)
            self.issue_advice.setText("建议：" + issue.recommendation)
            self.affected_title.setText(f"受影响文件 · {len(issue.affected_files)}（双击或回车定位）")
            self.affected_model.setStringList(list(issue.affected_files))
        else:
            context = record is not None or self._current_node is not None
            self.issue_heading.setText("所选规则未发现问题" if context else "尚未选择记录")
            self.issue_icon.clear()
            self.issue_description.setText("仅覆盖已执行规则；未评估项可在下方展开查看。" if context else "选择文件或分层结果查看具体事实。")
            self.evidence_text.clear()
            self.issue_impact.setText("不代表完整DICOM合规、序列完整或像素可用。" if context else "—")
            self.issue_advice.setText("结合采集协议和人工核对使用。" if context else "—")
            self.affected_title.setText("受影响文件")
            self.affected_model.setStringList([])
            if context and self.result and self.result.scan_run and self.result.scan_run.status != "完成":
                self.issue_heading.setText("检查未完成")
                self.issue_description.setText("本批次仅保留已读文件事实；序列检查未完成，不能据此判断没有问题。")
        self.affected_title.setVisible(issue is not None)
        self.affected_list.setVisible(issue is not None)
        self.handling_label.setVisible(issue is not None)
        self.handling_button.setVisible(issue is not None)
        context = record is not None or self._current_node is not None
        self.issue_impact.setVisible(context)
        self.issue_advice.setVisible(context)
        self.technical_toggle.setEnabled(context)
        self.affected_toggle.setVisible(context)
        self.affected_toggle.setText(f"关联文件 · {len(issue.affected_files)} / 序列定位" if issue else "序列定位")
        if not context:
            self.affected_toggle.setChecked(False)
        self.review.update_controls(self._is_busy())
        tone = str(issue.severity) if issue else "neutral"
        if self.issue_panel.property("tone") != tone:
            self.issue_panel.setProperty("tone", tone)
            self.issue_panel.style().unpolish(self.issue_panel)
            self.issue_panel.style().polish(self.issue_panel)
            self.issue_panel.update()

    def _copy_full_path(self) -> None:
        path = self.full_path_text.toPlainText()
        if path:
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("完整文件路径已复制；分享前请核对隐私信息")

    def _jump_to_affected(self, index) -> None:
        self._jump_to_path(index.data())

    def _jump_to_path(self, path) -> bool:
        """Shared presentation navigation for main and handling file lists."""
        row = self._path_to_row.get(path)
        if row is None:
            return False
        self.search_edit.clear()
        self.anomaly_checkbox.setChecked(False)
        self.severity_combo.setCurrentIndex(0)
        self.type_combo.setCurrentIndex(0)
        self.scope_combo.setCurrentIndex(0)
        if self.view_combo.currentIndex() == 1:
            node = self.hierarchy_model.path_nodes.get(path)
            target = self.hierarchy_proxy.mapFromSource(self.hierarchy_model.index_for_node(node))
            self.tree.setCurrentIndex(target)
            self.tree.scrollTo(target)
            return True
        target = self.proxy_model.mapFromSource(self.table_model.index(row, 0))
        self.table.setCurrentIndex(target)
        self.table.scrollTo(target)
        return True

    def _is_busy(self) -> bool:
        return (any(thread is not None for thread in (self.discovery_thread, self.inventory_thread, self.export_thread))
                or self.workspace.busy or self.review.busy or self.comparison.busy)

    def _tree_selection_changed(self, index, *_args):
        if self.view_combo.currentIndex() != 1:
            return
        source = self.hierarchy_proxy.mapToSource(index)
        node = self.hierarchy_model.node_at(source)
        self._current_node = node
        if node is None or node.kind == "file":
            self._display_record(node.payload if node else None)
            return
        self._current_record = None
        self._current_issues = node.issues
        value = node.payload
        self.detail_title.setText(node.label)
        set_detail_path(self, fallback=f"本分支 {len(node.files)} 个文件 · {len(node.issues)} 条关联问题\n{value.overall_status}")
        self.issue_selector.blockSignals(True)
        self.issue_selector.clear()
        for issue in node.issues:
            self.issue_selector.addItem(status_icon(issue.severity), f"{issue.severity} · {issue_label(issue)}", issue.issue_id)
            self.issue_selector.setItemData(self.issue_selector.count()-1, issue.description, Qt.ItemDataRole.ToolTipRole)
        self.issue_selector.setEnabled(bool(node.issues))
        self.issue_selector.setVisible(self.issue_selector.count() > 1)
        self.issue_selector.blockSignals(False)
        def values(items):
            return " | ".join(item or "未提供" for item in items[:20]) + (f"（另{len(items)-20}个值，完整集合见序列CSV）" if len(items) > 20 else "")
        text = f"全标识范围文件数: {value.file_count}\n模态: {values(value.modalities)}\n问题数: {value.issue_count}"
        if node.kind == "series":
            text += "\n" + "\n".join(f"{label}: {values(items)}" for label, items in (
                ("Rows", value.rows), ("Columns", value.columns), ("PixelSpacing", value.pixel_spacings),
                ("SliceThickness", value.slice_thicknesses), ("ImageOrientationPatient", value.orientations)))
            text += f"\n法向范围(mm): {value.spatial_range if value.spatial_range is not None else '未评估'}\n序列检查：\n"
            text += "\n".join(f"{check.code}: {check.status} {check.reason}" for check in value.assessments)
            self.uid_text.setPlainText(f"SeriesInstanceUID: {value.series_instance_uid or '未提供'}\nStudyInstanceUID集合:\n" + "\n".join(value.study_uids)
                                      + "\nFrameOfReferenceUID集合:\n" + "\n".join(value.frame_of_reference_uids)
                                      + "\nImageType集合:\n" + "\n".join(value.image_types))
        else:
            text += f"\n序列/未归组分支数: {value.series_count}\n总体状态: {value.overall_status}"
            self.uid_text.setPlainText(f"StudyInstanceUID: {value.study_instance_uid or '未提供'}")
        self.metadata_text.setPlainText(text)
        set_parameters(self, (("模态", values(value.modalities)), ("序列", node.label if node.kind == "series" else f"{value.series_count} 个分支"),
                             ("实例号", "展开文件查看"), ("矩阵", f"{values(value.rows)} × {values(value.columns)}" if node.kind == "series" else "展开序列查看"),
                             ("像素间距", values(tuple(format_spacing(item) for item in value.pixel_spacings)) if node.kind == "series" else "展开序列查看"),
                             ("层厚", values(tuple(item + " mm" if item else "未提供" for item in value.slice_thicknesses)) if node.kind == "series" else "展开序列查看")))
        self._show_selected_issue()

    def _jump_to_series(self):
        paths = self.affected_model.stringList()
        path = self._current_record.file_path if self._current_record else next(iter(paths), None)
        if path is None and self._current_node:
            path = next(iter(self._current_node.files), None)
        node = self.hierarchy_model.path_nodes.get(path)
        if node is None:
            return
        self.search_edit.clear()
        self.anomaly_checkbox.setChecked(False)
        self.severity_combo.setCurrentIndex(0)
        self.type_combo.setCurrentIndex(0)
        self.scope_combo.setCurrentIndex(0)
        self.view_combo.setCurrentIndex(1)
        target = self.hierarchy_proxy.mapFromSource(self.hierarchy_model.index_for_node(node.parent_node))
        self.tree.setCurrentIndex(target)
        self.tree.scrollTo(target)

    def _populate_scopes(self, result):
        self._scope_paths = {}
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("全部检查与序列", None)
        for kind, summaries in (("Study", result.studies), ("Series", result.series)):
            for number, summary in enumerate(summaries, 1):
                self._scope_paths[summary.group_key] = frozenset(summary.files)
                self.scope_combo.addItem(f"{kind} {number} · {summary.file_count} 文件", summary.group_key)
                self.scope_combo.setItemData(self.scope_combo.count()-1, summary.group_key, Qt.ItemDataRole.ToolTipRole)
        self.scope_combo.blockSignals(False)

    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 DICOM 文件夹",
            str(self.source_root or Path.home()),
        )
        if selected:
            self.path_edit.setText(selected)
            self._begin_discovery(Path(selected))

    def _path_edited(self) -> None:
        if self._is_busy():
            return
        text = self.path_edit.text().strip()
        if not text:
            self.candidate_paths = []
            self.source_root = None
            self._reset_results()
            self.start_button.setEnabled(False)
            self.statusBar().showMessage("请选择数据目录")
            return
        path = Path(text).expanduser()
        if not path.is_dir():
            self.candidate_paths = []
            self.source_root = None
            self._reset_results()
            self.start_button.setEnabled(False)
            self.statusBar().showMessage("输入的文件夹不存在")
            return
        resolved = path.resolve()
        if resolved != self.source_root or self.workspace.is_history or self._needs_rediscovery:
            self._begin_discovery(resolved)

    def _reset_results(self) -> None:
        self.comparison.reset_result()
        self.workspace.reset()
        self.result = None
        self._path_to_row = {}
        self._current_node = None
        self._current_issues = ()
        self._scope_paths = {}
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("全部检查与序列", None)
        self.scope_combo.blockSignals(False)
        self.proxy_model.set_allowed_paths(None)
        self.hierarchy_proxy.set_allowed_paths(None)
        self.hierarchy_model.set_result(None)
        show_scan_state(self, "等待扫描")
        self.overview_title.setText("等待清点")
        self.overview_detail.setText("检查所选元数据，不修改原文件。")
        self.table_model.clear()
        self.error_text.clear()
        self.export_button.setEnabled(False)
        for label in self.stats.values():
            label.setText("0")

    def _set_controls_busy(self, busy: bool) -> None:
        self.path_edit.setEnabled(not busy)
        self.choose_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy and bool(self.candidate_paths))
        self.export_button.setEnabled(not busy and self.result is not None)
        self.workspace.update_controls(busy)
        self.review.update_controls(busy)
        self.comparison.update_controls(busy)
        self._update_cancel_control()

    def _update_cancel_control(self):
        worker = self.inventory_worker or self.discovery_worker
        active = worker is not None and not self._scan_terminal
        requested = active and worker.cancellation.requested
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(active and not requested)
        self.cancel_button.setText("正在停止…" if requested else "取消扫描")

    def _cancel_scan(self):
        worker = self.inventory_worker or self.discovery_worker
        if worker is None or self._scan_terminal:
            return
        # Directly set a thread-safe token; a queued worker slot cannot run
        # while that worker's scan loop occupies its event loop.
        worker.cancellation.request()
        self._update_cancel_control()
        self.progress_bar.setFormat("正在停止，整理已读取记录…")
        self.overview_title.setText("正在停止扫描")
        self.overview_detail.setText("等待当前读取返回并整理部分结果，请勿关闭软件。")
        self.statusBar().showMessage("已请求取消，正在等待安全停止")

    @Slot(str)
    def _scan_phase(self, stage):
        worker = self.inventory_worker or self.discovery_worker
        if self._scan_terminal or worker is None or worker.cancellation.requested:
            return
        if stage == "读取元数据":
            self.progress_bar.setRange(0, len(self.candidate_paths))
            self.progress_bar.setFormat("正在读取：%v / %m")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat(f"正在{stage}…")
        self.statusBar().showMessage(f"正在{stage}，可取消扫描")

    def _begin_discovery(self, root: Path) -> None:
        if self._is_busy():
            return
        if self.workspace.directory is not None and not is_output_directory_allowed(root, self.workspace.directory):
            QMessageBox.warning(self, "工作目录冲突", "数据目录不能包含当前工作目录，请先更换独立工作目录。")
            self.path_edit.setText(str(self.source_root or ""))
            return
        self.source_root = root.resolve()
        self.path_edit.setText(str(self.source_root))
        self.candidate_paths = []
        self.discovery_complete = True
        self.discovery_notes = ()
        # Disabling the focused path editor may emit editingFinished again.
        # Consume the restart request before that signal can re-enter discovery.
        self._needs_rediscovery = False
        self._scan_terminal = False
        self._reset_results()
        self._set_controls_busy(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("正在递归查找 DICOM 文件…")
        show_scan_progress(self)
        self.statusBar().showMessage("正在查找候选 DICOM 文件")

        thread = QThread(self)
        worker = DiscoveryWorker(self.source_root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._discovery_completed)
        worker.cancelled.connect(self._inventory_completed)
        worker.phase_changed.connect(self._scan_phase)
        worker.failed.connect(self._discovery_failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._discovery_thread_finished)
        self.discovery_thread = thread
        self.discovery_worker = worker
        self._update_cancel_control()
        thread.start()

    @Slot(object)
    def _discovery_completed(self, paths: object) -> None:
        self._scan_terminal = True
        self._needs_rediscovery = False
        self._update_cancel_control()
        self.discovery_complete = getattr(paths, "complete", True)
        self.discovery_notes = getattr(paths, "notes", ())
        self.candidate_paths = list(paths) if isinstance(paths, list) else []
        count = len(self.candidate_paths)
        self.stats["file_count"].setText(str(count))
        self.overview_title.setText(f"已发现 {count} 个候选文件")
        self.overview_detail.setText("点击开始清点；发现范围不完整，结果只覆盖可访问目录。" if not self.discovery_complete else "点击开始清点，检查所选元数据。")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"已发现 {count} 个候选 DICOM 文件")
        show_scan_state(self, "等待扫描" if count else "未发现候选文件")
        self.statusBar().showMessage(
            "发现完成，可以开始清点" if count else "未发现候选 DICOM 文件"
        )

    @Slot(str)
    def _discovery_failed(self, message: str) -> None:
        self.comparison.scan_failed()
        self._scan_terminal = True
        self._needs_rediscovery = True
        self._update_cancel_control()
        self.error_text.insertPlainText(message + "\n")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("文件发现失败")
        show_scan_state(self, "文件发现失败", "ERROR")
        self.overview_title.setText("文件发现失败")
        self.statusBar().showMessage("文件发现失败")

    @Slot()
    def _discovery_thread_finished(self) -> None:
        self._finish_worker("discovery")

    def _start_inventory(self, allow_empty=False) -> None:
        if self._is_busy() or (not self.candidate_paths and not allow_empty):
            return
        self._scan_terminal = False
        self._reset_results()
        self.stats["file_count"].setText(str(len(self.candidate_paths)))
        self._set_controls_busy(True)
        self.progress_bar.setRange(0, len(self.candidate_paths))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("正在清点：%v / %m")
        show_scan_progress(self)
        self.statusBar().showMessage("正在读取 DICOM 元数据")

        thread = QThread(self)
        worker = InventoryWorker(self.candidate_paths, source_root=self.source_root,
                                 discovery_complete=self.discovery_complete, discovery_notes=self.discovery_notes)
        self.overview_title.setText("正在检查元数据")
        self.overview_detail.setText("读取和规则检查均在后台运行，请等待完整结果。")
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._inventory_progress)
        worker.phase_changed.connect(self._scan_phase)
        worker.records_ready.connect(self._records_ready)
        worker.error_line.connect(self._append_error)
        worker.completed.connect(self._inventory_completed)
        worker.failed.connect(self._inventory_failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._inventory_thread_finished)
        self.inventory_thread = thread
        self.inventory_worker = worker
        self._update_cancel_control()
        thread.start()

    @Slot(int, int)
    def _inventory_progress(self, processed: int, total: int) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(processed)

    @Slot(object)
    def _records_ready(self, records: object) -> None:
        if isinstance(records, list):
            self.table_model.append_records(records)
            self._ensure_selection()

    @Slot(str)
    def _append_error(self, message: str) -> None:
        self.error_text.insertPlainText(message + "\n\n")

    @Slot(object)
    def _inventory_completed(self, result: object) -> None:
        self._scan_terminal = True
        self._update_cancel_control()
        if not isinstance(result, InventoryResult):
            self._inventory_failed("后台任务返回了无效结果")
            return
        self.result = result
        self.workspace.scan_ready(result)
        self._path_to_row = {record.file_path: row for row, record in enumerate(result.records)}
        self._populate_scopes(result)
        self.proxy_model.set_allowed_paths(None)
        self.hierarchy_proxy.set_allowed_paths(None)
        self.hierarchy_model.set_result(result)
        self.tree.expandToDepth(0)
        self.table_model.set_records(result.records)
        self.table_model.set_series_labels(result.series)
        summary = result.summary
        for key in self.stats:
            self.stats[key].setText(str(getattr(summary, key)))
        self.progress_bar.setRange(0, max(1, summary.file_count))
        self.progress_bar.setValue(summary.file_count)
        complete = result.scan_run is None or result.scan_run.status == "完成"
        state = result.scan_run.status if result.scan_run else "完成"
        if not complete:
            self.candidate_paths = []
            self._needs_rediscovery = True
        label = ("扫描完成" if complete else "扫描已取消" if "取消" in state
                 else "扫描失败" if "失败" in state else "扫描未完成")
        show_scan_state(self, f"{label} · 成功 {summary.success_count}，失败 {summary.failure_count}",
                        "ERROR" if "失败" in state else "WARNING" if not complete else "")
        self.progress_bar.setFormat(f"{label}：{summary.file_count} 个文件")
        self.overview_title.setText(f"发现 {summary.anomaly_file_count} 个需核对文件" if summary.anomaly_file_count else "所选检查未发现问题")
        self.overview_detail.setText(f"{summary.issue_count} 条问题：错误 {summary.error_count}，警告 {summary.warning_count}，提示 {summary.info_count}。未完成评估 {summary.unevaluated_count} 个文件。"
                                     + ("仅表示候选清单处理完成，不代表完整IOD或像素通过。" if complete
                                        else "本批次已取消，只覆盖已读取记录。" if "取消" in state
                                        else "任务未完成，只覆盖已读取记录。" if "失败" in state
                                        else "发现范围不完整，不可作为完整结果。"))
        if not complete:
            self.overview_title.setText(f"{label} · 未完成批次")
            self.overview_detail.setText(self.overview_detail.text() + " 序列结论不能作为完整检查。重新扫描请再次确认数据目录。")
        self.overview_detail.setToolTip("\n".join(result.scan_run.notes) if result.scan_run else "")
        self.scan_notes_toggle.setText(f"扫描说明 · 问题 {summary.issue_count} / 未评估 {summary.unevaluated_count}")
        self._ensure_selection()
        self.statusBar().showMessage(
            f"{label}：成功 {summary.success_count}，失败 {summary.failure_count}"
        )
        self._set_controls_busy(self._is_busy())
        self.comparison.scan_ready(result)

    @Slot(str)
    def _inventory_failed(self, message: str) -> None:
        self.comparison.scan_failed()
        self._scan_terminal = True
        self._update_cancel_control()
        self.error_text.insertPlainText(f"任务错误：{message}\n")
        self.overview_title.setText("本次清点未完成")
        self.overview_detail.setText("已读记录仅供查看，不可导出为完整扫描结果。")
        self.progress_bar.setFormat("清点任务失败")
        show_scan_state(self, "扫描失败 · 已读记录仅供查看", "ERROR")
        self.statusBar().showMessage("清点任务失败")

    @Slot()
    def _inventory_thread_finished(self) -> None:
        self._finish_worker("inventory")

    def _export_result(self, kind: str = "files") -> None:
        if self.result is None or self.source_root is None or self._is_busy():
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "导出部分结果（CSV＋批次说明）" if self.result.scan_run and self.result.scan_run.status != "完成" else "选择独立的 CSV 输出文件夹",
            str(self.source_root.parent),
        )
        if not selected:
            return
        output_dir = Path(selected)
        if not is_output_directory_allowed(self.source_root, output_dir):
            QMessageBox.warning(
                self,
                "输出目录无效",
                "CSV 不能保存到原始 DICOM 文件夹或其子文件夹中。",
            )
            return
        self._begin_export(output_dir, kind)

    def _begin_export(self, output_dir: Path, kind: str) -> None:
        if self.result is None or self.source_root is None or self._is_busy():
            return
        thread = QThread(self)
        worker = ExportWorker(self.result, output_dir, self.source_root, kind)
        if kind == "comparison":
            if self.comparison.report is None or self.comparison.report.current_run != self.result.scan_run.run_id:
                return
            worker.comparison_report = self.comparison.report
            worker.baseline_result = self.comparison.report_baseline
        self._last_export_kind = kind
        if kind == "issues" and self.review.saved_context():
            worker.work_dir = self.workspace.directory
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._export_completed)
        worker.failed.connect(self._export_failed)
        worker.owner_ready.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._export_thread_finished)
        self.export_thread, self.export_worker = thread, worker
        self._set_controls_busy(True)
        partial = self.result.scan_run and self.result.scan_run.status != "完成"
        self.statusBar().showMessage("正在导出部分结果和未完成批次说明…" if partial else "正在后台导出完整扫描清单（不受表格筛选影响）…")
        if kind == "comparison":
            self.statusBar().showMessage("正在后台导出复检对比和范围限制…")
        thread.start()

    @Slot(object)
    def _export_completed(self, destination: object) -> None:
        self.statusBar().showMessage(f"CSV 已保存：{destination}")
        partial = self.result and self.result.scan_run and self.result.scan_run.status != "完成"
        scope = "仅含已读部分结果，请同时保留同名 .scan.json 批次说明；不能作为完整检查。" if partial else "导出包含完整扫描结果，不受当前筛选影响。"
        if getattr(self, "_last_export_kind", "") == "comparison":
            scope = "包含问题、文件、数量变化及比较限制；系统动作记录为本次对比时的实际动作。"
        QMessageBox.information(self, "导出完成", f"CSV 已保存到：\n{destination}\n{scope}")

    @Slot(str)
    def _export_failed(self, message: str) -> None:
        self.statusBar().showMessage("导出未完成，请检查输出目录")
        self.error_text.insertPlainText(message + "\n")
        QMessageBox.critical(self, "导出失败", message)

    @Slot()
    def _export_thread_finished(self) -> None:
        self._finish_worker("export")

    def _finish_worker(self, prefix: str) -> None:
        # finished precedes possible native thread-local cleanup. Keep wrappers
        # alive until the OS thread has exited; do not block the GUI waiting.
        thread = getattr(self, prefix + "_thread")
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, lambda: self._finish_worker(prefix))
            return
        setattr(self, prefix + "_worker", None)
        setattr(self, prefix + "_thread", None)
        thread.deleteLater()
        self._set_controls_busy(False)
        if prefix == "discovery":
            self.comparison.discovery_finished()
        elif prefix == "inventory":
            self.comparison.scan_finished()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._is_busy():
            QMessageBox.information(self, "任务进行中", "请等待当前任务完成后再关闭软件。")
            event.ignore()
            return
        event.accept()


def create_window_for_testing() -> MainWindow:
    """Construct a window when a QApplication already exists (test helper)."""

    if QApplication.instance() is None:
        raise RuntimeError("创建窗口前必须先创建 QApplication")
    return MainWindow()
