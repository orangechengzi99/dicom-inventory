"""Row-based mouse selection with a small keyboard-only position cue."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate


class ReviewFocusFilter(QObject):
    """Observe input modality without consuming any native navigation events."""
    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                        QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            return False
        if QCoreApplication.closingDown():
            return False
        view = self.parent()
        # During Qt teardown the parent may already be a base QWidget shell.
        # This observer must never repaint a view which is being destroyed.
        if not isinstance(view, QAbstractItemView):
            return False
        if kind == QEvent.Type.MouseButtonPress:
            view.setProperty("reviewKeyboardFocus", False)
        elif kind == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Shift, Qt.Key.Key_Control,
                                   Qt.Key.Key_Alt, Qt.Key.Key_Meta):
                view.setProperty("reviewKeyboardFocus", True)
        elif kind == QEvent.Type.FocusIn:
            if event.reason() in (Qt.FocusReason.TabFocusReason,
                                  Qt.FocusReason.BacktabFocusReason,
                                  Qt.FocusReason.ShortcutFocusReason):
                view.setProperty("reviewKeyboardFocus", True)
            elif event.reason() == Qt.FocusReason.MouseFocusReason:
                view.setProperty("reviewKeyboardFocus", False)
        viewport = view.viewport()
        if viewport is not None:
            viewport.update()
        return False


def install_review_focus(view):
    view.setProperty("reviewNoItemFrame", True)
    view.setProperty("reviewKeyboardFocus", False)
    view._review_focus_filter = ReviewFocusFilter(view)
    view.installEventFilter(view._review_focus_filter)
    view.viewport().installEventFilter(view._review_focus_filter)
    view.setItemDelegate(ReviewDelegate(view))


def paint_keyboard_hint(painter, option, index):
    view = option.widget
    if (view is None or not view.hasFocus()
            or not view.property("reviewKeyboardFocus")
            or view.currentIndex() != index):
        return
    painter.save()
    painter.setPen(QPen(QColor("#667085"), 1))
    rect = option.rect
    painter.drawLine(rect.left()+8, rect.bottom()-3, rect.right()-8, rect.bottom()-3)
    painter.restore()


class ReviewDelegate(QStyledItemDelegate):
    """Suppress both native and QSS item focus frames, not focus itself."""
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        paint_keyboard_hint(painter, option, index)
