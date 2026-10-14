"""Local SVG paths and explicit-size rendering from the approved reference."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# ---------------------------------------------------------------------------
# 原始矢量（与设计稿组件逐字一致）
# ---------------------------------------------------------------------------
OK = (
    "<svg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<circle cx='8' cy='8' r='8' fill='#12A150'/>"
    "<path d='M4.9 8.2L6.9 10.2L11.1 5.85' stroke='#FFFFFF' stroke-width='1.7' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)

WARN = (
    "<svg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M7.35 1.9C7.62 1.42 8.38 1.42 8.65 1.9L15.1 13.1C15.36 13.58 15.02 14.2 14.45 14.2H1.55"
    "C0.98 14.2 0.64 13.58 0.9 13.1L7.35 1.9Z' fill='#F0A422'/>"
    "<path d='M8 5.6V9.2' stroke='#FFFFFF' stroke-width='1.7' stroke-linecap='round'/>"
    "<circle cx='8' cy='11.6' r='1' fill='#FFFFFF'/></svg>"
)

ERROR = (
    "<svg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<circle cx='8' cy='8' r='8' fill='#E5484D'/>"
    "<path d='M5.75 5.75L10.25 10.25M10.25 5.75L5.75 10.25' stroke='#FFFFFF' stroke-width='1.7' "
    "stroke-linecap='round'/></svg>"
)

INFO = (
    "<svg width='14' height='14' viewBox='0 0 14 14' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<circle cx='7' cy='7' r='6.1' stroke='#9AA3B2' stroke-width='1.3'/>"
    "<path d='M7 6.5V10.1' stroke='#9AA3B2' stroke-width='1.4' stroke-linecap='round'/>"
    "<circle cx='7' cy='4.15' r='0.9' fill='#9AA3B2'/></svg>"
)

SEARCH = (
    "<svg width='15' height='15' viewBox='0 0 15 15' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<circle cx='6.8' cy='6.8' r='4.65' stroke='#9AA3B2' stroke-width='1.5'/>"
    "<path d='M10.4 10.4L13.4 13.4' stroke='#9AA3B2' stroke-width='1.5' stroke-linecap='round'/></svg>"
)

PLAY = (
    "<svg width='12' height='12' viewBox='0 0 12 12' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M3.2 1.9L10.1 6L3.2 10.1V1.9Z' fill='#FFFFFF'/></svg>"
)

FOLDER = (
    "<svg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M1.9 4.3C1.9 3.55 2.5 2.95 3.25 2.95H5.95L7.3 4.55H12.75C13.5 4.55 14.1 5.15 14.1 5.9V11.7"
    "C14.1 12.45 13.5 13.05 12.75 13.05H3.25C2.5 13.05 1.9 12.45 1.9 11.7V4.3Z' "
    "stroke='#3A4150' stroke-width='1.3' stroke-linejoin='round'/></svg>"
)

DOWNLOAD = (
    "<svg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M8 2.2V9.9' stroke='#3A4150' stroke-width='1.35' stroke-linecap='round'/>"
    "<path d='M4.85 6.9L8 10.05L11.15 6.9' stroke='#3A4150' stroke-width='1.35' "
    "stroke-linecap='round' stroke-linejoin='round'/>"
    "<path d='M2.9 13.1H13.1' stroke='#3A4150' stroke-width='1.35' stroke-linecap='round'/></svg>"
)

CHEVRON_DOWN = (
    "<svg width='14' height='14' viewBox='0 0 14 14' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M3.6 5.4L7 8.8L10.4 5.4' stroke='#667085' stroke-width='1.5' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)

CHEVRON_RIGHT = (
    "<svg width='14' height='14' viewBox='0 0 14 14' fill='none' xmlns='http://www.w3.org/2000/svg'>"
    "<path d='M5.4 3.6L8.8 7L5.4 10.4' stroke='#667085' stroke-width='1.5' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _render_image(svg: str, size: int, dpr_key: int) -> QImage:
    """Render physical pixels on a DPR=1 CPU canvas before tagging the pixmap.

    Consumers in reference_controls/status_delegate/display_helpers draw into
    explicit logical target rectangles; icon labels use fixed, scaled bounds.
    This keeps physical raster size separate from logical layout size. Local
    visual tests cover 1x/2x rendering on the installed PySide6 6.11.2 only.
    """
    dpr = dpr_key / 100.0
    physical = max(1, int(round(size * dpr)))

    # dpr = 1 的画布：逻辑坐标 == 设备像素，不存在解释空间
    canvas = QImage(physical, physical, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.setDevicePixelRatio(1.0)
    canvas.fill(Qt.GlobalColor.transparent)

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # 目标矩形显式给出，且是「物理尺寸」——与上面 dpr=1 的画布严格对应
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()

    return canvas


def _render(svg: str, size: int, dpr_key: int) -> QPixmap:
    """Cache CPU images, never QApplication-bound pixmaps across shutdown."""
    pixmap = QPixmap.fromImage(_render_image(svg, size, dpr_key))
    dpr = dpr_key / 100.0
    pixmap.setDevicePixelRatio(dpr)   # 交给消费端按逻辑尺寸使用
    return pixmap


def _dpr() -> float:
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    return float(screen.devicePixelRatio()) if screen else 1.0


def pixmap(svg: str, size: int) -> QPixmap:
    """把 SVG 渲染成指定逻辑尺寸的位图，自动适配高 DPI。"""
    return _render(svg, size, int(round(_dpr() * 100)))


def icon(svg: str, size: int) -> QIcon:
    return QIcon(pixmap(svg, size))


# ---------------------------------------------------------------------------
# 语义映射
# ---------------------------------------------------------------------------
# 状态 → 图标。三种状态各有一个专用图标，绝不靠颜色单独传达信息。
STATE_ICON = {"ok": OK, "warn": WARN, "error": ERROR}
