"""Aligned vector status marks, painted by one delegate (no cell widgets)."""
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..core.models import Severity
from .table_model import InventoryTableModel
from .display_helpers import status_color
from . import reference_icons as glyphs
from .reference_typography import ui_font
from .review_focus import ReviewDelegate, paint_keyboard_hint


class StatusDelegate(ReviewDelegate):
    ICON_SIZE = 16
    GAP = 8
    INSET = 10

    def paint(self, painter, option, index):
        model = index.model()
        source = model.mapToSource(index) if hasattr(model, "mapToSource") else index
        table_model = source.model()
        record = table_model.record_at(source.row()) if isinstance(table_model, InventoryTableModel) else source.data(Qt.ItemDataRole.UserRole)
        if record is None:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        unassessed = record.assessment_status == "未评估" or "未评估" in text
        severity = record.highest_severity
        svg = glyphs.WARN if severity == Severity.WARNING else glyphs.ERROR if severity == Severity.ERROR else glyphs.INFO.replace("#9AA3B2", "#667085") if severity == Severity.INFO or unassessed else glyphs.OK
        color = QColor(status_color(severity) if severity or unassessed else "#067647")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = option.rect.left() + self.INSET
        y = option.rect.top() + (option.rect.height()-self.ICON_SIZE)//2
        painter.drawPixmap(QRect(x, y, self.ICON_SIZE, self.ICON_SIZE), glyphs.pixmap(svg, self.ICON_SIZE))
        painter.setPen(color)
        painter.setFont(ui_font(14))
        text_rect = QRectF(option.rect).adjusted(self.INSET + self.ICON_SIZE + self.GAP, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(text_rect.width())))
        painter.restore()
        paint_keyboard_hint(painter, option, index)
