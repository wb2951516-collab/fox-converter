# -*- coding: utf-8 -*-
"""FastAPI 服务：文件选择 / 解析进度 / 邮件列表搜索 / 附件下载 / 设置"""
import asyncio
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core import FoxArchive, count_emails, is_fox_file, parse_mail, export_mail
from ..core.store import Store
from ..config import (load_config, save_config, get_export_dir, get_data_dir,
                      get_db_path)

# ── 全局状态 ──
app = FastAPI(title='Fox Converter')
_tasks = {}  # task_id -> {status, progress, total, current_subject, error, source}
_cfg = load_config()
_store = Store(get_db_path(_cfg))

WEB_DIR = Path(getattr(sys, '_MEIPASS', str(Path(__file__).resolve().parent.parent))) / 'web'
SKILL_DIR = Path(getattr(sys, '_MEIPASS', str(Path(__file__).resolve().parent.parent))) / 'skill'


# ── 鉴权：非同源（跨机/其他来源）访问 /api/* 需 X-API-Key ─────────────────────
@app.middleware('http')
async def api_key_guard(request, call_next):
    path = request.url.path
    if path.startswith('/api/') and path != '/api/health':
        origin = request.headers.get('origin', '')
        same_origin = origin in ('', f'http://127.0.0.1:{_cfg.get("port", 8732)}',
                                 f'http://localhost:{_cfg.get("port", 8732)}')
        if not same_origin:
            key = _cfg.get('api_key', '')
            if key and request.headers.get('x-api-key') != key:
                return JSONResponse({'detail': 'API Key 无效或缺失'}, status_code=401)
    return await call_next(request)


# ── 文件选择（原生对话框，后台线程） ──────────────────────────────────────────
def _tk_file_dialog():
    """在子线程中打开 tkinter 多选文件对话框，返回路径列表"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    paths = filedialog.askopenfilenames(
        title='选择 Foxmail 存档文件（可多选）',
        filetypes=[('Foxmail 存档', '*.fox'), ('所有文件', '*.*')],
    )
    root.destroy()
    return list(paths)


def _tk_folder_dialog():
    """在子线程中打开文件夹选择对话框"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askdirectory(title='选择包含 .fox 存档的文件夹')
    root.destroy()
    return path


@app.post('/api/browse')
async def browse_file():
    """打开原生多选文件对话框，返回选中的 .fox 文件路径列表"""
    loop = asyncio.get_event_loop()
    paths = await loop.run_in_executor(None, _tk_file_dialog)
    valid = [p for p in paths if is_fox_file(p)]
    skipped = len(paths) - len(valid)
    return {'paths': valid, 'skipped': skipped}


@app.post('/api/browse_folder')
async def browse_folder():
    """打开文件夹对话框，递归扫描其中全部 .fox"""
    loop = asyncio.get_event_loop()
    folder = await loop.run_in_executor(None, _tk_folder_dialog)
    if not folder:
        return {'paths': []}
    valid = [str(p) for p in Path(folder).rglob('*.fox') if is_fox_file(p)]
    return {'paths': sorted(valid), 'folder': folder}


@app.get('/api/peek')
async def peek_file(path: str = Query('')):
    """校验手动输入的 .fox 路径并返回基本信息"""
    p = Path(path.strip().strip('"'))
    if not p.exists():
        raise HTTPException(404, '文件不存在')
    if not is_fox_file(p):
        return {'path': str(p), 'warning': '该文件不是 .fox 存档（缺少 ARCF 头）'}
    return {'path': str(p), 'total': count_emails(p), 'size': os.path.getsize(p)}


@app.post('/api/diskcheck')
async def disk_check(body: dict = Body(...)):
    """检查导出盘剩余空间是否足够容纳所选存档的导出内容"""
    paths = body.get('paths') or []
    sizes, counts, total = {}, {}, 0
    for p in paths:
        try:
            s = os.path.getsize(p)
            sizes[p] = s
            total += s
        except OSError:
            continue
    export_root = get_export_dir(load_config())
    free = shutil.disk_usage(str(export_root)).free
    need = total * 1.2
    return {'sizes': sizes, 'counts': counts, 'need': need, 'free': free,
            'export_root': str(export_root)}


# ── 解析任务（后台线程 + SSE 进度，支持批量） ──────────────────────────────────
def _unique_export_dir(export_root: Path, stem: str) -> Path:
    """为存档分配不冲突的导出目录"""
    d = export_root / stem
    n = 2
    while d.exists() and any(d.iterdir()):
        d = export_root / f'{stem}_{n}'
        n += 1
    return d


def _parse_worker(task_id: str, paths: list):
    """后台批量解析线程：每个 .fox 一个存档，全局 Message-ID 去重"""
    task = _tasks[task_id]
    export_root = get_export_dir(load_config())
    known_ids = _store.known_message_ids()
    t0 = time.time()

    try:
        for file_index, fox_path in enumerate(paths):
            if task.get('cancelled'):
                break
            source_name = Path(fox_path).name
            task.update({
                'file_index': file_index + 1, 'file_total': len(paths),
                'filename': source_name, 'current': 0, 'total': 0,
                'subject': '', 'file_status': 'running',
            })

            # 导出目录：按文件名，冲突自动加序号
            export_dir = _unique_export_dir(export_root, Path(fox_path).stem)
            att_dir = export_dir / 'attachments'
            export_dir.mkdir(parents=True, exist_ok=True)

            # 同路径重复导入：复用存档记录
            existing = _store.get_archive_by_path(fox_path)
            if existing and existing['export_dir']:
                old_dir = Path(existing['export_dir'])
                if old_dir.exists():
                    export_dir = old_dir
            archive_id = _store.insert_archive(
                name=export_dir.name, source_name=source_name, source_path=fox_path,
                total_size=os.path.getsize(fox_path), export_dir=str(export_dir),
            )

            archive = FoxArchive(fox_path)
            total = count_emails(fox_path)
            task['total'] = total
            entries = []
            imported = skipped = errors = 0
            for index, offset, raw in archive:
                if task.get('cancelled'):
                    break
                mail = parse_mail(index, offset, raw)

                # 全局去重：其他存档已导入的邮件跳过
                if mail.message_id in known_ids:
                    skipped += 1
                    task.update({'current': index + 1, 'subject': (mail.subject or '')[:40]})
                    continue

                try:
                    entry = export_mail(mail, export_dir, att_dir)
                    entries.append(entry)
                    all_files = entry['attachment_files'] + entry.get('inline_files', [])
                    _store.insert_mail(source_name, mail, all_files, entry['md_file'],
                                       archive_id=archive_id)
                    known_ids.add(mail.message_id)
                    imported += 1
                except Exception:
                    errors += 1
                task.update({'current': index + 1, 'subject': (mail.subject or '')[:40]})
                if index % 10 == 0:
                    task['elapsed'] = time.time() - t0

            _store.update_archive_counts(archive_id, imported, skipped)
            from ..core.exporter import write_manifest
            write_manifest(export_dir / 'manifest.json', entries, source_name, total)
            task['file_status'] = 'cancelled' if task.get('cancelled') else 'done'
            task['imported'] = task.get('imported', 0) + imported
            task['skipped'] = task.get('skipped', 0) + skipped
            task['errors'] = task.get('errors', 0) + errors
            task['done_files'] = task.get('done_files', []) + [source_name]
            if task.get('cancelled'):
                break

        task['status'] = 'cancelled' if task.get('cancelled') else 'done'
        task['elapsed'] = time.time() - t0
        _cfg['last_source'] = paths[0] if paths else ''
        save_config(_cfg)
    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)


@app.post('/api/parse')
async def start_parse(body: dict = Body(...)):
    """启动批量解析任务"""
    paths = body.get('paths') or ([body['path']] if body.get('path') else [])
    paths = [p.strip() for p in paths if p and Path(p.strip()).exists()]
    if not paths:
        raise HTTPException(400, '没有有效的文件路径')
    bad = [p for p in paths if not is_fox_file(p)]
    if bad:
        raise HTTPException(400, f'存在非 .fox 存档文件：{Path(bad[0]).name}')

    # 磁盘空间检查：导出内容约与源文件相当，剩余不足 1.15 倍时拒绝
    export_root = get_export_dir(load_config())
    need = sum(os.path.getsize(p) for p in paths) * 1.15
    free = shutil.disk_usage(str(export_root)).free
    if free < need:
        raise HTTPException(
            400, f'磁盘空间不足：约需 {need/1024/1024/1024:.1f}GB，'
                 f'导出目录所在盘仅剩 {free/1024/1024/1024:.1f}GB')

    # 导出根目录确保存在
    export_root.mkdir(parents=True, exist_ok=True)

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {
        'status': 'running', 'current': 0, 'total': 0,
        'file_index': 0, 'file_total': len(paths), 'filename': '',
        'subject': '', 'errors': 0, 'elapsed': 0, 'imported': 0, 'skipped': 0,
        'done_files': [], 'source': paths[0],
        'export_dir': str(export_root),
    }
    t = threading.Thread(target=_parse_worker, args=(task_id, paths), daemon=True)
    t.start()
    return {'task_id': task_id}


@app.get('/api/parse/status/{task_id}')
async def parse_status_sse(task_id: str):
    """SSE 流式推送解析进度（批量：文件级 + 邮件级双进度）"""
    if task_id not in _tasks:
        raise HTTPException(404, '任务不存在')

    async def event_stream():
        while True:
            task = _tasks.get(task_id, {})
            data = {
                'status': task.get('status', 'unknown'),
                'file_index': task.get('file_index', 0),
                'file_total': task.get('file_total', 0),
                'filename': task.get('filename', ''),
                'file_status': task.get('file_status', ''),
                'done_files': task.get('done_files', []),
                'current': task.get('current', 0),
                'total': task.get('total', 0),
                'subject': task.get('subject', ''),
                'imported': task.get('imported', 0),
                'skipped': task.get('skipped', 0),
                'errors': task.get('errors', 0),
                'elapsed': task.get('elapsed', 0),
                'export_dir': task.get('export_dir', ''),
                'error': task.get('error', ''),
            }
            yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'
            if data['status'] in ('done', 'error', 'cancelled'):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type='text/event-stream')


# ── 邮件列表 / 详情 / 搜索 ───────────────────────────────────────────────────
@app.get('/api/archives')
async def list_archives():
    """已导入存档列表（含邮件数/占用空间），供侧栏管理与筛选"""
    return {'archives': _store.list_archives()}


@app.delete('/api/archives/{archive_id}')
async def delete_archive(archive_id: int):
    """删除存档：清数据库记录 + 删除其导出目录（MD/纯文本/附件）"""
    arc = _store.get_archive(archive_id)
    if not arc:
        raise HTTPException(404, '存档不存在')
    export_dir = _store.delete_archive(archive_id)
    removed = ''
    if export_dir and Path(export_dir).exists():
        shutil.rmtree(export_dir, ignore_errors=True)
        removed = export_dir
    return {'deleted': archive_id, 'export_dir_removed': removed}


@app.get('/api/sources')
async def list_sources():
    return {'sources': _store.list_sources()}


@app.get('/api/stats')
async def stats(source: str = Query(''), archive_id: int = Query(0)):
    if not archive_id and not source:
        archives = _store.list_archives()
        if not archives:
            return {'total': 0}
        source = ''
    return _store.stats(source, archive_id)


@app.get('/api/mails')
async def list_mails(
    source: str = Query(''),
    archive_id: int = Query(0),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    search: str = Query(''),
    order: str = Query('date_desc'),
):
    if not archive_id and not source:
        archives = _store.list_archives()
        if not archives:
            return {'total': 0, 'page': page, 'per_page': per_page, 'items': []}
    return _store.list_mails(source, archive_id, page, per_page, search, order)


@app.get('/api/mails/{mail_id}')
async def get_mail(mail_id: int):
    mail = _store.get_mail(mail_id)
    if not mail:
        raise HTTPException(404, '邮件不存在')
    return mail


# ── 附件下载 ─────────────────────────────────────────────────────────────────
@app.get('/api/attachment/{mail_id}/{filename:path}')
async def download_attachment(mail_id: int, filename: str):
    mail = _store.get_mail(mail_id)
    if not mail:
        raise HTTPException(404, '邮件不存在')
    # 1) 按登记的附件清单匹配，在其存档导出目录中定位
    for rel in mail['attachment_files']:
        if rel == filename or rel.endswith(filename) or filename in rel:
            for arc in _store.list_archives():
                if arc.get('export_dir'):
                    full = Path(arc['export_dir']) / rel
                    if full.exists():
                        return FileResponse(str(full), filename=Path(rel).name)
    # 2) 兜底：按相对路径在所有存档导出目录直接找（内嵌图等未登记文件）
    for arc in _store.list_archives():
        if arc.get('export_dir'):
            full = Path(arc['export_dir']) / filename
            if full.exists():
                return FileResponse(str(full), filename=Path(filename).name)
    raise HTTPException(404, '附件不存在')


# ── 设置 ─────────────────────────────────────────────────────────────────────
@app.get('/api/health')
async def health():
    """探活：供本机前端与外部智能体使用"""
    stats = _store.stats()
    return {
        'app': 'Fox Converter',
        'version': '2.1.0',
        'archives': len(_store.list_archives()),
        'emails': stats.get('total', 0),
        'auth_required': bool(_cfg.get('api_key')),
        'port': _cfg.get('port', 8732),
    }


@app.get('/api/agent-key')
async def get_agent_key():
    """获取（不存在则生成）Agent 接入 API Key"""
    if not _cfg.get('api_key'):
        import secrets
        _cfg['api_key'] = secrets.token_hex(16)
        save_config(_cfg)
    return {
        'api_key': _cfg['api_key'],
        'port': _cfg.get('port', 8732),
        'auth_required': True,
        'archives': len(_store.list_archives()),
        'emails': _store.stats().get('total', 0),
        'skill_url': f'http://127.0.0.1:{_cfg.get("port", 8732)}/skill/download',
    }


@app.post('/api/agent-key/regenerate')
async def regenerate_agent_key():
    import secrets
    _cfg['api_key'] = secrets.token_hex(16)
    save_config(_cfg)
    return {'api_key': _cfg['api_key']}


@app.get('/skill/SKILL.md')
async def skill_md():
    f = SKILL_DIR / 'SKILL.md'
    if not f.exists():
        raise HTTPException(404, '技能文件缺失')
    return FileResponse(str(f), media_type='text/markdown')


@app.get('/skill/download')
async def skill_download():
    """打包技能为 zip 供智能体下载安装"""
    import io
    import zipfile
    f = SKILL_DIR / 'SKILL.md'
    if not f.exists():
        raise HTTPException(404, '技能文件缺失')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(str(f), 'fox-converter/SKILL.md')
    buf.seek(0)
    return StreamingResponse(
        buf, media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename=fox-converter-skill.zip'},
    )


def _dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


@app.get('/api/paths')
async def get_paths():
    cfg = load_config()
    data_dir = get_data_dir(cfg)
    export_dir = get_export_dir(cfg)
    db_file = get_db_path(cfg)
    return {
        'data_dir': str(data_dir),
        'data_size': db_file.stat().st_size if db_file.exists() else 0,
        'export_dir': str(export_dir),
        'export_size': _dir_size(export_dir) if export_dir.exists() else 0,
    }


@app.post('/api/browse_dir')
async def browse_dir():
    """通用文件夹选择对话框"""
    loop = asyncio.get_event_loop()
    folder = await loop.run_in_executor(None, _tk_folder_dialog)
    return {'path': folder or ''}


@app.post('/api/migrate')
async def migrate(body: dict = Body(...)):
    """迁移存储位置。data_dir：复制数据库，重启生效；export_dir：后台复制文件后切换"""
    cfg = load_config()
    result = {}

    new_data = (body.get('data_dir') or '').strip()
    if new_data:
        target = Path(new_data)
        target.mkdir(parents=True, exist_ok=True)
        src_db = get_db_path(cfg)
        dst_db = target / 'foxmail2md.db'
        import sqlite3
        src = sqlite3.connect(str(src_db))
        dst = sqlite3.connect(str(dst_db))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        cfg['data_dir'] = str(target)
        result['data_dir'] = {'target': str(target), 'status': 'copied',
                              'note': '重启 Fox Converter 后生效'}
        save_config(cfg)

    new_export = (body.get('export_dir') or '').strip()
    if new_export:
        target = Path(new_export)
        if target.exists() and any(target.iterdir()):
            raise HTTPException(400, '目标导出目录非空，请选择空文件夹')
        old_root = get_export_dir(cfg)
        if not old_root.exists():
            raise HTTPException(400, '当前导出目录不存在，无需迁移')
        task_id = str(uuid.uuid4())[:8]
        _tasks[task_id] = {'status': 'running', 'kind': 'migrate-export',
                           'old_root': str(old_root), 'new_root': str(target),
                           'copied': 0}
        threading.Thread(target=_migrate_export_worker,
                         args=(task_id, old_root, target), daemon=True).start()
        result['export_dir'] = {'task_id': task_id, 'status': 'running',
                                'note': '后台复制中，完成后自动切换'}
    return result


def _migrate_export_worker(task_id: str, old_root: Path, target: Path):
    task = _tasks[task_id]
    try:
        target.mkdir(parents=True, exist_ok=True)
        items = [p for p in old_root.rglob('*') if p.is_file()]
        task['total'] = len(items)
        for i, src in enumerate(items):
            rel = src.relative_to(old_root)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            task['copied'] = i + 1
        # 切换 DB 中的绝对路径
        with _store._conn() as c:
            c.execute('UPDATE archives SET export_dir = REPLACE(export_dir, ?, ?)',
                      (str(old_root), str(target)))
        cfg = load_config()
        cfg['export_dir'] = str(target)
        save_config(cfg)
        shutil.rmtree(old_root, ignore_errors=True)
        task['status'] = 'done'
    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)


@app.get('/api/migrate/status/{task_id}')
async def migrate_status(task_id: str):
    task = _tasks.get(task_id, {})
    return {'status': task.get('status', 'unknown'),
            'copied': task.get('copied', 0), 'total': task.get('total', 0),
            'error': task.get('error', '')}


@app.get('/api/settings')
async def get_settings():
    return load_config()


@app.put('/api/settings')
async def update_settings(body: dict = Body(...)):
    global _cfg
    _cfg = {**_cfg, **body}
    save_config(_cfg)
    return _cfg


# ── 静态前端（禁缓存：本地应用，避免浏览器拿着旧 JS/CSS 出现样式与逻辑错乱） ──
class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
        return resp


if WEB_DIR.exists():
    app.mount('/', NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name='web')
