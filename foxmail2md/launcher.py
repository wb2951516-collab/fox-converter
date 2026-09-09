# -*- coding: utf-8 -*-
"""启动器：起本地服务 → 按设置打开浏览器 → 系统托盘

- 双击运行：打开界面，托盘常驻（打开界面 / 打开导出目录 / 退出）
- --no-tray：仅控制台模式（开发/调试用）
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
import threading


def _resource_path(rel: str) -> str:
    """兼容 PyInstaller 冻结环境的资源路径"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _open_in_browser(url: str, browser: str):
    """按设置打开浏览器；chrome/edge 用 app 模式（无地址栏的独立窗口）"""
    candidates = []
    if browser == 'chrome':
        candidates = [
            os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%LocalAppData%\Google\Chrome\Application\chrome.exe'),
        ]
    elif browser == 'edge':
        candidates = [
            os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        ]
    for p in candidates:
        if os.path.exists(p):
            # 列表参数直接启动，不经 shell，避免任何拼接注入
            subprocess.Popen([p, f'--app={url}'], close_fds=True)
            return
    webbrowser.open(url)


def _wait_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_ok(port):
            return True
        time.sleep(0.3)
    return False


def main(no_tray: bool = False):
    from .config import load_config
    cfg = load_config()
    port = int(cfg.get('port', 8732))
    url = f'http://127.0.0.1:{port}/'

    # 冻结为 noconsole exe 时 sys.stdout/stderr 为 None，
    # uvicorn 日志初始化会因此挂掉（分离启动必现）→ 重定向到日志文件
    if getattr(sys, 'frozen', False) or sys.stdout is None or sys.stderr is None:
        try:
            from pathlib import Path
            from .config import get_data_dir
            log_dir = get_data_dir(cfg) / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = open(log_dir / 'app.log', 'a', buffering=1,
                            encoding='utf-8', errors='replace')
            sys.stdout = log_file
            sys.stderr = log_file
        except Exception:
            pass

    print(f'Fox Converter 启动中… {url}')

    # 服务线程 + 启动监督：失败必须可见，绝不留下无服务的托盘僵尸
    supervisor = threading.Thread(
        target=_server_supervisor, args=(port, url, cfg), daemon=True)
    supervisor.start()

    if no_tray:
        print('控制台模式：Ctrl+C 退出')
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            sys.exit(0)

    # 托盘主循环（阻塞）
    _run_tray(url, cfg)


def _port_ok(port: int) -> bool:
    """TCP 探活：固定环回地址 + 数值端口，无 URL 构造"""
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _server_supervisor(port: int, url: str, cfg: dict):
    """监督服务线程：就绪后打开浏览器；20 秒不可用则弹窗报错并退出（不静默）"""
    t = threading.Thread(target=_run_server, args=(port,), daemon=True)
    t.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_ok(port):
            print(f'服务已就绪: {url}')
            threading.Thread(
                target=lambda: _open_in_browser(url, cfg.get('browser', 'default')),
                daemon=True).start()
            return
        if not t.is_alive():
            break
        time.sleep(0.3)
    if _port_ok(port):
        return
    msg = (f'Fox Converter 服务启动失败。\n\n'
           f'可能原因：端口 {port} 被其他程序（或旧实例）占用。\n'
           f'日志：%APPDATA%\\foxmail2md\\logs\\app.log')
    print(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, 'Fox Converter', 0x10)
    except Exception:
        pass
    os._exit(1)


def _run_server(port: int):
    import uvicorn
    from .server.app import app
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


def _run_tray(url: str, cfg: dict):
    import pystray
    from PIL import Image

    icon_path = _resource_path(os.path.join('assets', 'icon.png'))
    try:
        image = Image.open(icon_path)
    except Exception:
        image = Image.new('RGB', (64, 64), (37, 99, 235))

    def on_open(icon, item):
        _open_in_browser(url, cfg.get('browser', 'default'))

    def on_open_dir(icon, item):
        from .config import get_export_dir
        d = get_export_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem('打开界面', on_open, default=True),
        pystray.MenuItem('打开导出目录', on_open_dir),
        pystray.MenuItem('退出', on_quit),
    )
    icon = pystray.Icon('foxconverter', image, 'Fox Converter — 邮件存档转换', menu)
    icon.run()


if __name__ == '__main__':
    main(no_tray='--no-tray' in sys.argv)
