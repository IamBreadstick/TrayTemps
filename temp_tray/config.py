from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

APP_NAME = "TrayTemps"
CONFIG_DIR = Path(os.getenv("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    refresh_interval_seconds: int = 2
    unit: str = "C"  # C or F
    start_with_windows: bool = False
    appearance: str = "system"  # system, light, or dark
    pawnio_prompt: str = "ask"  # ask or never

    def normalize(self) -> "AppConfig":
        if self.refresh_interval_seconds not in (1, 2, 5):
            self.refresh_interval_seconds = 2
        if self.unit not in ("C", "F"):
            self.unit = "C"
        if self.appearance not in ("system", "light", "dark"):
            self.appearance = "system"
        if self.pawnio_prompt not in ("ask", "never"):
            self.pawnio_prompt = "ask"
        return self


def load_config() -> AppConfig:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return AppConfig(**data).normalize()
    except Exception:
        pass
    return AppConfig()


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config.normalize()), indent=2), encoding="utf-8")
