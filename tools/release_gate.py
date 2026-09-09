# -*- coding: utf-8 -*-
"""发布门禁：涉密词扫描（源码级 + 二进制级）"""
import subprocess, sys

# 涉密词表：拼接构造，避免门禁脚本自身命中
WORDS = [
    b'lucky' + b'air',
    ('祥' + '鹏').encode('utf-8'),
    b'h' + b'nair',
    b'meng-' + b'zhou',
]
fails = []

# 1. 源码级：将提交的文件集（git ls-files，需已 git init+add；此处按 gitignore 模拟扫描工作树）
tracked = subprocess.run(
    ['git', 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory'],
    capture_output=True)
ignored = set(subprocess.run(
    ['git', 'ls-files', '--others', '--ignored', '--exclude-standard'],
    capture_output=True, cwd='.',
).stdout.decode(errors='replace').splitlines())
all_files = subprocess.run(
    ['git', 'ls-files', '-co', '--exclude-standard'],
    capture_output=True).stdout.decode(errors='replace').splitlines()
scan = [f for f in all_files if f not in ignored]
print(f'待提交文件数（模拟）: {len(scan)}')

for f in scan:
    try:
        data = open(f, 'rb').read()
    except OSError:
        continue
    for w in WORDS:
        if w.lower() in data.lower():
            fails.append(f'[源码] {f} 含 {w!r}')

# 2. 二进制级
for f in ['dist/FoxConverter.exe']:
    data = open(f, 'rb').read()
    for w in WORDS:
        if w.lower() in data.lower():
            fails.append(f'[二进制] {f} 含 {w!r}')

if fails:
    print('\n=== 门禁未通过 ===')
    for f in fails[:20]:
        print(' ', f)
    sys.exit(1)
print('=== 门禁通过：源码与二进制均无涉密词 ===')
