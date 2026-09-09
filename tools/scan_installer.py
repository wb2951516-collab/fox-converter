# -*- coding: utf-8 -*-
"""安装包二进制涉密扫描"""
data = open('installer/build/FoxConverter_Setup_v2.1.0.exe', 'rb').read()
WORDS = [b'lucky' + b'air', ('祥' + '鹏').encode(), b'h' + b'nair', b'meng-' + b'zhou']
hits = [w for w in WORDS if w.lower() in data.lower()]
print('安装包二进制扫描:', ('命中 ' + repr(hits)) if hits else '干净')
