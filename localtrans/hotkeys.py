"""Win32 RegisterHotKey によるグローバルホットキー。

専用スレッドでメッセージループを回し、押されたらコールバックを呼ぶ。
フルスクリーンのゲームが前面にあっても届く (管理者権限で動くゲームの場合は、
このアプリも管理者として起動する必要がある)。
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "meta": MOD_WIN}
_SPECIAL = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "ins": 0x2D, "insert": 0x2D, "del": 0x2E, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "print": 0x2C, "pause": 0x13,
    "scrolllock": 0x91,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """'Ctrl+Alt+Q' → (modifiers, vk)。"""
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if text.endswith("++"):
        parts.append("+")
    mods, vk = 0, None
    for p in parts:
        low = p.lower()
        if low in _MODS:
            mods |= _MODS[low]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            vk = 0x70 + int(low[1:]) - 1
        elif low.startswith("num") and low[3:].isdigit():
            vk = 0x60 + int(low[3:])
        elif low in _SPECIAL:
            vk = _SPECIAL[low]
        else:
            raise ValueError(f"このキーは使えません: {p}")
    if vk is None:
        raise ValueError(f"キーが指定されていません: {text}")
    return mods, vk


user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
_MOD_VKS = {MOD_CONTROL: (0x11,), MOD_ALT: (0x12,), MOD_SHIFT: (0x10,), MOD_WIN: (0x5B, 0x5C)}


def _down(vk: int) -> bool:
    # 0x8000: いま押されている / 0x0001: 前回の問い合わせ以降に押された (素早い押下も拾う)
    return bool(user32.GetAsyncKeyState(vk) & 0x8001)


class HotkeyManager:
    """2 通りの方法でショートカットを拾う。

    1. RegisterHotKey: 通常のアプリ向け。キーはゲームに渡らない。
    2. キー状態のポーリング (GetAsyncKeyState): DirectInput / Raw Input で入力を
       独占するフルスクリーンゲームでは 1 が届かないことがあるので、その保険。
    同じ押下で両方が反応しても 1 回だけ実行する。
    """

    DEBOUNCE = 0.4
    POLL_INTERVAL = 0.015

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._thread_id = 0
        self._ready = threading.Event()
        self._fire_lock = threading.Lock()
        self._last_fire: dict[int, float] = {}
        self.errors: list[str] = []

    def start(self, bindings: dict[str, Callable[[], None]]) -> list[str]:
        """bindings: {'Ctrl+Alt+Q': callback}。登録に失敗したキーの説明を返す。"""
        self.stop()
        self.errors = []
        parsed: dict[int, tuple[str, int, int, Callable[[], None]]] = {}
        for i, (key, cb) in enumerate(bindings.items(), start=1):
            if not key:
                continue
            try:
                mods, vk = parse_hotkey(key)
            except ValueError as e:
                self.errors.append(str(e))
                continue
            parsed[i] = (key, mods, vk, cb)
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, args=(parsed,), daemon=True,
                                        name="hotkeys")
        self._thread.start()
        self._ready.wait(2)
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll, args=(parsed,), daemon=True,
                                             name="hotkey-poll")
        self._poll_thread.start()
        return self.errors

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(2)
        self._poll_thread = None

    def _fire(self, i: int, key: str, cb: Callable[[], None], via: str) -> None:
        now = time.monotonic()
        with self._fire_lock:
            if now - self._last_fire.get(i, 0.0) < self.DEBOUNCE:
                return
            self._last_fire[i] = now
        log.info("ショートカット %s (%s)", key, via)
        try:
            cb()
        except Exception:  # noqa: BLE001
            log.exception("ホットキー処理でエラー")

    def _run(self, parsed: dict[int, tuple[str, int, int, Callable[[], None]]]) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered: set[int] = set()
        for i, (key, mods, vk, _cb) in parsed.items():
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                registered.add(i)
            else:
                log.warning("%s を RegisterHotKey で登録できませんでした (ポーリングのみで検出)", key)
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in parsed:
                    key, _m, _v, cb = parsed[msg.wParam]
                    self._fire(msg.wParam, key, cb, "hotkey")
        finally:
            for i in registered:
                user32.UnregisterHotKey(None, i)

    def _poll(self, parsed: dict[int, tuple[str, int, int, Callable[[], None]]]) -> None:
        was_down = {i: False for i in parsed}
        all_vks = {vk for _k, _m, vk, _c in parsed.values()} | {v for vks in _MOD_VKS.values() for v in vks}
        while not self._poll_stop.wait(self.POLL_INTERVAL):
            # 「押された」ビットは問い合わせのたびに消えるので、1 周につき 1 回だけ読む
            state = {v: _down(v) for v in all_vks}
            for i, (key, mods, vk, cb) in parsed.items():
                down = state[vk] and all(
                    any(state[m] for m in vks) for flag, vks in _MOD_VKS.items() if mods & flag)
                if down and not was_down[i]:
                    self._fire(i, key, cb, "poll")
                was_down[i] = down
