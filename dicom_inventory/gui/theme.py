"""Reference-led light workbench; native popups remain neutral and accessible."""
from pathlib import Path
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QComboBox

STYLE = """
QMainWindow, QWidget { font-size: 14px; font-weight: 400; color: #292929; }
QMainWindow { background: #F5F5F5; }
QLabel#secondary, QLabel#parameterLabel, QStatusBar { color: #666666; font-size: 12px; }
QLabel#productTitle { font-size: 18px; font-weight: 600; }
QLabel#overviewTitle { font-size: 16px; font-weight: 600; }
QLabel#sectionTitle, QLabel#statValue { font-size: 14px; font-weight: 400; }
QLabel#fileTitle { font-size: 18px; font-weight: 600; }
QLabel#issueHeading { font-size: 14px; font-weight: 600; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QTableView, QTreeView, QListView {
    background: #FFFFFF; color: #292929; border: 1px solid #DDDDDD; border-radius: 4px;
    selection-background-color: #ECECEC; selection-color: #292929;
}
QLineEdit { padding: 8px; }
QPushButton, QComboBox { padding: 7px 10px; min-height: 22px; }
QPushButton { background: #FFFFFF; color: #292929; border: 1px solid #CCCCCC; border-radius: 4px; }
QPushButton:hover { background: #F4F4F4; }
QPushButton:pressed { background: #E7E7E7; }
QPushButton#primaryAction { background: #292929; color: #FFFFFF; border: 1px solid #292929; }
QPushButton#primaryAction:hover { background: #424242; }
QPushButton:disabled { background: #EEEEEE; color: #747474; border-color: #DDDDDD; }
QPushButton#primaryAction:disabled { background: #EEEEEE; color: #747474; border-color: #DDDDDD; }
QLineEdit:focus, QComboBox:focus, QPushButton:focus, QToolButton:focus, QCheckBox:focus,
QListView:focus, QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid #555555; }
QCheckBox { spacing: 8px; padding: 6px 2px; color: #292929; border: 1px solid transparent; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 20px; border: none; }
QComboBox::down-arrow { image: url("@ARROW@"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #FFFFFF; color: #292929; selection-background-color: #ECECEC; }
QTableView, QTreeView { alternate-background-color: #FAFAFA; border: none; border-radius: 0; }
QTreeView::item { min-height: 34px; padding-top: 5px; padding-bottom: 5px; border-bottom: 1px solid #EEEEEE; }
QTreeView::item:selected, QTableView::item:selected { background: #ECECEC; color: #292929; }
QTreeView::item:focus, QTableView::item:focus { border: 1px solid #555555; }
QTableView[reviewNoItemFrame="true"]::item:focus, QTreeView[reviewNoItemFrame="true"]::item:focus,
QListView[reviewNoItemFrame="true"]::item:focus {
    border: none; border-bottom: 1px solid #EEEEEE;
}
QListView[reviewNoItemFrame="true"]:focus { border: 1px solid #DDDDDD; }
QTableView::item { padding-left: 10px; border-bottom: 1px solid #EEEEEE; }
QHeaderView::section { background: #F7F7F7; color: #666666; border: none; padding: 10px; font-weight: 400; }
QWidget#detailBody, QWidget#resultPanel { background: #FFFFFF; }
QWidget#parameterPanel { background: #FFFFFF; border-top: 1px solid #E4E4E4; border-bottom: 1px solid #E4E4E4; }
QScrollArea { border: 1px solid #DDDDDD; background: #FFFFFF; border-radius: 4px; }
QToolButton { border: 1px solid transparent; padding: 5px 2px; color: #292929; text-align: left; }
QToolButton:hover { background: #F4F4F4; }
QProgressBar { background: #E8E8E8; border: none; border-radius: 3px; color: #292929; text-align: center; font-size: 12px; }
QProgressBar::chunk { background: #D5D5D5; border-radius: 3px; }
QSplitter::handle { background: transparent; width: 16px; }
QMenu { background: #FFFFFF; color: #292929; border: 1px solid #CCCCCC; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #ECECEC; }
"""
STYLE = STYLE.replace("@ARROW@", Path(__file__).with_name("chevron.svg").as_posix())

# Latest approved reference: white header/surfaces, quiet blue selection/tabs.
# Retain the original control/popup rules and semantic colors for all dialogs.
STYLE += """
QMainWindow, QWidget#workspaceContent { background: #F4F5F7; }
QWidget#productHeader { background: #FFFFFF; border-bottom: 1px solid #E3E6EB; }
QLabel#productTitle { font-size: 18px; font-weight: 600; }
QLabel#readOnlyBadge { background: #F2F4F7; border: 1px solid #E3E6EB; border-radius: 6px; padding: 6px 12px; color: #667085; font-size: 12px; }
QLabel#overviewTitle { font-size: 14px; font-weight: 400; }
QLabel#secondary, QLabel#parameterLabel { color: #667085; }
QWidget#summaryPanel { background: #FFFFFF; border: 1px solid #E3E6EB; border-radius: 10px; }
QFrame#summaryDivider { background: #E3E6EB; border: none; }
QLabel#statValue { font-weight: 600; }
QLabel#statValue { font-size: 15px; }
QLabel#anomalyCount { color: #B54708; font-size: 15px; font-weight: 600; }
QPushButton, QComboBox, QLineEdit { border-radius: 8px; border-color: #D5D9E0; }
QPushButton#primaryAction { background: #16181D; border-color: #16181D; }
QTabBar#resultTabs { background: transparent; border-bottom: 1px solid #E3E6EB; }
QTabBar#resultTabs::tab { background: transparent; color: #667085; border: none; border-bottom: 2px solid transparent; padding: 10px 18px 10px 0; margin-right: 20px; }
QTabBar#resultTabs::tab:selected { color: #2B5CE6; border-bottom-color: #2B5CE6; }
QTabBar#resultTabs::tab:focus { outline: none; border-top: 1px solid #555555; }
QWidget#resultPanel { background: transparent; }
QWidget#tablePanel { background: #FFFFFF; border: 1px solid #E3E6EB; border-radius: 10px; }
QLabel#sectionTitle { padding: 8px 12px; color: #667085; font-size: 12px; }
QTableView, QTreeView { background: #FFFFFF; selection-background-color: #EDF2FE; selection-color: #292929; }
QTableView::item:selected, QTreeView::item:selected { background: #EDF2FE; color: #292929; }
QHeaderView::section { background: #F8F9FB; color: #667085; border-bottom: 1px solid #E3E6EB; padding: 10px; }
QTableView::item, QTreeView::item { border-bottom-color: #E3E6EB; }
QScrollArea#detailScroll { background: transparent; border: none; }
QWidget#detailHolder { background: #F4F5F7; }
QWidget#detailBody { background: #FFFFFF; border: 1px solid #E3E6EB; border-radius: 10px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #C3C9D4; border-radius: 4px; background: #FFFFFF; }
QCheckBox::indicator:checked { background: #2B5CE6; border-color: #2B5CE6; }
QCheckBox::indicator:disabled { border-color: #D5D9E0; background: #F2F4F7; }
QPushButton::menu-indicator { subcontrol-position: right center; subcontrol-origin: padding; right: 4px; }
QScrollBar:vertical { background: #F4F5F7; width: 10px; margin: 0; border: none; }
QScrollBar::handle:vertical { background: #D3D8E0; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #B9C0CB; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QWidget#issuePanel { background: #F8F9FB; border: 1px solid #E3E6EB; border-radius: 8px; }
QWidget#issuePanel[tone="WARNING"] { background: #FFFAEB; border-color: #F5D68A; }
QWidget#issuePanel[tone="ERROR"] { background: #FEF3F2; border-color: #F7C7C4; }
QWidget#issuePanel[tone="WARNING"] QLabel { color: #8A5A12; }
QWidget#issuePanel[tone="ERROR"] QLabel { color: #912018; }
QWidget#parameterPanel { border-color: #EDEFF3; }
QStatusBar { background: #FAFBFC; border-top: 1px solid #E3E6EB; padding: 6px 20px; }
"""




def apply_palette(window):
    """Keep system accent colors out of inherited controls and popup windows."""
    palette = QPalette(window.palette())
    colors = {
        QPalette.ColorRole.Window: "#F4F5F7", QPalette.ColorRole.Base: "#FFFFFF",
        QPalette.ColorRole.AlternateBase: "#FAFAFA", QPalette.ColorRole.Button: "#FFFFFF",
        QPalette.ColorRole.Text: "#292929", QPalette.ColorRole.WindowText: "#292929",
        QPalette.ColorRole.ButtonText: "#292929", QPalette.ColorRole.Highlight: "#ECECEC",
        QPalette.ColorRole.HighlightedText: "#292929", QPalette.ColorRole.Link: "#292929",
        QPalette.ColorRole.LinkVisited: "#292929", QPalette.ColorRole.Accent: "#555555",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role, color in colors.items():
            palette.setColor(group, role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#747474"))
    window.setPalette(palette)
    popup_palette = QPalette(palette)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        popup_palette.setColor(group, QPalette.ColorRole.Window, QColor("#FFFFFF"))
    for combo in window.findChildren(QComboBox):
        combo.ensurePolished()
        combo.view().window().ensurePolished()
        combo.view().ensurePolished()
        combo.setPalette(palette)
        combo.view().setPalette(popup_palette)
        combo.view().window().setPalette(popup_palette)
