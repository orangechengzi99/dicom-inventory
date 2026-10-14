"""Presentation-only helpers; no DICOM reads or quality decisions."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF, QIcon
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem
from . import reference_icons as glyphs
from .reference_typography import ui_font, mono_font
from .review_focus import ReviewDelegate, paint_keyboard_hint

TEXT = "#292929"
MUTED = "#667085"
ERROR = "#B42318"
WARNING = "#B54708"


def issue_label(issue):
    name = {"SOPInstanceUID重复": "重复实例 UID"}.get(issue.issue_type, issue.issue_type)
    # A repeat UID label already names the field; do not repeat it in the heading.
    return name + (f" · {issue.field_name}" if issue.field_name and issue.code != "DUPLICATE_SOP_UID" else "")


def format_spacing(value):
    return value.replace("\\", " × ").replace("; ", " × ") + " mm" if value else "未提供"


def status_color(severity):
    return {"ERROR": ERROR, "WARNING": WARNING}.get(severity, MUTED)


def status_icon(severity):
    svg = {"WARNING": glyphs.WARN, "ERROR": glyphs.ERROR, "SUCCESS": glyphs.OK}.get(severity, glyphs.INFO.replace("#9AA3B2", MUTED))
    return glyphs.icon(svg, 18)


def toolbar_icon(kind):
    """Small local vector marks; no external icon or font request."""
    return glyphs.icon({"play": glyphs.PLAY, "folder": glyphs.FOLDER}.get(kind, glyphs.DOWNLOAD), 18)


class FileDelegate(ReviewDelegate):
    """Two text lines in one existing cell, with full text in the tooltip."""
    INSET = 10

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text
        opt.text = ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect.left(), option.rect.top(), 3, option.rect.height(), QColor("#2B5CE6"))
        rect = option.rect.adjusted(self.INSET, 0, -8, 0)
        font = ui_font(14, QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor("#1A47C8" if selected else TEXT))
        title_rect = QRectF(rect.left(), rect.top() + 5, rect.width(), 21)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, rect.width()))
        font.setWeight(QFont.Weight.Normal)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        subtitle = str(index.data(Qt.ItemDataRole.UserRole) or "未归组")
        painter.drawText(QRectF(rect.left(), rect.top() + 27, rect.width(), 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()
        paint_keyboard_hint(painter, option, index)


class NumericDelegate(ReviewDelegate):
    """Reference mono-family rendering on the existing file model."""
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setFont(mono_font(14))
        painter.setPen(QColor(TEXT))
        rect = option.rect.adjusted(10, 0, -8, 0)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()
        paint_keyboard_hint(painter, option, index)


def set_parameters(window, values=()):
    mapping = dict(values)
    for key, label in window.parameter_values.items():
        label.setText(str(mapping.get(key, "—")))




def set_detail_path(window, path="", fallback=""):
    """Only abbreviate display; the full path remains selectable and copyable."""
    from pathlib import Path
    window.full_path_text.setPlainText(path)
    window.path_toggle.setEnabled(bool(path))
    window.copy_path_button.setEnabled(bool(path))
    window.path_toggle.setVisible(bool(path))
    window.copy_path_button.setVisible(bool(path))
    window.detail_path.setToolTip(path)
    if path:
        value = Path(path)
        parent = value.parent.name
        name = value.name
        if len(parent) > 28:
            parent = parent[:18] + "…" + parent[-8:]
        if len(name) > 40:
            name = name[:26] + "…" + name[-12:]
        window.detail_path.setText(str(Path("…") / parent / name))
    else:
        window.detail_path.setText(fallback)
        window.path_toggle.setChecked(False)


def show_scan_state(window, text, severity=""):
    """Compact terminal display; does not change task or scan result state."""
    window.progress_bar.hide()
    # Counts are already visible in the summary cells. Keep the full message
    # accessible in the tooltip, including for historical/cancelled runs.
    window.scan_status.setToolTip(text)
    compact = text.split(" · ", 1)[0] if text.startswith(("扫描完成 · 成功", "扫描已取消 · 成功", "扫描失败 · 成功", "扫描未完成 · 成功")) else text
    window.scan_status.setText(compact)
    window.scan_status.setStyleSheet("color: " + status_color(severity) + ";")
    window.scan_status.show()
    badge = "ERROR" if severity == "ERROR" else "WARNING" if severity == "WARNING" else "SUCCESS" if "扫描完成" in text else "INFO"
    window.summary_icon.setPixmap(status_icon(badge).pixmap(18, 18))
    if severity:
        window.scan_notes_toggle.setChecked(True)
    elif "扫描完成" in text:
        window.scan_notes_toggle.setChecked(False)
    else:
        window.scan_notes_toggle.setText("扫描说明")


def show_scan_progress(window):
    window.scan_status.hide()
    window.summary_icon.setPixmap(status_icon("INFO").pixmap(18, 18))
    window.progress_bar.show()
