# -*- coding: utf-8 -*-
"""配置管理：持久化到 %APPDATA%/foxmail2md/config.json"""
import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    'theme': 'light',
    'accent_color': '#2563eb',
    'browser': 'default',
    'browser_path': '',
    'export_dir': '',
    'data_dir': '',
    'port': 8732,
    'last_source': '',
    'api_key': '',
}

CONFIG_DIR = Path(os.environ.get('APPDATA', str(Path.home()))) / 'foxmail2md'
CONFIG_PATH = CONFIG_DIR / 'config.json'


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_CONFIG, **cfg}
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def get_data_dir(cfg: dict = None) -> Path:
    """数据库与日志所在根目录（可在设置中自定义）"""
    cfg = cfg or load_config()
    return Path(cfg.get('data_dir') or CONFIG_DIR)


def get_db_path(cfg: dict = None) -> Path:
    return get_data_dir(cfg) / 'foxmail2md.db'


def get_export_dir(cfg: dict = None) -> Path:
    cfg = cfg or load_config()
    d = cfg.get('export_dir') or str(Path.home() / 'Documents' / 'foxmail2md_export')
    return Path(d)
