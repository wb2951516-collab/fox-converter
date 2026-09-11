# -*- coding: utf-8 -*-
"""清理终检：环境是否达到新用户安装状态"""
import os
import subprocess
from pathlib import Path

appdata = Path(os.environ.get('APPDATA', ''))
home = Path.home()
localapp = Path(os.environ.get('LOCALAPPDATA', ''))

def dir_exists(p):
    return Path(p).exists()

print('=== Fox Converter 清理终检 ===')
items = [
    ('配置/数据库', appdata / 'foxmail2md'),
    ('导出目录', home / 'Documents' / 'foxmail2md_export'),
    ('Agent 技能', home / '.zcode' / 'skills' / 'fox-converter'),
    ('程序目录 D', Path(r'D:\Fox Converter')),
    ('程序目录 C', localapp / 'Programs' / 'Fox Converter'),
    ('测试安装', localapp / 'Programs' / 'FoxConv-Test'),
    ('测试安装2', localapp / 'Programs' / 'FoxConv-Final'),
    ('开始菜单', appdata / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Fox Converter'),
    ('桌面快捷方式', home / 'Desktop' / 'Fox Converter.lnk'),
]
clean = True
for name, p in items:
    ok = not p.exists()
    clean &= ok
    print(f'  {"✓ 已清" if ok else "✗ 残留: " + str(p)}  ({name})')

r = subprocess.run(['tasklist'], capture_output=True, text=True)
n = sum(1 for line in r.stdout.splitlines() if 'FoxConverter' in line)
print(f'  {"✓ 无进程" if n == 0 else "✗ 进程数 " + str(n)}  (FoxConverter)')
r2 = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
busy = any(':8732' in line and 'LISTENING' in line for line in r2.stdout.splitlines())
print(f'  {"✓ 端口 8732 空闲" if not busy else "✗ 端口 8732 被占用"}')

build_dir = Path(__file__).resolve().parent.parent / 'installer' / 'build'
setups = sorted(build_dir.glob('FoxConverter_Setup_v*.exe'), key=lambda p: p.stat().st_mtime)
if setups:
    setup = setups[-1]
    print(f'  ✓ 新安装包就绪  ({setup.name}, {setup.stat().st_size/1024/1024:.0f}MB)')
else:
    print('  ✗ 安装包缺失')

print()
print('=== 环境已完全干净，可安装测试 ===' if clean and n == 0 and not busy else '=== 仍有残留，见上 ===')
