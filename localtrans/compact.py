"""最小表示: 訳文だけを半透明の帯で出す、ゲームに重ねる用の小さなウィンドウ。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QTextOption
from PySide6.QtWidgets import QMenu, QWidget

PAD = 12
EDGE = 8          # 右端のこの幅をドラッグすると横幅を変える
MIN_W = 240


class CompactView(QWidget):
    """フォーカスを取らない枠なしウィンドウ。ドラッグで移動、右端のドラッグで幅を変える。

    高さは訳文の量に合わせて自動で変わる (上端は動かない)。
    """

    exit_requested = Signal()
    select_requested = Signal()
    repeat_requested = Signal()
    auto_toggled = Signal(bool)
    settings_requested = Signal()
    moved = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.text = ""
        self.hint = ""          # 「翻訳中…」などの小さな表示
        self.opacity = 0.7      # 背景の不透明度
        self.font_ = QFont("Yu Gothic UI", 15)
        self.auto_on = False
        self.locked = False     # クリックを透過する
        self._drag: QPoint | None = None
        self._resize_from: tuple[int, int] | None = None
        self.resize(640, 80)

    # ---------- 外から呼ぶ ----------
    def set_style(self, font_size: int, opacity: float) -> None:
        self.font_ = QFont("Yu Gothic UI", font_size)
        self.opacity = max(0.0, min(1.0, opacity))
        self._fit()

    def set_text(self, text: str) -> None:
        self.text = text.strip()
        self.hint = ""
        self._fit()

    def set_hint(self, hint: str) -> None:
        self.hint = hint
        self._fit()

    def set_locked(self, on: bool) -> None:
        """クリック透過。透過中はマウスで動かせないので、最小表示のショートカットで解除する。"""
        self.locked = on
        visible = self.isVisible()  # フラグを変えると Qt はいったんウィンドウを隠す
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        if visible:
            self.show()
        self.update()

    # ---------- 描画と大きさ ----------
    def _lines_rect(self, width: int) -> QRect:
        fm = QFontMetrics(self.font_)
        body = self.text or "（ここに訳文が出ます）"
        r = fm.boundingRect(QRect(0, 0, width - 2 * PAD, 10000),
                            int(Qt.TextFlag.TextWordWrap), body)
        return r

    def _hint_height(self) -> int:
        return QFontMetrics(self._hint_font()).height() + 2 if self.hint else 0

    def _hint_font(self) -> QFont:
        f = QFont(self.font_)
        f.setPointSizeF(max(8.0, self.font_.pointSizeF() * 0.6))
        return f

    def _fit(self) -> None:
        h = self._lines_rect(self.width()).height() + self._hint_height() + 2 * PAD
        if h != self.height():
            self.resize(self.width(), h)
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        p.fillPath(path, QColor(16, 16, 20, int(255 * self.opacity)))
        y = PAD
        if self.hint:
            p.setFont(self._hint_font())
            p.setPen(QColor(160, 200, 255))
            p.drawText(QRect(PAD, y, self.width() - 2 * PAD, self._hint_height()),
                       int(Qt.AlignmentFlag.AlignLeft), self.hint)
            y += self._hint_height()
        p.setFont(self.font_)
        p.setPen(QColor(245, 245, 245) if self.text else QColor(150, 150, 150))
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        rect = QRect(PAD, y, self.width() - 2 * PAD, self.height() - y - PAD)
        # 字の周りに薄い影を付けて、明るい背景の上でも読めるようにする
        body = self.text or "（ここに訳文が出ます）"
        p.save()
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(rect.translated(1, 1), body, opt)
        p.restore()
        p.drawText(rect, body, opt)

    # ---------- マウス ----------
    def _on_edge(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - EDGE

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        if self._on_edge(pos):
            self._resize_from = (int(e.globalPosition().x()), self.width())
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        if self._resize_from:
            x0, w0 = self._resize_from
            self.resize(max(MIN_W, w0 + int(e.globalPosition().x()) - x0), self.height())
            self._fit()
        elif self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._on_edge(pos)
                           else Qt.CursorShape.SizeAllCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._drag is not None or self._resize_from:
            self.moved.emit()
        self._drag = None
        self._resize_from = None

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.exit_requested.emit()

    def contextMenuEvent(self, e):  # noqa: N802
        m = QMenu(self)
        m.addAction("範囲を選んで翻訳", self.select_requested.emit)
        m.addAction("選んでいる範囲で翻訳", self.repeat_requested.emit)
        a = m.addAction("自動監視")
        a.setCheckable(True)
        a.setChecked(self.auto_on)
        a.toggled.connect(self.auto_toggled.emit)
        m.addSeparator()
        m.addAction("クリックを透過する（最小表示のショートカットで解除）", lambda: self.set_locked(True))
        m.addAction("設定", self.settings_requested.emit)
        m.addAction("通常の表示に戻す", self.exit_requested.emit)
        m.addAction("終了", self.quit_requested.emit)
        m.exec(e.globalPos())
