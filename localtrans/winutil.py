"""Win32 のウィンドウ操作の補助。"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TokenElevation = 20
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x2
SW_RESTORE, SW_SHOW = 9, 5


def foreground() -> int:
    return user32.GetForegroundWindow() or 0


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_process_elevated(pid: int) -> bool | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None  # 権限の高いプロセスはトークンを開けないことが多い
        try:
            val, size = wintypes.DWORD(), wintypes.DWORD()
            if advapi32.GetTokenInformation(tok, TokenElevation, ctypes.byref(val),
                                            ctypes.sizeof(val), ctypes.byref(size)):
                return bool(val.value)
            return None
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def self_elevated() -> bool:
    return bool(is_process_elevated(kernel32.GetCurrentProcessId()))


def describe_window(hwnd: int) -> str:
    """ログ用: タイトル、実行ファイル、矩形、管理者権限かどうか。"""
    if not hwnd:
        return "(なし)"
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    pid = _pid(hwnd)
    exe = "?"
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        n = wintypes.DWORD(1024)
        b = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
            exe = b.value.rsplit("\\", 1)[-1]
        kernel32.CloseHandle(h)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (f"'{buf.value}' {exe} pid={pid} rect=({r.left},{r.top},{r.right},{r.bottom}) "
            f"elevated={is_process_elevated(pid)}")


def force_foreground(hwnd: int) -> bool:
    """フルスクリーンゲームが前面にあっても、指定ウィンドウを前面に出してフォーカスを渡す。

    Windows は背景のプロセスが勝手に前面を奪うのを禁じているので、
    前面ウィンドウのスレッドに入力を一時的に結び付けてから切り替える。
    それでもだめなら Alt キーの空打ちで制限を解除する (よく知られた回避策)。
    """
    if not hwnd:
        return False
    fg = foreground()
    if fg == hwnd:
        return True
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    if foreground() == hwnd:
        return True
    # Alt を押して離すと、このプロセスが「最後に入力を受けた」扱いになり前面化が許可される
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    for _ in range(10):
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if foreground() == hwnd:
            return True
        time.sleep(0.03)
    return False


def restore_foreground(hwnd: int) -> None:
    if not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    force_foreground(hwnd)
