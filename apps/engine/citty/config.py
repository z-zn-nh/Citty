"""配置加载：config.yaml + .env。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:  # 可选依赖
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[3]  # D:\Citty
DEFAULT_CONFIG = ROOT / "config.yaml"


class Config(dict):
    """点号访问的 dict：cfg.get_path('mt.target_lang')。"""

    def get_path(self, path: str, default: Any = None) -> Any:
        cur: Any = self
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def load_config(path: str | Path | None = None, env_file: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    env_path = Path(env_file) if env_file else ROOT / ".env"
    if load_dotenv is not None and env_path.exists():
        load_dotenv(env_path, override=False)
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = Config(data)
    out_dir = ROOT / str(cfg.get_path("logging.srt_dir", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg["_root"] = str(ROOT)
    cfg["_out_dir"] = str(out_dir)
    return cfg


def env(name: str | None) -> str | None:
    if not name:
        return None
    val = os.environ.get(name)
    return val.strip() if val else None
