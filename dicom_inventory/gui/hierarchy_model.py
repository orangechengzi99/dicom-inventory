"""Qt model/view hierarchy; consumes core summaries, does not run checks."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from .table_model import AnomalyFilterProxyModel
from ..core.models import InventoryRecord, Severity


@dataclass(eq=False)
class HierarchyNode:
    label: str
    kind: str
    payload: object = None
    files: tuple[str, ...] = ()
    issues: tuple = ()
    parent_node: HierarchyNode | None = None
    row: int = 0
    children: list[HierarchyNode] = field(default_factory=list)

    @property
    def highest_severity(self):
        return max((issue.severity for issue in self.issues), key=lambda level: level.rank, default=None)

    @property
    def assessment_status(self):
        if isinstance(self.payload, InventoryRecord):
            return self.payload.assessment_status
        return "未评估" if "未评估" in getattr(self.payload, "overall_status", "") else "已检查"

    @property
    def status(self):
        if isinstance(self.payload, InventoryRecord):
            return self.payload.quality_status
        return {Severity.ERROR: "错误", Severity.WARNING: "需核对", Severity.INFO: "提示"}.get(
            self.highest_severity, "部分未评估" if self.assessment_status == "未评估" else "已检查")


class HierarchyModel(QAbstractItemModel):
    HEADERS = ("检查 / 序列 / 文件", "状态", "模态", "文件", "问题")

    def __init__(self):
        super().__init__()
        self.root = HierarchyNode("", "root")
        self.path_nodes = {}

    def set_result(self, result):
        self.beginResetModel()
        self.root = HierarchyNode("", "root")
        self.path_nodes = {}
        if result:
            records = {record.file_path: record for record in result.records}
            summaries = {series.group_key: series for series in result.series}
            series_numbers = {series.group_key: number for number, series in enumerate(result.series, 1)}
            series_for_path = {path: series for series in result.series for path in series.files}
            def child(parent, label, kind, payload, files, issues):
                node = HierarchyNode(label, kind, payload, files, issues, parent, len(parent.children))
                parent.children.append(node)
                return node
            for number, study in enumerate(result.studies, 1):
                valid = ":unassigned:" not in study.group_key
                branch = child(self.root, f"Study {number}" if valid else "未归组 · " + Path(study.files[0]).name,
                               "study", study, study.files, study.issues)
                branch_files = {}
                for path in study.files:
                    summary = series_for_path[path]
                    branch_files.setdefault(summary.group_key, []).append(path)
                for key in study.series_keys:
                    summary = summaries[key]
                    paths = tuple(branch_files[key])
                    path_set = set(paths)
                    issues = tuple(issue for issue in summary.issues if path_set.intersection(issue.affected_files))
                    series = child(branch, f"Series {series_numbers[key]}" if ":unassigned:" not in key else "未归组文件",
                                   "series", summary, paths, issues)
                    # Use projection order when available; every file stays represented.
                    order = {path: position for path, position in summary.sorted_positions}
                    for path in sorted(paths, key=lambda path: (order.get(path, float("inf")), path.casefold())):
                        record = records[path]
                        node = child(series, Path(path).name, "file", record, (path,), record.issues)
                        self.path_nodes[path] = node
        self.endResetModel()

    def node_at(self, index):
        return index.internalPointer() if index.isValid() else None

    def index_for_node(self, node):
        return self.createIndex(node.row, 0, node) if node and node is not self.root else QModelIndex()

    def index(self, row, column, parent=QModelIndex()):
        if row < 0 or column < 0 or column >= len(self.HEADERS) or (parent.isValid() and parent.column() != 0):
            return QModelIndex()
        node = self.node_at(parent) or self.root
        return self.createIndex(row, column, node.children[row]) if row < len(node.children) else QModelIndex()

    def parent(self, index):
        node = self.node_at(index)
        return self.index_for_node(node.parent_node) if node else QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() and parent.column() != 0 else len((self.node_at(parent) or self.root).children)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        node = self.node_at(index)
        if node is None:
            return None
        if role == Qt.ItemDataRole.UserRole:
            return node
        if role == Qt.ItemDataRole.ToolTipRole:
            value = node.payload
            return value.file_path if node.kind == "file" else getattr(value, node.kind + "_instance_uid", "") or node.label
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        value = node.payload
        modalities = (value.modality,) if node.kind == "file" else value.modalities
        return (node.label, node.status, "/".join(modalities) or "—", str(len(node.files)), str(len(node.issues)))[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None


class HierarchyFilterProxyModel(AnomalyFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setRecursiveFilteringEnabled(True)

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        node = model.node_at(model.index(row, 0, parent))
        if self._keyword and not any(self._keyword in Path(path).name.casefold() for path in node.files):
            return False
        if self._allowed_paths is not None and not self._allowed_paths.intersection(node.files):
            return False
        if self._anomalies_only and not node.issues:
            return False
        return not (self._severity or self._issue_type) or any(
            (not self._severity or issue.severity == self._severity) and
            (not self._issue_type or issue.issue_type == self._issue_type) for issue in node.issues)
