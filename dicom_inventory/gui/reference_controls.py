"""Reference paint geometry on native, keyboard-accessible Qt primitives."""
from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QPushButton, QStyle, QStyleOptionButton, QWidget

from .reference_icons import pixmap
from .reference_typography import ui_font


class IconTextButton(QPushButton):
    """Reuse reference spacing, but retain disabled labels and native menus."""
    def __init__(self, text, svg, *, primary=False):
        super().__init__(text)
        self.svg = svg
        self.primary = primary
        self.icon_gap = 8
        self.icon_size = 16
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        width = QFontMetricsF(ui_font(14)).horizontalAdvance(self.text())
        return QSize(round(width + 28 + self.icon_size + self.icon_gap + (18 if self.menu() else 0)), 40)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text, option.icon = "", QIcon()
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = "#FFFFFF" if self.primary else "#3A4150"
        svg = self.svg
        if not self.isEnabled():
            color = "#747474"
            svg = svg.replace("#FFFFFF", color).replace("#3A4150", color)
        painter.drawPixmap(QRect(14, (self.height()-16)//2, 16, 16), pixmap(svg, 16))
        painter.setFont(ui_font(14))
        painter.setPen(QColor(color))
        painter.drawText(QRect(38, 0, self.width()-38-(22 if self.menu() else 10), self.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        painter.end()


class TickCheckBox(QCheckBox):
    """Ordinary checkbox semantics with the reference's vector checkmark."""
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        box = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        if box.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#FFFFFF"), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(box.x()+x*box.width()/16, box.y()+y*box.height()/16)
                                       for x,y in ((4.9,8.2),(6.9,10.2),(11.1,5.85))]))
        painter.end()


class ContentHolder(QWidget):
    """Use the actual wrapped height, not an unwrapped label minimum hint."""
    def minimumSizeHint(self):
        layout = self.layout()
        height = layout.heightForWidth(max(1, self.width())) if layout else 0
        return QSize(0, max(0, height))
