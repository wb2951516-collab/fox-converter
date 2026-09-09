# -*- coding: utf-8 -*-
"""核查真实数据库的存档索引与数据完好性"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from pathlib import Path
from foxmail2md.config import CONFIG_DIR

DB = CONFIG_DIR / 'foxmail2md.db'
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
arcs = conn.execute('SELECT id, name, source_path, email_total, export_dir FROM archives').fetchall()
print(f'存档记录: {len(arcs)} 条')
for a in arcs:
    print(' ', dict(a))
n = conn.execute('SELECT COUNT(*) FROM emails').fetchone()[0]
print(f'邮件记录: {n} 封')
roots = conn.execute("SELECT DISTINCT export_dir FROM archives").fetchall()
for r in roots:
    ed = r['export_dir']
    if ed:
        p = Path(ed)
        exists = p.exists() and any(p.iterdir())
        print(f'导出目录 {ed} → 存在且有内容: {exists}')
conn.close()
