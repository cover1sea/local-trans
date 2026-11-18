"""PLaMo 3 Translate 0.8B による翻訳。"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

MODEL_ID = "pfnet/plamo-3-translate-0.8b"
STOP_MARKERS = ("<|plamo:tag|>", "<|plamo:bos|>")


class Translator:
    def __init__(self, device: str = "cuda:0") -> None:
        self.device = device
        self._model = None
        self._tok = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> str:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = self.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            log.warning("CUDA が使えないので CPU で動かします")
            device = "cpu"
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        # ダウンロード済みならネットワークに触れずに読み込む
        for local_only in (True, False):
            try:
                self._tok = AutoTokenizer.from_pretrained(
                    MODEL_ID, trust_remote_code=True, local_files_only=local_only)
                self._model = AutoModelForCausalLM.from_pretrained(
                    MODEL_ID, trust_remote_code=True, dtype=dtype, device_map=device,
                    local_files_only=local_only)
                break
            except OSError:
                if not local_only:
                    raise
                log.info("モデルがキャッシュに無いのでダウンロードします")
        self._model.eval()
        self.device = device
        if device.startswith("cuda"):
            idx = int(device.split(":")[1]) if ":" in device else 0
            return torch.cuda.get_device_name(idx)
        return "CPU"

    def translate(self, text: str, *, target_lang: str = "ja", source_lang: str | None = "en",
                  form: str | None = None, context: str | None = None,
                  glossary: list[dict] | None = None, max_new_tokens: int = 1024) -> str:
        import torch

        if not text.strip():
            return ""
        kwargs: dict = {"target_lang": target_lang}
        if source_lang:
            kwargs["source_lang"] = source_lang
        if form and target_lang == "ja":
            kwargs["form"] = form
        if context:
            kwargs["context"] = context
        if glossary:
            kwargs["glossary"] = glossary
        with self._lock, torch.inference_mode():
            inputs = self._tok.apply_chat_template(
                [{"role": "user", "content": text}], add_generation_prompt=True,
                return_dict=True, return_tensors="pt", **kwargs).to(self._model.device)
            out = self._model.generate(**inputs, do_sample=False, max_new_tokens=max_new_tokens,
                                       eos_token_id=self._stop_ids())
            gen = out[0, inputs["input_ids"].shape[1]:].tolist()
        stops = set(self._stop_ids())
        for i, t in enumerate(gen):
            if t in stops:
                gen = gen[:i]
                break
        return self._tok.decode(gen, skip_special_tokens=True).strip()

    def _stop_ids(self) -> list[int]:
        ids = {self._tok.convert_tokens_to_ids(m) for m in STOP_MARKERS}
        if self._tok.eos_token_id is not None:
            ids.add(self._tok.eos_token_id)
        unk = self._tok.unk_token_id
        return [i for i in ids if i is not None and i != unk]
