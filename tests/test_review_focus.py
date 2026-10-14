"""Real mouse/key interactions on all result columns; synthetic DICOM only."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QWidget
from shiboken6 import delete

from test_ui_visual import build_sample
from test_ui_gray import assert_no_purple
from dicom_inventory.gui.review_focus import ReviewFocusFilter


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("base_parent", [False, True])
def test_focus_observer_ignores_missing_or_base_widget_parent(app, base_parent):
    parent = QWidget() if base_parent else None
    observer = ReviewFocusFilter(parent)
    try:
        assert observer.eventFilter(parent, QFocusEvent(QEvent.Type.FocusOut)) is False
        assert observer.eventFilter(parent, QEvent(QEvent.Type.DeferredDelete)) is False
    finally:
        delete(parent if parent is not None else observer)


@pytest.fixture
def review_window(app, tmp_path, make_dicom):
    window, result = build_sample(tmp_path, make_dicom)
    window.resize(1320, 860)
    window.show()
    window.table.setFocus()
    app.processEvents()
    yield window, result
    window.table.setFocus()
    assert not window._is_busy()
    window.close()
    delete(window)
    app.processEvents()


def save(window, name):
    shot = window.grab()
    assert_no_purple(shot)
    output = os.environ.get("DICOM_FOCUS_OUTPUT")
    if output:
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        assert shot.save(str(folder / (name + ".png")))


def logical_pixel(image, x, y):
    scale = image.devicePixelRatio()
    return image.pixelColor(round(x*scale), round(y*scale))


@pytest.mark.parametrize("column", range(5))
def test_mouse_clicks_do_not_draw_cell_frames(app, review_window, column):
    window, result = review_window
    index = window.proxy_model.index(3, column)
    view = window.table
    view.scrollTo(index)
    app.processEvents()
    rect = view.visualRect(index)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    app.processEvents()
    assert view.currentIndex() == index
    assert view.selectionModel().isRowSelected(3, index.parent())
    assert window.detail_title.text() == Path(result.records[3].file_path).name
    option = QStyleOptionViewItem()
    option.widget, option.rect = view, rect
    option.state |= QStyle.StateFlag.State_HasFocus
    delegate = view.itemDelegateForColumn(column) or view.itemDelegate()
    delegate.initStyleOption(option, index)
    assert not option.state & QStyle.StateFlag.State_HasFocus
    assert not view.property("reviewKeyboardFocus")
    image = view.viewport().grab().toImage()
    for x in (rect.left()+6, rect.right()-6):
        assert logical_pixel(image, x, rect.top()+2) == QColor("#EDF2FE")
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    app.processEvents()
    assert view.state() != view.State.EditingState
    assert not index.flags() & Qt.ItemFlag.ItemIsEditable
    save(window, f"mouse-column-{column}")


def test_tree_mouse_selection_and_expansion_remain_native(app, review_window):
    window, _ = review_window
    window.view_tabs.setCurrentIndex(1)
    view = window.tree
    view.expandAll()
    index = window.hierarchy_proxy.index(0, 0)
    view.scrollTo(index)
    app.processEvents()
    for column in range(5):
        target = index.siblingAtColumn(column)
        rect = view.visualRect(target)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        app.processEvents()
        assert view.currentIndex() == target
        option = QStyleOptionViewItem()
        option.widget = view
        option.state |= QStyle.StateFlag.State_HasFocus
        (view.itemDelegateForColumn(column) or view.itemDelegate()).initStyleOption(option, target)
        assert not option.state & QStyle.StateFlag.State_HasFocus
        assert not view.property("reviewKeyboardFocus")
    view.setCurrentIndex(index)
    QTest.keyClick(view, Qt.Key.Key_Left)
    assert not view.isExpanded(index)
    QTest.keyClick(view, Qt.Key.Key_Right)
    assert view.isExpanded(index)
    assert view.property("reviewKeyboardFocus")
    save(window, "tree-keyboard")


def test_keyboard_hint_is_a_line_not_a_mouse_frame(app, review_window):
    window, _ = review_window
    view = window.table
    index = window.proxy_model.index(3, 2)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
    QTest.keyClick(view, Qt.Key.Key_Down)
    app.processEvents()
    assert view.hasFocus() and view.currentIndex().row() == 4
    assert view.property("reviewKeyboardFocus")
    rect = view.visualRect(view.currentIndex())
    image = view.viewport().grab().toImage()
    assert logical_pixel(image, rect.center().x(), rect.bottom()-3) == QColor("#667085")
    assert logical_pixel(image, rect.right()-6, rect.top()+2) == QColor("#EDF2FE")
    save(window, "keyboard-line")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    app.processEvents()
    assert not view.property("reviewKeyboardFocus")
    image = view.viewport().grab().toImage()
    assert logical_pixel(image, rect.center().x(), rect.bottom()-3) == QColor("#EDF2FE")
    save(window, "mouse-after-keyboard")
