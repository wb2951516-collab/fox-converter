# -*- coding: utf-8 -*-
"""紧急恢复：把被 e2e 测试误迁移的用户导出数据搬回原位并修复索引"""
import os, sys, shutil, sqlite3
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from foxmail2md.config import CONFIG_DIR

DB = CONFIG_DIR / 'foxmail2md.db'
REAL_ROOT = Path.home() / 'Documents' / 'foxmail2md_export'

# 找最近的 fox_newuser 临时目录里的 export_moved
tmp_base = Path(os.environ.get('TEMP', '')) 
cands = sorted(tmp_base.glob('fox_newuser_*/export_moved'), key=lambda p: p.stat().st_mtime, reverse=True)
print('候选临时目录:', [str(c) for c in cands])
if not cands:
    print('未找到被迁移的数据，检查是否已恢复')
    sys.exit(0)

src_root = cands[0]
REAL_ROOT.mkdir(parents=True, exist_ok=True)
moved = 0
for item in src_root.iterdir():
    dst = REAL_ROOT / item.name
    if dst.exists():
        # 同名冲突：用户原位可能已有部分数据，跳过已存在的
        print(f'跳过已存在: {item.name}')
        continue
    shutil.move(str(item), str(dst))
    moved += 1
print(f'搬回 {moved} 个条目 → {REAL_ROOT}')

# 修复 DB 里的绝对路径
conn = sqlite3.connect(str(DB))
with conn:
    conn.execute('UPDATE archives SET export_dir = REPLACE(export_dir, ?, ?)',
                 (str(src_root), str(REAL_ROOT)))
n = conn.execute('SELECT id, name, export_dir FROM archives').fetchall()
conn.close()
print('--- 当前存档索引 ---')
for r in n:
    print(r)
print('=== 恢复完成 ===')
