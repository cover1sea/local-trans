"""キャプチャ済みの静止画を全モニターに表示し、ドラッグで翻訳範囲を選ばせる。"""
from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .capture import Monitor
from .winutil import force_foreground, foreground

log = logging.getLogger(__name__)


def bgra_to_qimage(bgra: np.ndarray) -> QImage:
    h, w = bgra.shape[:2]
    buf = np.ascontiguousarray(bgra)
    return QImage(buf.data, w, h, buf.strides[0], QImage.Format.Format_ARGB32).copy()


class _ScreenOverlay(QWidget):
    def __init__(self, owner: "RegionSelector", screen, mon: Monitor, frame: np.ndarray) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.owner, self.mon = owner, mon
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.pix = QPixmap.fromImage(bgra_to_qimage(frame))
        self.start: QPoint | None = None
        self.end: QPoint | None = None

    def _sel(self) -> QRect | None:
        if self.start is None or self.end is None:
            return None
        return QRect(self.start, self.end).normalized()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        sel = self._sel()
        if sel and sel.width() > 1 and sel.height() > 1:
            sx = self.pix.width() / max(1, self.width())
            sy = self.pix.height() / max(1, self.height())
            src = QRect(int(sel.x() * sx), int(sel.y() * sy), int(sel.width() * sx), int(sel.height() * sy))
            p.drawPixmap(sel, self.pix, src)
            p.setPen(QPen(QColor(80, 170, 255), 2))
            p.drawRect(sel)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Yu Gothic UI", 12))
        p.drawText(QRect(0, 16, self.width(), 30), Qt.AlignmentFlag.AlignHCenter,
                   "ドラッグで翻訳範囲を選択　/　Esc・右クリックでキャンセル")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.owner.finish(None)
            return
        self.start = self.end = e.position().toPoint()
        self.owner.clear_others(self)
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self.start is not None:
            self.end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        self.end = e.position().toPoint()
        sel = self._sel()
        if sel is None or sel.width() < 5 or sel.height() < 5:
            self.start = self.end = None
            self.update()
            return
        # 論理座標 → 物理ピクセル (仮想デスクトップ座標)
        sx = self.mon.width / max(1, self.width())
        sy = self.mon.height / max(1, self.height())
        region = [self.mon.left + int(sel.left() * sx), self.mon.top + int(sel.top() * sy),
                  self.mon.left + int((sel.right() + 1) * sx), self.mon.top + int((sel.bottom() + 1) * sy)]
        self.owner.finish(region)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.owner.finish(None)


class RegionSelector(QObject):
    selected = Signal(list, object)  # region [l, t, r, b], 選択範囲の BGRA 画像
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.overlays: list[_ScreenOverlay] = []
        self.frames: dict[str, tuple[Monitor, np.ndarray]] = {}

    def start(self, captures: list[tuple[Monitor, np.ndarray]]) -> None:
        self.close_all()
        from .presets import screen_for_monitor

        monitors = [m for m, _f in captures]
        for mon, frame in captures:
            scr = screen_for_monitor(mon, monitors)
            if scr is None:
                log.warning("モニター %s に対応する画面が見つかりません", mon.name)
                continue
            ov = _ScreenOverlay(self, scr, mon, frame)
            self.overlays.append(ov)
            ov.show()
        self.frames = {m.name: (m, f) for m, f in captures}
        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        target = next((o for o in self.overlays if o.screen() is cursor_screen), None) \
            or (self.overlays[0] if self.overlays else None)
        if target:
            target.raise_()
            target.activateWindow()
            self._activate(target)
            # ウィンドウの表示が終わってからもう一度 (ゲームが前面を取り返すことがある)
            QTimer.singleShot(120, lambda: self._activate(target))

    def _activate(self, ov: "_ScreenOverlay") -> None:
        if ov not in self.overlays:
            return
        hwnd = int(ov.winId())
        if foreground() != hwnd:
            ok = force_foreground(hwnd)
            log.info("選択画面の前面化: %s", "成功" if ok else "失敗")
        ov.grabKeyboard()

    def clear_others(self, keep: _ScreenOverlay) -> None:
        for o in self.overlays:
            if o is not keep and o.start is not None:
                o.start = o.end = None
                o.update()

    def finish(self, region: list[int] | None) -> None:
        crop = None
        if region:
            for mon, frame in self.frames.values():
                cx, cy = (region[0] + region[2]) // 2, (region[1] + region[3]) // 2
                if mon.left <= cx < mon.right and mon.top <= cy < mon.bottom:
                    crop = np.ascontiguousarray(frame[max(0, region[1] - mon.top):region[3] - mon.top,
                                                      max(0, region[0] - mon.left):region[2] - mon.left])
                    break
        self.close_all()
        if region and crop is not None and crop.size:
            self.selected.emit(region, crop)
        else:
            self.cancelled.emit()

    def close_all(self) -> None:
        for o in self.overlays:
            o.close()
        self.overlays = []
        self.frames = {}
