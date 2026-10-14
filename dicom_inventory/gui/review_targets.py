"""File targets presented from existing issue metadata; no filesystem reads."""
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout,
    QLabel, QListView, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .display_helpers import issue_label
from .review_focus import install_review_focus


def relative_file_label(path, source_root=None):
    if source_root:
        try:
            return Path(path).relative_to(Path(source_root)).as_posix()
        except ValueError:
            pass
    return str(path)


def file_preview(paths, source_root=None):
    if not paths:
        return "无关联文件"
    names = [relative_file_label(path, source_root) for path in paths[:2]]
    if len(paths) == 1:
        return names[0]
    return f"{len(paths)} 个文件 · " + "、".join(names) + (" 等" if len(paths) > 2 else "")


class TargetFilesModel(QAbstractListModel):
    """Keep all target paths, render only rows requested by the view."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.paths, self.source_root = (), None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.paths)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.paths):
            return None
        path = self.paths[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return relative_file_label(path, self.source_root)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole):
            return path
        return None

    def replace(self, paths, source_root=None):
        self.beginResetModel()
        self.paths, self.source_root = tuple(paths), source_root
        self.endResetModel()


class IssueTargetsPanel(QWidget):
    locate_requested = Signal(str)

    def __init__(self, parent=None, *, allow_locate=False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.title = QLabel("选择问题后查看关联文件")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.files = QListView()
        self.files.setAccessibleName("所选问题的全部关联文件")
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.setFixedHeight(84)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.model = TargetFilesModel(self.files)
        self.files.setModel(self.model)
        install_review_focus(self.files)
        self.path = QPlainTextEdit()
        self.path.setAccessibleName("所选关联文件的完整路径，可查看和复制")
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("选择上方文件，查看完整路径")
        self.path.setFixedHeight(84)
        row = QHBoxLayout()
        row.addWidget(self.title, 1)
        self.copy_button = QPushButton("复制完整路径")
        self.copy_button.clicked.connect(self.copy_path)
        self.locate_button = QPushButton("定位到主审阅")
        self.locate_button.setVisible(allow_locate)
        self.locate_button.clicked.connect(self.locate)
        row.addWidget(self.copy_button)
        row.addWidget(self.locate_button)
        layout.addLayout(row)
        files_row = QHBoxLayout()
        files_row.addWidget(self.files, 1)
        files_row.addWidget(self.path, 2)
        layout.addLayout(files_row)
        self.files.selectionModel().currentChanged.connect(self._selected)
        self._selected(QModelIndex())

    def set_issues(self, issues, source_root=None):
        paths = tuple(dict.fromkeys(path for issue in issues for path in issue.affected_files))
        if len(issues) == 1:
            issue = issues[0]
            scope = {"file": "文件级", "series": "序列级"}.get(issue.scope_kind, "文件组")
            self.title.setText(f"{issue_label(issue)} · {scope} · {len(paths)} 个关联文件")
        elif issues:
            self.title.setText(f"所选 {len(issues)} 项问题 · {len(paths)} 个关联文件（去重）")
        else:
            self.title.setText("选择问题后查看关联文件")
        self.model.replace(paths, source_root)
        self.files.setCurrentIndex(self.model.index(0))
        self._selected(self.files.currentIndex())

    def _selected(self, index, *_):
        path = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else ""
        self.path.setPlainText(path or "")
        self.copy_button.setEnabled(bool(path))
        self.locate_button.setEnabled(bool(path))

    def copy_path(self):
        path = self.path.toPlainText()
        if path:
            QApplication.clipboard().setText(path)
            self.copy_button.setToolTip("完整路径已复制；分享前请核对隐私信息")

    def locate(self):
        path = self.path.toPlainText()
        if path:
            self.locate_requested.emit(path)
