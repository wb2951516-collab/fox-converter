# -*- coding: utf-8 -*-
"""只读探测本机 Fox Converter 数据库规模与配置（grill-me 前置调查）"""
import json
import os
import sqlite3

print('sqlite version:', sqlite3.sqlite_version)
import json as _json
_cfg = os.path.expandvars(r'%APPDATA%\foxmail2md\config.json')
_dd = ''
if os.path.exists(_cfg):
    try:
        _dd = _json.load(open(_cfg, encoding='utf-8')).get('data_dir') or ''
    except Exception:
        pass
p = (os.path.join(_dd, 'foxmail2md.db') if _dd
     else os.path.expandvars(r'%APPDATA%\foxmail2md\foxmail2md.db'))
print('db path:', p)
print('db exists:', os.path.exists(p))
if os.path.exists(p):
    print('db size MB:', round(os.path.getsize(p) / 1048576, 1))
    con = sqlite3.connect('file:' + p.replace('\\', '/') + '?mode=ro', uri=True)
    cur = con.cursor()
    try:
        print('emails:', cur.execute('SELECT COUNT(*) FROM emails').fetchone()[0])
        body = cur.execute(
            'SELECT SUM(LENGTH(body_text)+LENGTH(body_html)+LENGTH(body_md)) FROM emails'
        ).fetchone()[0]
        print('body bytes total MB:', round((body or 0) / 1048576, 1))
        mx = cur.execute('SELECT MAX(LENGTH(body_html)) FROM emails').fetchone()[0] or 0
        print('max single body_html KB:', round(mx / 1024, 1))
        print('archives:', cur.execute('SELECT COUNT(*) FROM archives').fetchone()[0])
        print('tables:', [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
    except Exception as e:
        print('query err:', e)
    con.close()

cfg = os.path.expandvars(r'%APPDATA%\foxmail2md\config.json')
if os.path.exists(cfg):
    raw = open(cfg, encoding='utf-8').read()
    try:
        c = json.loads(raw)
        print('data_dir:', c.get('data_dir', '(default APPDATA)'))
        print('export_dir:', c.get('export_dir', '(unset)'))
        print('port:', c.get('port'))
    except Exception:
        print('config raw:', raw[:300])
