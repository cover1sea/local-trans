"""範囲プリセットの管理ダイアログと、範囲を一瞬表示する枠。"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QKeySequenceEdit, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .capture import Monitor
from .hotkeys import parse_hotkey


def region_text(r: list[int]) -> str:
    return f"({r[0]}, {r[1]})  {r[2] - r[0]}×{r[3] - r[1]}"


class PresetDialog(QDialog):
    """名前・ショートカットの編集、並べ替え、削除。"""

    def __init__(self, presets: list[dict], reserved_keys: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("範囲プリセット")
        self.resize(620, 360)
        self.reserved = {k for k in reserved_keys if k}
        self.rows: list[dict] = [dict(p) for p in presets]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("ショートカットを設定すると、ゲーム中でもそのキーで直接その範囲を翻訳できます。"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["名前", "ショートカット", "範囲 (位置・大きさ)"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, fn in (("上へ", lambda: self._move(-1)), ("下へ", lambda: self._move(1)),
                         ("削除", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.err = QLabel()
        self.err.setStyleSheet("color: #d33")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(0)
        for p in self.rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(p.get("name", "")))
            ke = QKeySequenceEdit(QKeySequence(p.get("hotkey", "")))
            ke.setMaximumSequenceLength(1)
            ke.setClearButtonEnabled(True)
            self.table.setCellWidget(r, 1, ke)
            it = QTableWidgetItem(region_text(p["region"]))
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 2, it)

    def _collect(self) -> None:
        for r, p in enumerate(self.rows):
            p["name"] = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            ke: QKeySequenceEdit = self.table.cellWidget(r, 1)
            p["hotkey"] = ke.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _move(self, d: int) -> None:
        r = self.table.currentRow()
        if r < 0 or not 0 <= r + d < len(self.rows):
            return
        self._collect()
        self.rows[r], self.rows[r + d] = self.rows[r + d], self.rows[r]
        self._fill()
        self.table.selectRow(r + d)

    def _delete(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        self._collect()
        del self.rows[r]
        self._fill()

    def _accept(self) -> None:
        self._collect()
        seen: set[str] = set()
        for i, p in enumerate(self.rows):
            if not p["name"]:
                p["name"] = f"範囲 {i + 1}"
            k = p["hotkey"]
            if not k:
                continue
            try:
                parse_hotkey(k)
            except ValueError as e:
                self.err.setText(f"{p['name']}: {e}")
                return
            if k in self.reserved or k in seen:
                self.err.setText(f"{p['name']}: {k} はほかのショートカットと重複しています")
                return
            seen.add(k)
        self.accept()

    def result_presets(self) -> list[dict]:
        return self.rows


class RegionFlash(QWidget):
    """指定した範囲に枠を 1 秒ほど表示する (クリックは素通り)。"""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)

    def flash(self, region: list[int], monitors: list[Monitor], ms: int = 1200) -> None:
        rect = phys_to_logical(region, monitors)
        if rect is None:
            return
        self.setGeometry(rect.adjusted(-3, -3, 3, 3))
        self.show()
        self.raise_()
        self._timer.start(ms)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 80, 80), 3))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))


def screen_for_monitor(mon: Monitor, monitors: list[Monitor]):
    """DXGI のモニターに対応する QScreen を探す。

    Qt の画面名はモデル名 (例: 'ZOWIE XL LCD') で DXGI の '\\\\.\\DISPLAY1' と一致しないので、
    位置で照合する。Qt は高 DPI でも各画面の左上は物理座標のまま保つ。
    """
    screens = QGuiApplication.screens()
    for s in screens:
        g = s.geometry()
        if (g.x(), g.y()) == (mon.left, mon.top):
            return s
    for s in screens:
        if s.name().lower() == mon.name.lower():
            return s
    # 最後の手段: 並び順 (左上から) で対応付け
    order_m = sorted(monitors, key=lambda m: (m.left, m.top))
    order_s = sorted(screens, key=lambda s: (s.geometry().x(), s.geometry().y()))
    if mon in order_m and len(order_m) == len(order_s):
        return order_s[order_m.index(mon)]
    return None


def phys_to_logical(region: list[int], monitors: list[Monitor]) -> QRect | None:
    cx, cy = (region[0] + region[2]) // 2, (region[1] + region[3]) // 2
    mon = next((m for m in monitors if m.left <= cx < m.right and m.top <= cy < m.bottom), None)
    if mon is None:
        return None
    scr = screen_for_monitor(mon, monitors)
    if scr is None:
        return None
    g = scr.geometry()
    sx, sy = g.width() / mon.width, g.height() / mon.height
    return QRect(g.x() + int((region[0] - mon.left) * sx), g.y() + int((region[1] - mon.top) * sy),
                 int((region[2] - region[0]) * sx), int((region[3] - region[1]) * sy))
