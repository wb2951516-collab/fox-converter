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
    'lang': 'zh-CN',
    'last_source': '',
    'api_key': '',
}

# 配置目录：默认 %APPDATA%/foxmail2md；测试可用 FOX2MD_CONFIG_DIR 隔离，
# 避免 e2e 测试的 save_config 污染真实用户配置
_CONFIG_DIR_OVERRIDE = os.environ.get('FOX2MD_CONFIG_DIR')
CONFIG_DIR = (Path(_CONFIG_DIR_OVERRIDE) if _CONFIG_DIR_OVERRIDE
              else Path(os.environ.get('APPDATA', str(Path.home()))) / 'foxmail2md')
CONFIG_PATH = CONFIG_DIR / 'config.json'

# 中间件每个请求都会读配置：按 mtime 缓存，避免每请求同步读盘
_cache = {'mtime': None, 'cfg': None}


def load_config() -> dict:
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cache['cfg'] is None or _cache['mtime'] != mtime:
        cfg = None
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            except Exception:
                cfg = None
        _cache['cfg'] = {**DEFAULT_CONFIG, **cfg} if cfg else dict(DEFAULT_CONFIG)
        _cache['mtime'] = mtime
    return dict(_cache['cfg'])


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_CONFIG, **cfg}
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    _cache['mtime'] = None  # 下次 load 重新读盘


def get_data_dir(cfg: dict = None) -> Path:
    """数据库与日志所在根目录（可在设置中自定义；测试用环境变量优先）"""
    env = os.environ.get('FOX2MD_DATA_DIR')
    if env:
        return Path(env)
    cfg = cfg or load_config()
    return Path(cfg.get('data_dir') or CONFIG_DIR)


def get_db_path(cfg: dict = None) -> Path:
    return get_data_dir(cfg) / 'foxmail2md.db'


def get_export_dir(cfg: dict = None) -> Path:
    env = os.environ.get('FOX2MD_EXPORT_DIR')
    if env:
        return Path(env)
    cfg = cfg or load_config()
    d = cfg.get('export_dir') or str(Path.home() / 'Documents' / 'foxmail2md_export')
    return Path(d)
