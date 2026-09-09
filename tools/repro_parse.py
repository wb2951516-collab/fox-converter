# -*- coding: utf-8 -*-
"""在独立端口启动开发实例（调试用）

用法: python tools/repro_parse.py
之后在浏览器/脚本里访问 http://127.0.0.1:8734/api/* 验证功能。
导入验证请通过界面或 curl 完成（避免在本脚本内做 HTTP 调用）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading
import time
import uvicorn
from foxmail2md.server.app import app

PORT = 8734

threading.Thread(
    target=lambda: uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning'),
    daemon=True).start()

print(f'开发实例已启动: http://127.0.0.1:{PORT}/')
print('Ctrl+C 退出')
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
