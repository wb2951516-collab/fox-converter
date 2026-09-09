# -*- coding: utf-8 -*-
"""诊断迁移后附件下载失败"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading, time, json, urllib.request
from pathlib import Path
import uvicorn
from foxmail2md.server.app import app
from foxmail2md.config import load_config

PORT = 8734
BASE = f'http://127.0.0.1:{PORT}'
TMP = Path(os.environ.get('FOX2MD_DATA_DIR', ''))

t = threading.Thread(target=lambda: uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning'), daemon=True)
t.start()
time.sleep(4)

cfg = load_config()
print('config.export_dir =', cfg.get('export_dir'))
print('config.data_dir   =', cfg.get('data_dir'))
arcs = json.loads(urllib.request.urlopen(f'{BASE}/api/archives').read())['archives']
print('存档 export_dir   =', arcs[0]['export_dir'])
mails = json.loads(urllib.request.urlopen(f'{BASE}/api/mails?per_page=100').read())
target = None
for m in mails['items']:
    if m['has_attachments']:
        d = json.loads(urllib.request.urlopen(f"{BASE}/api/mails/{m['id']}").read())
        if d['attachment_files']:
            target = (m['id'], d['attachment_files'][0])
            break
print('目标附件:', target)
mid, rel = target
# 磁盘上找
for arc in arcs:
    p = Path(arc['export_dir']) / rel
    print(f'  尝试 {p} → 存在={p.exists()}')
# HTTP 下载
try:
    with urllib.request.urlopen(f'{BASE}/api/attachment/{mid}/{rel}', timeout=30) as resp:
        print('HTTP 下载:', resp.status, len(resp.read()), 'B')
except urllib.error.HTTPError as e:
    print('HTTP 下载失败:', e.code, e.read().decode()[:100])
