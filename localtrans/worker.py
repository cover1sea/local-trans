"""OCR と翻訳をバックグラウンドで実行するワーカー。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Signal

from .capture import ScreenCapturer
from .config import Config
from .ocr import WindowsOcr
from .translator import Translator
from .winutil import trim_working_set

log = logging.getLogger(__name__)


class Worker(QObject):
    status = Signal(str)
    model_ready = Signal(str)
    error = Signal(str)
    source_ready = Signal(str)            # OCR で読んだ原文
    translation_progress = Signal(str)    # 段落ごとの途中経過 (それまでの訳文全体)
    translation_done = Signal(str, str)   # 原文, 訳文
    busy_changed = Signal(bool)

    def __init__(self, cfg: Config, capturer: ScreenCapturer) -> None:
        super().__init__()
        self.cfg = cfg
        self.capturer = capturer
        self.translator = Translator(cfg.device)
        self._ocr: WindowsOcr | None = None
        self._q: queue.Queue = queue.Queue()
        self._history: deque[str] = deque(maxlen=10)
        self._auto_last = ""      # 自動監視: 最後に翻訳した原文
        self._auto_pending = ""   # 自動監視: 前回読んだ原文 (2 回続けて同じなら確定)
        self.busy = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="worker")
        self._thread.start()

    # ---- 呼び出し側 (UI スレッド) ----
    def load_model(self) -> None:
        self._q.put(("load",))

    def translate_image(self, bgra: np.ndarray) -> None:
        self._q.put(("image", bgra))

    def capture_and_translate(self, region: list[int]) -> None:
        self._q.put(("region", region))

    def auto_poll(self, region: list[int]) -> None:
        if not self.busy and self._q.empty():
            self._q.put(("auto", region))

    def translate_text(self, text: str) -> None:
        self._q.put(("text", text))

    def reset_auto(self) -> None:
        self._auto_last = self._auto_pending = ""

    def clear_history(self) -> None:
        self._history.clear()

    # ---- ワーカースレッド ----
    def _run(self) -> None:
        while True:
            job = self._q.get()
            kind = job[0]
            quiet = kind == "auto"
            if not quiet:
                self._set_busy(True)
            try:
                if kind == "load":
                    self._load()
                elif kind == "image":
                    self._from_image(job[1])
                elif kind == "region":
                    self._from_image(self.capturer.grab_rect(*job[1]))
                elif kind == "auto":
                    self._auto(job[1])
                elif kind == "text":
                    self._translate(job[1])
            except Exception as e:  # noqa: BLE001
                log.exception("ジョブ %s でエラー", kind)
                self.error.emit(f"{type(e).__name__}: {e}")
            finally:
                if not quiet:
                    self._set_busy(False)

    def _set_busy(self, b: bool) -> None:
        self.busy = b
        self.busy_changed.emit(b)

    def _load(self) -> None:
        self.status.emit("翻訳モデルを読み込み中… (初回はダウンロードに数分かかります)")
        t0 = time.time()
        dev = self.translator.load()
        # 1 回訳しておくと CUDA のカーネルが読み込まれ、最初の翻訳が速くなる。
        # そのあと作業セットを手放すと、読み込みで触れただけのページ (約 1.5GB) が RAM から出ていく。
        try:
            self.translator.translate("Hello.", max_new_tokens=8)
            before, after = trim_working_set()
            log.info("作業セットを解放: %d MB → %d MB", before, after)
        except Exception:  # noqa: BLE001
            log.exception("準備の翻訳か作業セットの解放に失敗")
        self.model_ready.emit(f"{dev} ・ 読み込み {time.time() - t0:.1f} 秒")

    def _get_ocr(self) -> WindowsOcr:
        if self._ocr is None:
            self._ocr = WindowsOcr("en-US")
        return self._ocr

    def _read(self, bgra: np.ndarray) -> str:
        return "\n\n".join(self._get_ocr().read_paragraphs(bgra))

    def _from_image(self, bgra: np.ndarray) -> None:
        self.status.emit("文字を読み取り中…")
        t0 = time.time()
        text = self._read(bgra)
        ocr_t = time.time() - t0
        self.source_ready.emit(text)
        if not text:
            self.status.emit("英文が見つかりませんでした")
            self.translation_done.emit("", "")
            return
        self._auto_last = self._auto_pending = text
        self._translate(text, ocr_t)

    def _auto(self, region: list[int]) -> None:
        text = self._read(self.capturer.grab_rect(*region))
        if not text or text == self._auto_last:
            self._auto_pending = text
            return
        if text != self._auto_pending:  # 文字送り中かもしれないので、次の読み取りまで待つ
            self._auto_pending = text
            return
        self._auto_last = text
        self._set_busy(True)
        try:
            self.source_ready.emit(text)
            self._translate(text)
        finally:
            self._set_busy(False)

    def _translate(self, text: str, ocr_t: float | None = None) -> None:
        if not self.translator.loaded:
            self.status.emit("翻訳モデルの読み込みを待っています…")
            while not self.translator.loaded:
                time.sleep(0.2)
        self.status.emit("翻訳中…")
        t0 = time.time()
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        outs: list[str] = []
        for p in paras:
            # 1 段落の中の改行 (手入力時) は文としてつなげずにそのまま渡す
            ctx = "\n".join(self._history) if self.cfg.use_context and self._history else None
            out = self.translator.translate(p, form=self.cfg.form, context=ctx,
                                            glossary=[g for g in self.cfg.glossary
                                                      if g.get("source") and g.get("target")] or None)
            outs.append(out)
            self.translation_progress.emit("\n\n".join(outs))
            if self.cfg.use_context:
                self._history.append(p)
                while len(self._history) > max(0, self.cfg.context_size):
                    self._history.popleft()
        result = "\n\n".join(outs)
        msg = f"翻訳 {time.time() - t0:.2f} 秒"
        if ocr_t is not None:
            msg = f"OCR {ocr_t:.2f} 秒 ・ " + msg
        self.status.emit(msg)
        self.translation_done.emit(text, result)
