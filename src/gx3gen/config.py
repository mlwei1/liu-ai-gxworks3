"""本地配置（workspace/config.json）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]


def workspace_root() -> Path:
    env = os.environ.get("GX3GEN_WORKSPACE")
    root = Path(env) if env else (REPO_ROOT / "workspace")
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return workspace_root() / "config.json"


DEFAULT_CONFIG: Dict[str, Any] = {
    # llm 仅为可选的独立 Web/CI 适配器配置；agent 模式不使用、不需要 API Key。
    "llm": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout": 120,
        "temperature": 0.2,
    },
    "ui": {"host": "127.0.0.1", "port": 8765},
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(base))
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> Dict[str, Any]:
    path = config_path()
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(cfg: Dict[str, Any]) -> None:
    cfg = _deep_merge(DEFAULT_CONFIG, cfg or {})
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
