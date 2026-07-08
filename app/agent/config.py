"""Configuration helpers for the LabSafe agent."""

import json
import os
from copy import deepcopy


CONFIG_FILE = os.environ.get("LABSAFE_CONFIG_FILE", "/home/elf/labsafe/config.json")
MESSAGES_FILE = os.environ.get("LABSAFE_MESSAGES_FILE", "/home/elf/labsafe/messages.json")
AGENT_DB_FILE = os.environ.get("LABSAFE_AGENT_DB", "/home/elf/labsafe/agent_state.sqlite3")
AGENT_LOG_FILE = os.environ.get("LABSAFE_AGENT_LOG", "/tmp/labsafe_agent.log")
UPLOAD_DIR = os.environ.get("LABSAFE_AGENT_UPLOAD_DIR", "/home/elf/labsafe/uploads/agent")
LABSAFE_BASE_URL = os.environ.get("LABSAFE_BASE_URL", "http://127.0.0.1:5000")
AGENT_HOST = os.environ.get("LABSAFE_AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("LABSAFE_AGENT_PORT", "5055"))
AGENT_SERVICE_URL = os.environ.get(
    "LABSAFE_AGENT_SERVICE_URL", f"http://{AGENT_HOST}:{AGENT_PORT}"
)


DEFAULT_AGENT_CONFIG = {
    "enabled": True,
    "primary_provider": "minimax",
    "provider_order": ["minimax", "deepseek", "local_openai_compatible"],
    "request_timeout_seconds": 30,
    "tool_timeout_seconds": 3,
    "temperature": 0.2,
    "max_tokens": 800,
    "deep_thinking_max_tokens": 1400,
    "deep_thinking_timeout_seconds": 60,
    "conversation_memory": {
        "enabled": True,
        "max_turns": 8,
        "max_chars": 6000,
        "max_item_chars": 900
    },
    "vision": {
        "enabled": True,
        "max_images": 3,
        "max_image_bytes": 4000000,
        "max_side_px": 1280,
        "jpeg_quality": 82
    },
    "web_search": {
        "enabled": True,
        "timeout_seconds": 6,
        "max_results": 5,
        "provider_order": ["bing_rss", "bing", "duckduckgo"]
    },
    "uploads": {
        "enabled": True,
        "max_file_mb": 12,
        "allowed_extensions": ["jpg", "jpeg", "png", "webp", "txt", "md", "json", "csv", "pdf", "docx"],
        "text_preview_chars": 18000,
        "thumbnail_max_px": 480
    },
    "knowledge": {
        "enabled": True,
        "max_hits": 5,
        "sources": [
            "/home/elf/labsafe/LabSafe_实验室安全智能Agent_技术交接文档.md",
            "/home/elf/labsafe/LabSafe_RK3588_技术交接文档.md",
            "/home/elf/labsafe/docs/LabSafe_实验室安全智能Agent_技术交接文档.md",
            "/home/elf/labsafe/docs/LabSafe_RK3588_技术交接文档.md",
            "/home/elf/labsafe/config.json"
        ]
    },
    "providers": {
        "minimax": {
            "enabled": True,
            "api_key": "",
            "base_url": "https://api.minimaxi.com/v1",
            "endpoint": "https://api.minimaxi.com/v1/chat/completions",
            "model": "MiniMax-M3",
            "supports_vision": True,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
        "deepseek": {
            "enabled": True,
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "endpoint": "https://api.deepseek.com/chat/completions",
            "model": "deepseek-v4-flash",
            "supports_vision": False,
        },
        "local_openai_compatible": {
            "enabled": False,
            "api_key": "",
            "base_url": "http://127.0.0.1:8000/v1",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "local-model",
            "supports_vision": False,
        },
    },
    "safety": {
        "temperature_warning_c": 40.0,
        "humidity_low_percent": 15.0,
        "humidity_high_percent": 85.0,
        "camera_stale_seconds": 10.0,
        "confirm_ttl_seconds": 300,
    },
}


def _deep_update(target, source):
    for key, value in (source or {}).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def load_raw_config(config_file=CONFIG_FILE):
    if not os.path.exists(config_file):
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_agent_config(config_file=CONFIG_FILE):
    """Load merged agent config while preserving the legacy ai config path."""
    raw = load_raw_config(config_file)
    config = deepcopy(DEFAULT_AGENT_CONFIG)
    _deep_update(config, raw.get("agent", {}))

    fire_config = raw.get("fire", {})
    if isinstance(fire_config, dict) and fire_config.get("temp_threshold") is not None:
        try:
            config["safety"]["temperature_warning_c"] = float(fire_config["temp_threshold"])
        except (TypeError, ValueError):
            pass

    ai_config = raw.get("ai", {})
    if isinstance(ai_config, dict):
        legacy_model = str(ai_config.get("model") or "").strip().lower()
        legacy_key = str(ai_config.get("api_key") or "").strip()
        if "deepseek" in legacy_model:
            config["primary_provider"] = "deepseek"
            if legacy_key and not config["providers"]["deepseek"].get("api_key"):
                config["providers"]["deepseek"]["api_key"] = legacy_key
        elif "minimax" in legacy_model or legacy_model:
            config["primary_provider"] = "minimax"
            if legacy_key and not config["providers"]["minimax"].get("api_key"):
                config["providers"]["minimax"]["api_key"] = legacy_key

    order = list(config.get("provider_order") or [])
    primary = config.get("primary_provider")
    if primary and primary in config["providers"]:
        order = [primary] + [name for name in order if name != primary]
    config["provider_order"] = [name for name in order if name in config["providers"]]
    return config
