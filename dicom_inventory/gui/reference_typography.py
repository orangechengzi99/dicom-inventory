"""Font-family fallback adapted from the user-provided PySide6 reference."""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

# 桌面端 UI 正文：优先 Segoe UI（拉丁精致），中文回落到雅黑
UI_FAMILIES = [
    "Segoe UI",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "sans-serif",
]

# 数值与等宽：Windows 11 自带 Cascadia Mono，Win10 回落到 Consolas
MONO_FAMILIES = [
    "Cascadia Mono",
    "Consolas",
    "Cascadia Code",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Courier New",
    "monospace",
]


def _first_available(families: list[str]) -> list[str]:
    """返回系统中实际存在的字体族，顺序保持不变。

    全都不可用时保留最后一个（通用别名），交给 Qt 自己兜底。
    """
    installed = {name.lower() for name in QFontDatabase.families()}
    hits = [f for f in families if f.lower() in installed]
    return hits or families[-1:]


_FAMILIES_CACHE: dict[str, list[str]] = {}


def resolve(kind: str = "ui") -> list[str]:
    """取得可用的字体族链。kind 取 "ui" 或 "mono"。"""
    if kind not in _FAMILIES_CACHE:
        _FAMILIES_CACHE[kind] = _first_available(
            MONO_FAMILIES if kind == "mono" else UI_FAMILIES
        )
    return _FAMILIES_CACHE[kind]


def make_font(size: float, weight: QFont.Weight = QFont.Weight.Normal,
              mono: bool = False) -> QFont:
    """构造带降级链的字体对象。"""
    font = QFont()
    font.setFamilies(resolve("mono" if mono else "ui"))
    font.setPixelSize(round(size))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def ui_font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    return make_font(size, weight, mono=False)


def mono_font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """等宽数字字体 —— 用于矩阵、像素间距、层厚、实例号。"""
    return make_font(size, weight, mono=True)
