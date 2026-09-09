# -*- coding: utf-8 -*-
"""导出器：Mail 对象 → Markdown 文件 + 附件 + manifest"""
import os
import re
import json
import hashlib
from pathlib import Path

from .mail_parser import Mail, Attachment

# HTML 中未被任何部件解析的 cid 图片引用（源数据 cid 不匹配，无法恢复）
_UNRESOLVED_CID_RE = re.compile(
    r'<img[^>]+src\s*=\s*["\']cid:[^"\']+["\'][^>]*>', re.I)
# 兜底：替换后仍残留的 cid 字符串
_LEFTOVER_CID_RE = re.compile(r'cid:[^\s"\'<>)\]]+', re.I)


def _safe_filename(name: str, maxlen: int = 60) -> str:
    """清洗文件名：去除非法字符、控制长度"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.strip('. ')
    if len(name) > maxlen:
        name = name[:maxlen].strip()
    return name or 'untitled'


def _msg_hash(mail: Mail) -> str:
    """生成邮件唯一短 hash（用于附件目录名与去重）"""
    src = (mail.message_id or '').encode('utf-8')
    if not src:
        src = f'{mail.offset}:{mail.raw_size}'.encode()
    return hashlib.md5(src).hexdigest()[:12]


def _build_front_matter(mail: Mail, rel_paths: dict) -> str:
    """YAML front matter"""
    lines = ['---']
    lines.append(f'message_id: "{_yaml_escape(mail.message_id)}"')
    lines.append(f'subject: "{_yaml_escape(mail.subject)}"')
    lines.append(f'from: "{_yaml_escape(mail.from_)}"')
    lines.append(f'to: "{_yaml_escape(mail.to)}"')
    if mail.cc:
        lines.append(f'cc: "{_yaml_escape(mail.cc)}"')
    if mail.date_iso:
        lines.append(f'date: "{mail.date_iso}"')
    elif mail.date:
        lines.append(f'date: "{_yaml_escape(mail.date)}"')
    if rel_paths.get('attachments'):
        lines.append('attachments:')
        for a in rel_paths['attachments']:
            lines.append(f'  - "{_yaml_escape(a)}"')
    if rel_paths.get('inline_images'):
        lines.append('inline_images:')
        for a in rel_paths['inline_images']:
            lines.append(f'  - "{_yaml_escape(a)}"')
    lines.append(f'has_attachments: {str(mail.has_attachments).lower()}')
    lines.append(f'source_offset: {mail.offset}')
    lines.append(f'raw_size: {mail.raw_size}')
    lines.append('---')
    return '\n'.join(lines)


def _yaml_escape(s: str) -> str:
    return s.replace('"', '\\"').replace('\n', ' ').replace('\r', '')


def export_mail(mail: Mail, out_dir: Path, att_dir: Path) -> dict:
    """导出单封邮件 → out_dir/YYYYMMDD_subject_hash.md + 附件

    返回 manifest 条目 dict。
    """
    h = _msg_hash(mail)
    date_part = ''
    if mail.date_iso:
        date_part = mail.date_iso[:10].replace('-', '')
    else:
        date_part = 'nodate'
    subject_safe = _safe_filename(mail.subject or '无主题')
    md_name = f'{date_part}_{subject_safe}_{h}.md'
    md_path = out_dir / md_name
    # 同 Message-ID 的邮件在存档中出现多次时避免同名覆盖
    if md_path.exists():
        md_name = f'{date_part}_{subject_safe}_{h}_{mail.index}.md'
        md_path = out_dir / md_name
    txt_name = md_name[:-3] + '.txt'

    # 附件目录
    mail_att_dir = att_dir / h
    rel_att = []
    rel_img = []

    if mail.attachments or mail.inline_images:
        mail_att_dir.mkdir(parents=True, exist_ok=True)

    for att in mail.attachments:
        fname = _safe_filename(att.filename)
        apath = mail_att_dir / fname
        # 重名加序号
        n = 1
        while apath.exists():
            stem, ext = os.path.splitext(fname)
            apath = mail_att_dir / f'{stem}_{n}{ext}'
            n += 1
        apath.write_bytes(att.payload)
        rel = f'attachments/{h}/{apath.name}'
        rel_att.append(rel)

    for img in mail.inline_images:
        fname = _safe_filename(img.filename)
        ipath = mail_att_dir / fname
        n = 1
        while ipath.exists():
            stem, ext = os.path.splitext(fname)
            ipath = mail_att_dir / f'{stem}_{n}{ext}'
            n += 1
        ipath.write_bytes(img.payload)
        rel = f'attachments/{h}/{ipath.name}'
        rel_img.append(rel)

    # 构造 Markdown：先在 HTML 上替换 cid 引用，再转 MD
    from .mail_parser import _html_to_md
    body = ''
    if mail.body_html:
        html = mail.body_html
        for img, rel in zip(mail.inline_images, rel_img):
            if img.cid:
                html = html.replace(f'cid:{img.cid}', rel)
                html = html.replace(f'CID:{img.cid}', rel)
        # 清理失效的 cid 引用（转发邮件中 Foxmail 重写过 cid，部件里无对应数据）
        html = _UNRESOLVED_CID_RE.sub('', html)
        # 兜底：清除残留的 cid 字符串（alt 文案、表格文本中的引用等）
        html = _LEFTOVER_CID_RE.sub('', html)
        # 回写 body_html（cid 已替换为相对路径），供 SQLite/前端展示内嵌图
        mail.body_html = html
        body = _html_to_md(html)
    if not body:
        body = mail.body_md or mail.body_text or '(无正文)'
    # 回写 Mail 对象，供 SQLite/前端使用
    mail.body_md = body

    fm = _build_front_matter(mail, {'attachments': rel_att, 'inline_images': rel_img})

    content = f'{fm}\n\n# {mail.subject or "(无主题)"}\n\n'
    if mail.attachments:
        content += '## 附件\n\n'
        for att, rel in zip(mail.attachments, rel_att):
            content += f'- [{att.filename}]({rel}) ({_fmt_size(att.size)})\n'
        content += '\n'
    content += body
    md_path.write_text(content, encoding='utf-8')

    # 纯文本区（plaintext/）：带邮件头摘要，供 AI/RAG 按区取用
    txt_dir = out_dir / 'plaintext'
    txt_dir.mkdir(exist_ok=True)
    txt_path = txt_dir / txt_name
    head_lines = [
        f'主题: {mail.subject or "(无主题)"}',
        f'发件人: {mail.from_}',
        f'收件人: {mail.to}',
    ]
    if mail.cc:
        head_lines.append(f'抄送: {mail.cc}')
    head_lines.append(f'日期: {mail.date_iso or mail.date}')
    if mail.attachments:
        head_lines.append('附件: ' + '; '.join(a.filename for a in mail.attachments))
    txt_content = '\n'.join(head_lines) + '\n\n' + (mail.body_text or body or '(无正文)')
    txt_path.write_text(txt_content, encoding='utf-8')

    return {
        'index': mail.index,
        'message_id': mail.message_id,
        'subject': mail.subject,
        'from': mail.from_,
        'to': mail.to,
        'cc': mail.cc,
        'date': mail.date_iso or mail.date,
        'has_attachments': mail.has_attachments,
        'attachment_count': len(mail.attachments),
        'attachment_files': rel_att,
        'inline_files': rel_img,
        'md_file': md_name,
        'text_file': f'plaintext/{txt_name}',
        'source_offset': mail.offset,
        'raw_size': mail.raw_size,
        'parse_error': mail.parse_error,
    }


def _fmt_size(n: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}TB'


def write_manifest(manifest_path: Path, entries: list, source_file: str, total: int):
    """写入 manifest.json（供 RAG/AI 工具消费）"""
    data = {
        'source_file': source_file,
        'total_emails': total,
        'exported': len(entries),
        'emails': entries,
    }
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )
