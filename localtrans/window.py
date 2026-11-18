from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QSplitter, QStatusBar, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from .capture import ScreenCapturer
from .compact import CompactView
from .config import APP_DIR, Config
from .hotkeys import HotkeyManager
from .presets import PresetDialog, RegionFlash, region_text
from .selector import RegionSelector
from .settings_dialog import SettingsDialog
from .worker import Worker
from . import winutil

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
HWND_TOPMOST, HWND_NOTOPMOST = wintypes.HWND(-1), wintypes.HWND(-2)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
SW_SHOWNOACTIVATE = 4

# ライト・ダーク両方で見えるよう、色は半透明の灰色と選択色 (palette(highlight)) で作る
TOOLBAR_STYLE = """
QToolBar { spacing: 4px; padding: 2px 4px; }
QToolButton {
    border: 1px solid rgba(128, 128, 128, 0.45);
    border-radius: 5px;
    padding: 3px 10px;
    background: rgba(128, 128, 128, 0.10);
}
QToolButton:hover {
    background: rgba(128, 128, 128, 0.32);
    border-color: palette(highlight);
}
QToolButton:pressed { background: rgba(128, 128, 128, 0.50); }
QToolButton:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
}
QToolButton:checked:hover { border-color: palette(highlighted-text); }
QToolButton:disabled { color: rgba(128, 128, 128, 0.7); }
"""


class MainWindow(QMainWindow):
    # ホットキーは別スレッドから届くので、シグナルで UI スレッドへ渡す
    hk_select = Signal()
    hk_repeat = Signal()
    hk_auto = Signal()
    hk_preset = Signal(int)
    hk_compact = Signal()

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Local Trans")
        icon = APP_DIR / "assets" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(560, 420)
        if cfg.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(cfg.window_geometry.encode()))

        self.capturer = ScreenCapturer()
        self.worker = Worker(cfg, self.capturer)
        self.selector = RegionSelector()
        self.hotkeys = HotkeyManager()
        self._prev_foreground = 0
        self.model_ok = False
        self.compact = CompactView()
        self._compact_started = False

        self._build_ui()
        self._wire()
        self.apply_font()
        self.register_hotkeys()
        self.worker.load_model()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        self.act_select = QAction("範囲を選んで翻訳", self)
        self.act_repeat = QAction("この範囲で翻訳", self)
        self.act_auto = QAction("自動監視", self, checkable=True)
        self.act_top = QAction("最前面", self, checkable=True)
        self.act_top.setChecked(self.cfg.always_on_top)
        self.act_settings = QAction("設定", self)
        self.act_compact = QAction("最小表示", self)
        for a in (self.act_select, self.act_auto):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_top)
        tb.addAction(self.act_compact)
        tb.addAction(self.act_settings)

        # 2 段目: 範囲プリセット
        self.addToolBarBreak()
        tb2 = QToolBar()
        tb2.setMovable(False)
        tb2.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb2)
        tb2.addWidget(QLabel(" 範囲: "))
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(180)
        self.preset_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_box.setMinimumContentsLength(14)
        tb2.addWidget(self.preset_box)
        self.act_save_preset = QAction("保存", self)
        self.act_save_preset.setToolTip("いまの範囲に名前を付けてプリセットとして保存")
        self.act_manage_presets = QAction("管理", self)
        self.act_manage_presets.setToolTip("プリセットの名前・ショートカットの変更、並べ替え、削除")
        self.act_show_region = QAction("表示", self)
        self.act_show_region.setToolTip("いまの範囲を画面上に枠で表示")
        for a in (self.act_repeat, self.act_show_region, self.act_save_preset, self.act_manage_presets):
            tb2.addAction(a)
        # ツールバーのボタンは枠がなく、ホバーしても見た目が変わらないので、ボタンらしく見せる
        for bar in (tb, tb2):
            bar.setStyleSheet(TOOLBAR_STYLE)
            for btn in bar.findChildren(QToolButton):
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flash = RegionFlash()

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(6, 4, 6, 4)
        split = QSplitter(Qt.Orientation.Vertical)
        src_box = QWidget()
        sv = QVBoxLayout(src_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(QLabel("原文 (編集して Ctrl+Enter で再翻訳)"))
        self.src = QPlainTextEdit()
        self.src.setPlaceholderText("ショートカットキーで画面をキャプチャするか、ここに英文を入力してください")
        sv.addWidget(self.src)
        dst_box = QWidget()
        dv = QVBoxLayout(dst_box)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addWidget(QLabel("訳文"))
        self.dst = QPlainTextEdit()
        self.dst.setReadOnly(True)
        dv.addWidget(self.dst)
        split.addWidget(src_box)
        split.addWidget(dst_box)
        split.setSizes([140, 260])
        v.addWidget(split)
        self.setCentralWidget(central)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_lbl = QLabel("起動中…")
        self.model_lbl = QLabel("")
        sb.addWidget(self.status_lbl, 1)
        sb.addPermanentWidget(self.model_lbl)

    def _wire(self) -> None:
        self.act_select.triggered.connect(self.start_select)
        self.act_repeat.triggered.connect(self.repeat_capture)
        self.act_auto.toggled.connect(self.set_auto)
        self.act_top.toggled.connect(self.set_on_top)
        self.act_settings.triggered.connect(self.open_settings)
        self.hk_select.connect(self.start_select)
        self.hk_repeat.connect(self.repeat_capture)
        self.hk_auto.connect(lambda: self.act_auto.setChecked(not self.act_auto.isChecked()))
        self.hk_preset.connect(self.trigger_preset)
        self.hk_compact.connect(self.toggle_compact)
        self.act_compact.triggered.connect(lambda: self.set_compact(True))
        c = self.compact
        c.exit_requested.connect(lambda: self.set_compact(False))
        c.select_requested.connect(self.start_select)
        c.repeat_requested.connect(self.repeat_capture)
        c.auto_toggled.connect(self.act_auto.setChecked)
        c.settings_requested.connect(self.open_settings)
        c.moved.connect(self._save_compact_geometry)
        c.quit_requested.connect(self.close)
        self.act_auto.toggled.connect(lambda on: setattr(c, "auto_on", on))
        self.preset_box.activated.connect(self.on_preset_chosen)
        self.act_save_preset.triggered.connect(self.save_preset)
        self.act_manage_presets.triggered.connect(self.manage_presets)
        self.act_show_region.triggered.connect(self.show_region)
        self.refresh_presets()
        QShortcut(QKeySequence("Ctrl+Return"), self.src, activated=self.retranslate)
        QShortcut(QKeySequence("Ctrl+Enter"), self.src, activated=self.retranslate)

        self.selector.selected.connect(self.on_selected)
        self.selector.cancelled.connect(self.on_select_cancelled)

        w = self.worker
        w.status.connect(self.status_lbl.setText)
        w.error.connect(self.on_error)
        w.model_ready.connect(self.on_model_ready)
        w.source_ready.connect(self.on_source)
        w.translation_progress.connect(self.dst.setPlainText)
        w.translation_progress.connect(self.compact.set_text)
        w.translation_done.connect(self.on_done)

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_tick)

    def apply_font(self) -> None:
        f = QFont("Yu Gothic UI", self.cfg.font_size)
        self.src.setFont(f)
        self.dst.setFont(f)
        self.compact.set_style(self.cfg.compact_font_size, self.cfg.compact_opacity / 100)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self.set_on_top(self.act_top.isChecked()))
        if not self._compact_started:
            self._compact_started = True
            if self.cfg.compact_mode:
                QTimer.singleShot(0, lambda: self.set_compact(True))

    # ---------- 最前面 ----------
    def _hwnd(self) -> int:
        return int(self.winId())

    def set_on_top(self, on: bool) -> None:
        self.cfg.always_on_top = on
        user32.SetWindowPos(self._hwnd(), HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def bring_up_quietly(self) -> None:
        """フォーカスを奪わずに表示する (ゲームの操作を邪魔しない)。"""
        if self.cfg.compact_mode:
            self._raise_compact()
            return
        hwnd = self._hwnd()
        if self.isMinimized() or not self.isVisible():
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        if self.cfg.always_on_top:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    # ---------- 最小表示 ----------
    def toggle_compact(self) -> None:
        if self.cfg.compact_mode and self.compact.locked:
            # クリック透過中は、まず透過を解除する (帯をマウスで操作できるようにする)
            self.compact.set_locked(False)
            self._raise_compact()
            return
        self.set_compact(not self.cfg.compact_mode)

    def set_compact(self, on: bool) -> None:
        self.cfg.compact_mode = on
        self.cfg.save()
        c = self.compact
        if on:
            if self.cfg.compact_geometry:
                x, y, w = self.cfg.compact_geometry
                c.resize(w, c.height())
                c.move(x, y)
            else:
                g = self.screen().availableGeometry()
                c.resize(min(640, g.width() - 48), c.height())
                c.move(g.right() - c.width() - 24, g.top() + 24)
            c.set_text(self.dst.toPlainText())
            self.hide()
            self._raise_compact()
        else:
            c.set_locked(False)
            c.hide()
            self.show()
            self.bring_up_quietly()

    def _raise_compact(self) -> None:
        c = self.compact
        if not c.isVisible():
            c.show()
        user32.SetWindowPos(int(c.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _save_compact_geometry(self) -> None:
        c = self.compact
        self.cfg.compact_geometry = [c.x(), c.y(), c.width()]
        self.cfg.save()

    def _notice(self, text: str) -> None:
        """ステータスバーに出す。最小表示のときは帯の上の小さな表示にも出す。"""
        self.status_lbl.setText(text)
        if self.cfg.compact_mode:
            self.compact.set_hint(text)
            self._raise_compact()

    # ---------- ホットキー ----------
    def register_hotkeys(self) -> None:
        bindings = {
            self.cfg.hotkey_select: self.hk_select.emit,
            self.cfg.hotkey_repeat: self.hk_repeat.emit,
            self.cfg.hotkey_auto: self.hk_auto.emit,
            self.cfg.hotkey_compact: self.hk_compact.emit,
        }
        bindings.pop("", None)
        extra_errs = []
        for i, p in enumerate(self.cfg.presets):
            k = p.get("hotkey")
            if not k:
                continue
            if k in bindings:
                extra_errs.append(f"プリセット「{p.get('name')}」の {k} はほかのショートカットと重複しているため無効です")
                continue
            bindings[k] = lambda i=i: self.hk_preset.emit(i)
        errs = self.hotkeys.start(bindings) + extra_errs
        self.act_select.setToolTip(f"範囲を選んで翻訳 ({self.cfg.hotkey_select})")
        self.act_repeat.setToolTip(f"選んでいる範囲をキャプチャして翻訳 ({self.cfg.hotkey_repeat})")
        self.act_auto.setToolTip(f"前回の範囲を監視し、文字が変わったら自動で翻訳 ({self.cfg.hotkey_auto})")
        self.act_compact.setToolTip(f"訳文だけを半透明の帯で表示 ({self.cfg.hotkey_compact})")
        if errs:
            QMessageBox.warning(self, "ショートカットキー", "\n".join(errs))

    # ---------- キャプチャ ----------
    def start_select(self) -> None:
        if self.selector.overlays:
            # 選択画面がゲームの裏に隠れて操作できないときは、もう一度押せば閉じられる
            self.selector.finish(None)
            return
        self._prev_foreground = winutil.foreground()
        log.info("範囲選択開始。前面: %s", winutil.describe_window(self._prev_foreground))
        try:
            caps = [(m, self.capturer.grab_monitor(m)) for m in self.capturer.monitors()]
        except Exception as e:  # noqa: BLE001
            log.exception("キャプチャ失敗")
            self.on_error(f"キャプチャに失敗しました: {e}")
            return
        self.selector.start(caps)

    def _restore_foreground(self) -> None:
        prev, self._prev_foreground = self._prev_foreground, 0
        if prev and prev != self._hwnd():
            # 排他フルスクリーンのゲームは前面を外れると最小化されるので、戻して前面に出す
            QTimer.singleShot(50, lambda: winutil.restore_foreground(prev))

    def on_selected(self, region: list, crop) -> None:
        self.cfg.last_region = [int(x) for x in region]
        self.cfg.save()
        self.refresh_presets()
        self._restore_foreground()
        self.worker.reset_auto()
        self.worker.translate_image(crop)
        self.bring_up_quietly()

    def on_select_cancelled(self) -> None:
        self._restore_foreground()

    def repeat_capture(self) -> None:
        if not self.cfg.last_region:
            self._notice(f"先に「範囲を選んで翻訳」({self.cfg.hotkey_select}) で範囲を選んでください")
            self.bring_up_quietly()
            return
        self.worker.capture_and_translate(self.cfg.last_region)

    # ---------- 範囲プリセット ----------
    def refresh_presets(self) -> None:
        """コンボボックスを作り直す。いまの範囲がどのプリセットとも違えば先頭に「保存していない範囲」を出す。"""
        box = self.preset_box
        box.blockSignals(True)
        box.clear()
        cur = self.cfg.last_region
        match = next((i for i, p in enumerate(self.cfg.presets) if p.get("region") == cur), None)
        if cur and match is None:
            box.addItem("前回の範囲（未保存）", None)
        for i, p in enumerate(self.cfg.presets):
            label = p.get("name") or f"範囲 {i + 1}"
            if p.get("hotkey"):
                label += f"  [{p['hotkey']}]"
            box.addItem(label, i)
        if match is not None:
            box.setCurrentIndex(box.findData(match))
        if box.count() == 0:
            box.addItem("（範囲がまだありません）", None)
        box.setToolTip(f"いまの範囲: {region_text(cur)}" if cur else "")
        box.blockSignals(False)

    def _use_preset(self, i: int) -> bool:
        if not 0 <= i < len(self.cfg.presets):
            return False
        self.cfg.last_region = list(self.cfg.presets[i]["region"])
        self.cfg.save()
        self.worker.reset_auto()
        self.refresh_presets()
        return True

    def on_preset_chosen(self, index: int) -> None:
        i = self.preset_box.itemData(index)
        if i is not None and self._use_preset(i):
            self.status_lbl.setText(f"範囲「{self.cfg.presets[i].get('name')}」に切り替えました")
            self.show_region()

    def trigger_preset(self, i: int) -> None:
        """プリセットのショートカット: その範囲に切り替えてすぐ翻訳する (ゲーム中向け)。"""
        if self._use_preset(i):
            self.worker.capture_and_translate(self.cfg.last_region)

    def show_region(self) -> None:
        if self.cfg.last_region:
            self.flash.flash(self.cfg.last_region, self.capturer.monitors())

    def save_preset(self) -> None:
        if not self.cfg.last_region:
            self.status_lbl.setText("先に「範囲を選んで翻訳」で範囲を選んでください")
            return
        name, ok = QInputDialog.getText(self, "範囲を保存", "プリセットの名前:",
                                        text=f"範囲 {len(self.cfg.presets) + 1}")
        name = name.strip()
        if not ok or not name:
            return
        existing = next((p for p in self.cfg.presets if p.get("name") == name), None)
        if existing is not None:
            if QMessageBox.question(self, "範囲を保存", f"「{name}」を今の範囲で上書きしますか？") \
                    != QMessageBox.StandardButton.Yes:
                return
            existing["region"] = list(self.cfg.last_region)
        else:
            self.cfg.presets.append({"name": name, "region": list(self.cfg.last_region), "hotkey": ""})
        self.cfg.save()
        self.refresh_presets()
        self.status_lbl.setText(f"範囲「{name}」を保存しました（「管理」でショートカットを割り当てられます）")

    def manage_presets(self) -> None:
        reserved = [self.cfg.hotkey_select, self.cfg.hotkey_repeat, self.cfg.hotkey_auto,
                    self.cfg.hotkey_compact]
        dlg = PresetDialog(self.cfg.presets, reserved, self)
        if dlg.exec():
            self.cfg.presets = dlg.result_presets()
            self.cfg.save()
            self.refresh_presets()
            self.register_hotkeys()

    def set_auto(self, on: bool) -> None:
        if on and not self.cfg.last_region:
            self.act_auto.setChecked(False)
            self._notice("自動監視には、先に範囲を選んでください")
            return
        if on:
            self.worker.reset_auto()
            self.auto_timer.start(self.cfg.auto_interval_ms)
            self._notice("自動監視中: 範囲の文字が変わると翻訳します")
        else:
            self.auto_timer.stop()
            self._notice("自動監視を停止しました")

    def _auto_tick(self) -> None:
        if self.cfg.last_region and self.model_ok:
            self.worker.auto_poll(self.cfg.last_region)

    def retranslate(self) -> None:
        text = self.src.toPlainText().strip()
        if text:
            # 手入力では空行を段落区切りとして扱う
            self.worker.translate_text(text)

    # ---------- ワーカーからの通知 ----------
    def on_model_ready(self, info: str) -> None:
        self.model_ok = True
        self.model_lbl.setText(info)
        self.status_lbl.setText(
            f"準備完了 ・ {self.cfg.hotkey_select}: 範囲選択 / {self.cfg.hotkey_repeat}: 選んでいる範囲 / "
            f"{self.cfg.hotkey_auto}: 自動監視")

    def on_source(self, text: str) -> None:
        self.src.setPlainText(text)
        self.dst.setPlainText("")
        if self.cfg.compact_mode:
            self.compact.set_hint("翻訳中…")
        self.bring_up_quietly()

    def on_done(self, _src: str, dst: str) -> None:
        self.dst.setPlainText(dst)
        self.compact.set_text(dst)
        self.bring_up_quietly()

    def on_error(self, msg: str) -> None:
        self._notice("エラー: " + msg)

    # ---------- 設定 ----------
    def open_settings(self) -> None:
        old_dev = self.cfg.device
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.cfg.save()
            self.apply_font()
            self.register_hotkeys()
            if self.auto_timer.isActive():
                self.auto_timer.start(self.cfg.auto_interval_ms)
            if not self.cfg.use_context:
                self.worker.clear_history()
            if self.cfg.device != old_dev:
                self.status_lbl.setText("実行デバイスの変更はアプリの再起動後に反映されます")

    def closeEvent(self, e):  # noqa: N802
        self.cfg.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        if self.compact.isVisible():
            self._save_compact_geometry()
        self.cfg.save()
        self.hotkeys.stop()
        self.compact.close()
        QApplication.quit()
        super().closeEvent(e)
