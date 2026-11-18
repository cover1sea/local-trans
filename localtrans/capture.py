"""画面キャプチャ。

DXGI Desktop Duplication (dxcam) を優先して使う。GDI (BitBlt) と違い、
DirectX で描画されたフルスクリーンゲームも黒画面にならずに取れる。
失敗したときは mss (GDI) にフォールバックする。
座標はすべて物理ピクセル (仮想デスクトップ座標)。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Monitor:
    name: str  # \\.\DISPLAY1 など。Qt の QScreen.name() と同じ
    left: int
    top: int
    right: int
    bottom: int
    device_idx: int = -1
    output_idx: int = -1

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class ScreenCapturer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cams: dict[tuple[int, int], object] = {}
        self._dxcam = None
        try:
            import dxcam

            self._dxcam = dxcam
        except Exception:  # noqa: BLE001
            log.exception("dxcam を読み込めませんでした。GDI キャプチャのみ使います")

    # ---- モニター列挙 ----
    def monitors(self) -> list[Monitor]:
        mons: list[Monitor] = []
        if self._dxcam is not None:
            try:
                factory = getattr(self._dxcam, "__factory")
                for d, outs in enumerate(factory.outputs):
                    for o, out in enumerate(outs):
                        out.update_desc()
                        r = out.desc.DesktopCoordinates
                        mons.append(Monitor(out.devicename, r.left, r.top, r.right, r.bottom, d, o))
            except Exception:  # noqa: BLE001
                log.exception("DXGI の出力一覧を取得できませんでした")
                mons = []
        if not mons:
            import mss

            with mss.MSS() as s:
                for i, m in enumerate(s.monitors[1:]):
                    mons.append(Monitor(m.get("name", f"monitor{i}"), m["left"], m["top"],
                                        m["left"] + m["width"], m["top"] + m["height"]))
        return mons

    # ---- キャプチャ ----
    def grab_monitor(self, mon: Monitor) -> np.ndarray:
        """モニター全体を BGRA (H, W, 4) で返す。"""
        with self._lock:
            if mon.device_idx >= 0:
                frame = self._grab_dxgi(mon)
                if frame is not None:
                    return frame
            return self._grab_gdi(mon.left, mon.top, mon.width, mon.height)

    def grab_rect(self, left: int, top: int, right: int, bottom: int) -> np.ndarray:
        """仮想デスクトップ座標の矩形を BGRA で返す。矩形を含むモニターから切り出す。"""
        cx, cy = (left + right) // 2, (top + bottom) // 2
        mons = self.monitors()
        mon = next((m for m in mons if m.left <= cx < m.right and m.top <= cy < m.bottom), None)
        if mon is None:
            return self._grab_gdi(left, top, right - left, bottom - top)
        full = self.grab_monitor(mon)
        x0, y0 = max(0, left - mon.left), max(0, top - mon.top)
        x1, y1 = min(mon.width, right - mon.left), min(mon.height, bottom - mon.top)
        return np.ascontiguousarray(full[y0:y1, x0:x1])

    def _grab_dxgi(self, mon: Monitor) -> np.ndarray | None:
        key = (mon.device_idx, mon.output_idx)
        for attempt in range(2):
            try:
                cam = self._cams.get(key)
                if cam is None:
                    cam = self._dxcam.create(device_idx=mon.device_idx, output_idx=mon.output_idx,
                                             output_color="BGRA", processor_backend="numpy")
                    self._cams[key] = cam
                frame = None
                # 画面が静止していると新フレームが来ないので、最後のフレームも許可する
                for _ in range(10):
                    frame = cam.grab(new_frame_only=False)
                    if frame is not None:
                        break
                    time.sleep(0.02)
                if frame is not None:
                    return np.array(frame, copy=True)
            except Exception:  # noqa: BLE001
                # 解像度変更や排他フルスクリーンへの切り替えで複製が無効になることがある。作り直す
                log.warning("DXGI キャプチャ失敗 (試行 %d)", attempt + 1, exc_info=True)
                cam = self._cams.pop(key, None)
                if cam is not None:
                    try:
                        cam.release()
                    except Exception:  # noqa: BLE001
                        pass
        return None

    @staticmethod
    def _grab_gdi(left: int, top: int, width: int, height: int) -> np.ndarray:
        import mss

        with mss.MSS() as s:
            shot = s.grab({"left": left, "top": top, "width": width, "height": height})
            return np.array(shot, dtype=np.uint8)  # BGRA
