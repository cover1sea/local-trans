from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class Config:
    hotkey_select: str = "Ctrl+Alt+Q"   # 範囲を選んで翻訳
    hotkey_repeat: str = "Ctrl+Alt+W"   # 前回の範囲で翻訳
    hotkey_auto: str = "Ctrl+Alt+E"     # 前回の範囲を自動監視 ON/OFF
    hotkey_compact: str = "Ctrl+Alt+D"  # 最小表示 ON/OFF
    always_on_top: bool = True
    device: str = "cuda:0"
    form: str = "plain"                 # plain (だ・である) / polite (です・ます)
    use_context: bool = True            # 直前の原文を文脈として渡す
    context_size: int = 3
    font_size: int = 13
    auto_interval_ms: int = 1000
    last_region: list[int] | None = None  # [left, top, right, bottom] 物理ピクセル
    window_geometry: str | None = None
    glossary: list[dict] = field(default_factory=list)  # [{"source": "...", "target": "..."}]
    # 範囲プリセット: [{"name": "会話ウィンドウ", "region": [l, t, r, b], "hotkey": "Ctrl+Alt+1"}]
    presets: list[dict] = field(default_factory=list)
    # 最小表示 (訳文だけの半透明の帯)
    compact_mode: bool = False
    compact_geometry: list[int] | None = None  # [x, y, 幅] 論理座標
    compact_font_size: int = 15
    compact_opacity: int = 70                  # 背景の濃さ (%)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**known)
            except Exception:  # noqa: BLE001
                log.exception("config.json を読めませんでした。既定値で起動します")
        return cls()

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                               encoding="utf-8")
