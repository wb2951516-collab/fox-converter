# -*- coding: utf-8 -*-
"""检查 exe 是否打包了 tkinter"""
data = open(r'D:\AI-coding\Project_pending_iteration\Foxmail存档文件转换MD工具\dist\FoxConverter.exe', 'rb').read()
for m in [b'_tkinter', b'tcl86t', b'TCL_LIBRARY', b'tkinter']:
    print(m.decode(), '→', '存在' if m in data else '缺失')
