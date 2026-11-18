"""デスクトップとスタートメニューに Local Trans のショートカットを作る。

ショートカットにはアプリのアイコンと AppUserModelID (LocalTrans.App) を設定する。
ID をそろえておくと、起動中のウィンドウをタスクバーにピン留めしたときも
Python ではなくこのアプリとしてピン留めされる。

    uv run python tools/make_shortcuts.py            # 作る
    uv run python tools/make_shortcuts.py --remove   # 消す
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, Structure, c_ulong, c_ushort, c_void_p, c_wchar_p, cast
from pathlib import Path

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.persist import IPersistFile
from comtypes.shelllink import IShellLinkW, ShellLink

APP_ID = "LocalTrans.App"   # main.py の SetCurrentProcessExplicitAppUserModelID と同じ値
NAME = "Local Trans"
ROOT = Path(__file__).resolve().parent.parent


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("val", c_void_p), ("pad", c_void_p)]


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "count")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], c_ulong, "i"), (["in"], POINTER(PROPERTYKEY), "key")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "Commit"),
    ]


PKEY_APP_USER_MODEL_ID = PROPERTYKEY(GUID("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)
VT_LPWSTR = 31
CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY = 0x02, 0x10


def _folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def _targets() -> list[Path]:
    return [_folder(CSIDL_DESKTOPDIRECTORY) / f"{NAME}.lnk", _folder(CSIDL_PROGRAMS) / f"{NAME}.lnk"]


def make(path: Path) -> None:
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        sys.exit(f"{pythonw} がありません。先に LocalTrans.bat を一度実行するか、uv sync を実行してください。")
    link = comtypes.client.CreateObject(ShellLink, interface=IShellLinkW)
    link.SetPath(str(pythonw))
    link.SetArguments(f'"{ROOT / "main.py"}"')
    link.SetWorkingDirectory(str(ROOT))
    link.SetIconLocation(str(ROOT / "assets" / "icon.ico"), 0)
    link.SetDescription("画面の英文を日本語に翻訳します (PLaMo 3 Translate)")

    store = link.QueryInterface(IPropertyStore)
    value = ctypes.create_unicode_buffer(APP_ID)
    pv = PROPVARIANT(vt=VT_LPWSTR, val=cast(value, c_void_p))
    store.SetValue(PKEY_APP_USER_MODEL_ID, pv)
    store.Commit()

    path.parent.mkdir(parents=True, exist_ok=True)
    link.QueryInterface(IPersistFile).Save(str(path), True)
    print(f"作成しました: {path}")


def main() -> None:
    comtypes.CoInitialize()
    for p in _targets():
        if "--remove" in sys.argv:
            if p.exists():
                p.unlink()
                print(f"削除しました: {p}")
        else:
            make(p)


if __name__ == "__main__":
    main()
