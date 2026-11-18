"""Windows 標準の OCR (Windows.Media.Ocr) で英文を読み取る。"""
from __future__ import annotations

import asyncio
import re
import statistics
from dataclasses import dataclass

import numpy as np
from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter


@dataclass
class OcrLine:
    text: str
    x: float
    y: float
    w: float
    h: float


class OcrError(RuntimeError):
    pass


class WindowsOcr:
    def __init__(self, lang: str = "en-US") -> None:
        language = Language(lang)
        if not OcrEngine.is_language_supported(language):
            raise OcrError(
                f"Windows の OCR 言語 '{lang}' が入っていません。"
                "設定 → 時刻と言語 → 言語と地域 で英語 (米国) を追加してください。"
            )
        self._engine = OcrEngine.try_create_from_language(language)
        self._max_dim = int(OcrEngine.max_image_dimension)

    def read_lines(self, bgra: np.ndarray) -> list[OcrLine]:
        img = Image.fromarray(np.ascontiguousarray(bgra[:, :, [2, 1, 0]]))
        # 小さい文字は拡大したほうが認識しやすい
        scale = 1.0
        if img.height < 300 or img.width < 600:
            scale = 2.0
        if max(img.size) * scale > self._max_dim:
            scale = self._max_dim / max(img.size)
        if scale != 1.0:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                             Image.LANCZOS)
        rgba = np.asarray(img.convert("RGBA"))[:, :, [2, 1, 0, 3]].copy()  # → BGRA
        lines = asyncio.run(self._recognize(rgba))
        return [OcrLine(l.text, l.x / scale, l.y / scale, l.w / scale, l.h / scale) for l in lines]

    async def _recognize(self, bgra: np.ndarray) -> list[OcrLine]:
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, bgra.shape[1], bgra.shape[0],
            BitmapAlphaMode.PREMULTIPLIED)
        result = await self._engine.recognize_async(bmp)
        out: list[OcrLine] = []
        for line in result.lines:
            rects = [w.bounding_rect for w in line.words]
            if not rects:
                continue
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
            out.append(OcrLine(line.text, x0, y0, x1 - x0, y1 - y0))
        return out

    def read_paragraphs(self, bgra: np.ndarray) -> list[str]:
        return group_paragraphs(self.read_lines(bgra))


def group_paragraphs(lines: list[OcrLine]) -> list[str]:
    """行間が広いところで段落を区切り、段落内の改行はつなげる。"""
    if not lines:
        return []
    lines = sorted(lines, key=lambda l: (l.y, l.x))
    med_h = statistics.median(l.h for l in lines) or 1
    paras: list[list[OcrLine]] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        gap = cur.y - (prev.y + prev.h)
        if gap > med_h * 0.9:
            paras.append([cur])
        else:
            paras[-1].append(cur)
    return [t for t in (_join([l.text for l in p]) for p in paras) if t]


def _join(texts: list[str]) -> str:
    out = ""
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif re.search(r"[A-Za-z]-$", out):  # 行末ハイフネーション
            out = out[:-1] + t
        else:
            out += " " + t
    return re.sub(r"\s+", " ", out).strip()
