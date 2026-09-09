# -*- coding: utf-8 -*-
"""MIME 邮件解析器：raw bytes → 结构化 Mail 对象"""
import email
import re
from email import policy
from email.utils import parsedate_to_datetime, parseaddr
from dataclasses import dataclass, field
from typing import List, Optional

from markdownify import markdownify as _md


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int
    cid: str = ''
    is_inline: bool = False
    payload: bytes = b''


@dataclass
class Mail:
    index: int
    offset: int
    raw_size: int
    message_id: str
    subject: str
    from_: str
    to: str
    cc: str
    bcc: str
    date: str
    date_iso: str
    body_text: str
    body_html: str
    body_md: str
    attachments: List[Attachment] = field(default_factory=list)
    inline_images: List[Attachment] = field(default_factory=list)
    has_attachments: bool = False
    parse_error: Optional[str] = None


def _hdr(value) -> str:
    """安全解码邮件头（policy.default 已自动处理 RFC2047 编码词）"""
    if value is None:
        return ''
    return str(value).strip()


def _addr(value) -> str:
    """提取规范化的地址显示"""
    if not value:
        return ''
    name, addr = parseaddr(value)
    if addr and name:
        return f'{name} <{addr}>'
    return addr or name or str(value)


def _strip_fox_trailer(payload: bytes) -> bytes:
    """去除 .fox 记录尾部 UTF-16LE 元数据对非 multipart 体的污染

    多部件邮件在 final boundary 处被解析器自然截断，不受影响；
    单部件邮件会把 trailer 一并当作正文。

    策略（按可靠性递减）：
    1. HTML 邮件：最后的 </html> 之后的全部截断（trailer 永远在 </html> 之后）
    2. 其它文本：首个 0x00 截断（trailer 的 ASCII 段必含 0x00，
       纯中文段无 0x00 但这种 text/plain trailer 较少见）
    """
    if not payload:
        return payload
    # HTML 截断：优先 </html>，否则最后的任意闭合标签（</table>/</div> 等）
    for pat in (rb'</html\s*>', rb'</[a-zA-Z][^>]*>'):
        matches = list(re.finditer(pat, payload, re.I))
        if matches:
            return payload[:matches[-1].end()]
    # 0x00 截断（纯文本邮件回退）
    z = payload.find(b'\x00')
    if z != -1:
        payload = payload[:z]
    return payload.rstrip(b'\r\n \t')


def _html_preclean(html: str) -> str:
    """转换前清理：完整文档头、style/script 块、HTML 注释（Outlook 邮件常含内嵌 CSS）"""
    import re
    html = re.sub(r'(?is)<!(?:DOCTYPE|doctype)[^>]*>', '', html)
    html = re.sub(r'(?is)<(script|style|head)[^>]*>.*?</\1\s*>', '', html)
    html = re.sub(r'(?is)<!--.*?-->', '', html)
    return html


def _html_to_text(html: str) -> str:
    """HTML → 纯文本（粗略，去标签）"""
    import re
    html = _html_preclean(html)
    txt = re.sub(r'(?is)<(script|style).*?</\1>', '', html)
    txt = re.sub(r'(?s)<[^>]+>', '', txt)
    txt = re.sub(r'&nbsp;', ' ', txt)
    txt = re.sub(r'&amp;', '&', txt)
    txt = re.sub(r'&lt;', '<', txt)
    txt = re.sub(r'&gt;', '>', txt)
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt.strip()


def _html_to_md(html: str) -> str:
    """HTML → Markdown"""
    html = _html_preclean(html)
    try:
        return _md(html, heading_style='ATX')
    except Exception:
        return _html_to_text(html)


def _normalize_charset(cs):
    """gb2312/gbk 归一为超集 gb18030（避免个别字符解码为 \ufffd）"""
    if cs:
        cs = cs.lower().strip()
        if cs in ('gb2312', 'gbk', 'gb_2312-80', 'csgb2312'):
            return 'gb18030'
    return cs or 'utf-8'


def _decode_part(part):
    """安全获取 part 的文本内容"""
    try:
        return part.get_content()
    except Exception:
        charset = _normalize_charset(part.get_content_charset())
        raw = part.get_payload(decode=True) or b''
        return raw.decode(charset, errors='replace')


# =?gbk? / =?gb2312? 编码词改写为 gb18030（超集，修复个别字符解码失败）
_CHARSET_FIX_RE = re.compile(rb'=\?gb(2312|k)\?', re.I)
# 多部件声明探测（头部区域）
_MULTIPART_RE = re.compile(rb'content-type:\s*multipart/', re.I)


def parse_mail(index: int, offset: int, raw_bytes: bytes) -> Mail:
    """解析单封 MIME bytes → Mail 对象"""
    raw_size = len(raw_bytes)
    parse_error = None

    # 非 multipart 邮件：截掉 .fox 尾部 UTF-16LE 元数据
    # （multipart 有 final boundary 自然挡住；单部件的 base64/QP 文本不含 0x00，
    #   而 trailer 必含 0x00——在原始切片上首个 0x00 处截断）
    if b'\x00' in raw_bytes and not _MULTIPART_RE.search(raw_bytes[:4096]):
        raw_bytes = raw_bytes[:raw_bytes.find(b'\x00')].rstrip(b'\r\n \t')

    # 编码词 charset 归一化：gbk/gb2312 → gb18030（个别字符仅 gb18030 可解）
    raw_bytes = _CHARSET_FIX_RE.sub(b'=?gb18030?', raw_bytes)

    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception as e:
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception as e2:
            return Mail(
                index=index, offset=offset, raw_size=raw_size,
                message_id='', subject='(解析失败)', from_='', to='', cc='', bcc='',
                date='', date_iso='', body_text='', body_html='', body_md='',
                parse_error=f'{type(e2).__name__}: {e2}',
            )

    message_id = _hdr(msg.get('Message-ID'))
    subject = _hdr(msg.get('Subject'))
    from_ = _addr(_hdr(msg.get('From')))
    to = _hdr(msg.get('To'))
    cc = _hdr(msg.get('Cc'))
    bcc = _hdr(msg.get('Bcc'))
    date = _hdr(msg.get('Date'))
    date_iso = ''
    if date:
        try:
            dt = parsedate_to_datetime(date)
            if dt:
                date_iso = dt.isoformat()
        except Exception:
            pass

    # 如果没有 Message-ID，用内容 hash 兜底
    if not message_id:
        import hashlib
        message_id = f'auto-{hashlib.md5(raw_bytes[:4096]).hexdigest()[:16]}'

    body_text = ''
    body_html = ''
    attachments: List[Attachment] = []
    inline_images: List[Attachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get('Content-Disposition', '')).lower()
            filename = part.get_filename()
            cid = _hdr(part.get('Content-ID')).strip('<>')
            payload = part.get_payload(decode=True) or b''

            # 1. 带 cid 的部件一律视为内嵌引用（Foxmail 常用 application/octet-stream 存内嵌图）
            if cid:
                inline_images.append(Attachment(
                    filename=filename or f'inline_{cid or len(inline_images)}',
                    content_type=ctype, size=len(payload), payload=payload,
                    cid=cid, is_inline=True,
                ))
            # 2. 普通附件（有文件名且非正文类型）
            elif part.is_attachment() or (filename and ctype not in ('text/plain', 'text/html')):
                if filename:
                    attachments.append(Attachment(
                        filename=filename, content_type=ctype,
                        size=len(payload), payload=payload, cid=cid,
                        is_inline=False,
                    ))
            # 3. 正文（手动解码以应用 charset 归一化）
            elif ctype == 'text/plain' and not body_text:
                cs = _normalize_charset(part.get_content_charset())
                raw = part.get_payload(decode=True) or b''
                body_text = raw.decode(cs, errors='replace')
            elif ctype == 'text/html' and not body_html:
                cs = _normalize_charset(part.get_content_charset())
                raw = part.get_payload(decode=True) or b''
                body_html = raw.decode(cs, errors='replace')
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True) or b''
        payload = _strip_fox_trailer(payload)
        charset = _normalize_charset(msg.get_content_charset())
        try:
            text = payload.decode(charset, errors='replace')
        except (LookupError, TypeError):
            text = payload.decode('utf-8', errors='replace')
        if ctype == 'text/html':
            body_html = text
        else:
            body_text = text

    if body_html and not body_text:
        body_text = _html_to_text(body_html)
    # body_md 在 exporter 中做 cid 替换后再转换（此处留空）

    return Mail(
        index=index, offset=offset, raw_size=raw_size,
        message_id=message_id, subject=subject, from_=from_, to=to, cc=cc, bcc=bcc,
        date=date, date_iso=date_iso, body_text=body_text, body_html=body_html,
        body_md='', attachments=attachments, inline_images=inline_images,
        has_attachments=len(attachments) > 0, parse_error=parse_error,
    )
