# -*- coding: utf-8 -*-
"""检查 exe 是否打包了 tkinter"""
from pathlib import Path

exe = Path(__file__).resolve().parent.parent / 'dist' / 'FoxConverter.exe'
data = exe.read_bytes()
for m in [b'_tkinter', b'tcl86t', b'TCL_LIBRARY', b'tkinter']:
    print(m.decode(), '→', '存在' if m in data else '缺失')
