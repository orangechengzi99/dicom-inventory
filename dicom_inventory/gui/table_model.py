"""Efficient model/view classes for large inventory result sets."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..core.models import InventoryRecord, Severity
from .display_helpers import issue_label

ColumnGetter = Callable[[InventoryRecord], str]


def _missing(value: str) -> str:
    return value if value else "—"


COLUMNS: tuple[tuple[str, ColumnGetter], ...] = (
    ("文件", lambda item: Path(item.file_path).name),
    ("模态", lambda item: _missing(item.modality)),
    ("矩阵", lambda item: f"{item.rows or '—'} × {item.columns or '—'}"),
    ("状态", lambda item: item.quality_status),
    ("异常摘要", lambda item: "; ".join(dict.fromkeys(issue_label(issue) for issue in item.issues)) or
     ("序列检查未完成" if any(check.code == "SCAN_INCOMPLETE" for check in item.series_checks) else "未发现问题")),
    ("读取状态", lambda item: item.status),
    ("异常类型", lambda item: "; ".join(item.anomaly_types)),
    ("SOPClassUID", lambda item: _missing(item.sop_class_uid)),
    ("NumberOfFrames", lambda item: item.number_of_frames or "未提供"),
    ("StudyInstanceUID", lambda item: _missing(item.study_instance_uid)),
    ("SeriesInstanceUID", lambda item: _missing(item.series_instance_uid)),
    ("SOPInstanceUID", lambda item: _missing(item.sop_instance_uid)),
    ("Rows", lambda item: _missing(item.rows)),
    ("Columns", lambda item: _missing(item.columns)),
    ("PixelSpacing", lambda item: _missing(item.pixel_spacing)),
    ("SliceThickness", lambda item: _missing(item.slice_thickness)),
    ("ImagePositionPatient", lambda item: _missing(item.image_position_patient)),
    ("ImageOrientationPatient", lambda item: _missing(item.image_orientation_patient)),
    ("InstanceNumber", lambda item: _missing(item.instance_number)),
    ("RescaleSlope", lambda item: _missing(item.rescale_slope)),
    ("RescaleIntercept", lambda item: _missing(item.rescale_intercept)),
    ("缺失标签", lambda item: "; ".join(item.missing_tags)),
    ("重复实例", lambda item: "是" if item.is_duplicate else "否"),
    ("错误信息", lambda item: item.error_message),
)


class InventoryTableModel(QAbstractTableModel):
    """Table model that stores records without per-cell widgets."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[InventoryRecord] = []
        self.series_labels: dict[str, str] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.records)):
            return None
        record = self.records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[index.column()][1](record)
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 0:
                return record.file_path + "\n" + self.series_labels.get(record.file_path, "序列待汇总" if record.series_instance_uid else "未归组")
            return COLUMNS[index.column()][1](record)
        if role == Qt.ItemDataRole.UserRole and index.column() == 0:
            return self.series_labels.get(record.file_path, "序列待汇总" if record.series_instance_uid else "未归组")
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 3:
            from .display_helpers import status_color
            return QColor(status_color(record.highest_severity))
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(COLUMNS):
            return COLUMNS[section][0]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self.records.clear()
        self.series_labels.clear()
        self.endResetModel()

    def append_records(self, records: list[InventoryRecord]) -> None:
        if not records:
            return
        first = len(self.records)
        last = first + len(records) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self.records.extend(records)
        self.endInsertRows()

    def set_records(self, records: list[InventoryRecord]) -> None:
        self.beginResetModel()
        self.records = list(records)
        self.endResetModel()

    def set_series_labels(self, series) -> None:
        self.series_labels = {path: (f"序列 {number:02}" if ":unassigned:" not in summary.group_key else "未归组")
                              for number, summary in enumerate(series, 1) for path in summary.files}
        if self.records:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.records)-1, 0))

    def record_at(self, row: int) -> InventoryRecord | None:
        return self.records[row] if 0 <= row < len(self.records) else None


class AnomalyFilterProxyModel(QSortFilterProxyModel):
    """Optionally retain only records that have one or more anomalies."""

    def __init__(self) -> None:
        super().__init__()
        self._anomalies_only = False
        self._severity = ""
        self._issue_type = ""
        self._allowed_paths = None
        self._keyword = ""

    def set_keyword(self, keyword: str) -> None:
        self._set_filter("_keyword", keyword.strip().casefold())

    def set_allowed_paths(self, paths: frozenset[str] | None):
        self._set_filter("_allowed_paths", paths)

    def set_severity(self, severity: str) -> None:
        self._set_filter("_severity", severity)

    def set_issue_type(self, issue_type: str) -> None:
        self._set_filter("_issue_type", issue_type)

    def set_anomalies_only(self, enabled: bool) -> None:
        self._set_filter("_anomalies_only", enabled)

    def _set_filter(self, name: str, value) -> None:
        if getattr(self, name) == value:
            return
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            setattr(self, name, value)
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:  # Qt 6.8 compatibility.
            setattr(self, name, value)
            self.invalidateFilter()

    def filterAcceptsRow(  # noqa: N802
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, InventoryTableModel):
            return True
        record = model.record_at(source_row)
        if record is None:
            return False
        if self._keyword and self._keyword not in Path(record.file_path).name.casefold():
            return False
        if self._allowed_paths is not None and record.file_path not in self._allowed_paths:
            return False
        if self._anomalies_only and not record.is_anomaly:
            return False
        if not self._severity and not self._issue_type:
            return True
        # Both controls must match the SAME issue, not unrelated issues on a file.
        return any((not self._severity or issue.severity == self._severity) and
                   (not self._issue_type or issue.issue_type == self._issue_type)
                   for issue in record.issues)
