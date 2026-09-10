# -*- coding: utf-8 -*-
"""SQLite 邮件索引 + FTS5 全文搜索

供 Web UI 快速查询；解析 .fox 时写入，列表/搜索/详情均走 SQLite。
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    source_path   TEXT,
    email_total   INTEGER DEFAULT 0,
    skipped       INTEGER DEFAULT 0,
    total_size    INTEGER DEFAULT 0,
    export_dir    TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS emails (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id    INTEGER DEFAULT 0,
    source_file   TEXT NOT NULL,
    mail_index    INTEGER NOT NULL,
    message_id    TEXT,
    subject       TEXT,
    from_addr     TEXT,
    to_addr       TEXT,
    cc_addr       TEXT,
    date_iso      TEXT,
    date_text     TEXT,
    has_attachments   INTEGER DEFAULT 0,
    attachment_count  INTEGER DEFAULT 0,
    attachment_files  TEXT,    -- JSON array of relative paths
    attachment_names  TEXT,    -- JSON array of filenames
    body_text     TEXT,
    body_html     TEXT,
    body_md       TEXT,
    md_file       TEXT,
    source_offset INTEGER,
    raw_size      INTEGER,
    parse_error   TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_emails_source ON emails(source_file);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_iso);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_addr);
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_archives_path ON archives(source_path);
"""

INSERT_SQL = """
INSERT INTO emails (
    archive_id,
    source_file, mail_index, message_id, subject, from_addr, to_addr, cc_addr,
    date_iso, date_text, has_attachments, attachment_count, attachment_files,
    attachment_names, body_text, body_html, body_md, md_file, source_offset,
    raw_size, parse_error
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

FTS_INSERT_SQL = """
INSERT INTO emails_fts (rowid, subject, from_addr, to_addr, body_text, body_md)
VALUES (?, ?, ?, ?, ?, ?)
"""


class Store:
    """SQLite 邮件存储"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA)
            # 旧库迁移：补 archive_id 列并为已有源文件建立存档记录
            cols = [r[1] for r in c.execute('PRAGMA table_info(emails)').fetchall()]
            if 'archive_id' not in cols:
                c.execute('ALTER TABLE emails ADD COLUMN archive_id INTEGER DEFAULT 0')
                for r in c.execute(
                    'SELECT DISTINCT source_file FROM emails WHERE archive_id = 0'
                ).fetchall():
                    src = r['source_file']
                    cur = c.execute(
                        'INSERT INTO archives (name, source_name, source_path, email_total)'
                        ' VALUES (?, ?, NULL, (SELECT COUNT(*) FROM emails WHERE source_file = ?))',
                        (src, src, src),
                    )
                    c.execute('UPDATE emails SET archive_id = ? WHERE source_file = ?',
                              (cur.lastrowid, src))
            c.execute('CREATE INDEX IF NOT EXISTS idx_emails_archive ON emails(archive_id)')

    # ── 存档管理 ──
    def insert_archive(self, name, source_name, source_path, total_size, export_dir):
        """登记一个存档；同 source_path 已存在则复用（更新信息）"""
        with self._conn() as c:
            row = c.execute(
                'SELECT id FROM archives WHERE source_path = ?', (source_path,)
            ).fetchone()
            if row:
                c.execute(
                    'UPDATE archives SET name=?, total_size=?, export_dir=? WHERE id=?',
                    (name, total_size, export_dir, row['id']),
                )
                return row['id']
            cur = c.execute(
                'INSERT INTO archives (name, source_name, source_path, total_size, export_dir)'
                ' VALUES (?, ?, ?, ?, ?)',
                (name, source_name, source_path, total_size, export_dir),
            )
            return cur.lastrowid

    def update_archive_counts(self, archive_id, email_total, skipped):
        with self._conn() as c:
            c.execute('UPDATE archives SET email_total=?, skipped=? WHERE id=?',
                      (email_total, skipped, archive_id))

    def list_archives(self):
        with self._conn() as c:
            rows = c.execute('''
                SELECT a.*,
                       (SELECT COUNT(*) FROM emails e WHERE e.archive_id = a.id) AS email_count
                FROM archives a ORDER BY a.created_at DESC, a.id DESC
            ''').fetchall()
        return [dict(r) for r in rows]

    def get_archive(self, archive_id):
        with self._conn() as c:
            r = c.execute('SELECT * FROM archives WHERE id = ?', (archive_id,)).fetchone()
        return dict(r) if r else None

    def get_mails_by_ids(self, ids):
        """按 id 列表取邮件（保序，供批量删除/统计）"""
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        with self._conn() as c:
            rows = c.execute(
                f'SELECT * FROM emails WHERE id IN ({marks})', list(ids)
            ).fetchall()
        by_id = {r['id']: dict(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def delete_mails(self, ids):
        """按 id 列表删除邮件记录"""
        if not ids:
            return 0
        marks = ','.join('?' * len(ids))
        with self._conn() as c:
            cur = c.execute(f'DELETE FROM emails WHERE id IN ({marks})', list(ids))
            return cur.rowcount

    def find_mails(self, archive_id=0, sender='', subject='',
                   date_from='', date_to='', min_mb=None, max_mb=None, limit=500):
        """按条件筛选邮件（条件之间 AND；大小按源邮件体积，单位 MB）"""
        conds, args = [], []
        if archive_id:
            conds.append('archive_id = ?')
            args.append(archive_id)
        if sender:
            conds.append('from_addr LIKE ?')
            args.append(f'%{sender}%')
        if subject:
            conds.append('subject LIKE ?')
            args.append(f'%{subject}%')
        if date_from:
            conds.append('date_iso >= ?')
            args.append(date_from)
        if date_to:
            conds.append('date_iso <= ?')
            args.append(date_to + 'T23:59:59' if len(date_to) == 10 else date_to)
        if min_mb is not None:
            conds.append('raw_size >= ?')
            args.append(int(min_mb * 1024 * 1024))
        if max_mb is not None:
            conds.append('raw_size <= ?')
            args.append(int(max_mb * 1024 * 1024))
        where = ' AND '.join(conds) if conds else '1=1'
        with self._conn() as c:
            rows = c.execute(
                f'SELECT * FROM emails WHERE {where} '
                'ORDER BY date_iso DESC LIMIT ?',
                (*args, limit),
            ).fetchall()
        return [self._row_to_detail(r) for r in rows]

    def get_archive_by_path(self, source_path):
        with self._conn() as c:
            r = c.execute('SELECT * FROM archives WHERE source_path = ?',
                          (source_path,)).fetchone()
        return dict(r) if r else None

    def delete_archive(self, archive_id):
        """删除存档：先返回其邮件的导出文件所在目录集合，再清数据"""
        with self._conn() as c:
            arc = c.execute('SELECT export_dir FROM archives WHERE id = ?',
                            (archive_id,)).fetchone()
            c.execute('DELETE FROM emails WHERE archive_id = ?', (archive_id,))
            c.execute('DELETE FROM archives WHERE id = ?', (archive_id,))
        return arc['export_dir'] if arc else None

    def known_message_ids(self):
        """全局 Message-ID 集合（跨存档去重用）"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT message_id FROM emails WHERE message_id LIKE '<%'"
            ).fetchall()
        return {r['message_id'] for r in rows}

    def clear_source(self, source_file: str):
        """清除某源文件的所有已索引邮件（重新解析前调用）"""
        with self._conn() as c:
            c.execute('DELETE FROM emails WHERE source_file = ?', (source_file,))
            c.execute('DELETE FROM emails_fts')

    def insert_mail(self, source_file: str, mail, rel_attachments: list, md_file: str,
                    archive_id: int = 0):
        """插入一封邮件（mail 为 Mail 对象）"""
        att_names = [a.filename for a in mail.attachments]
        att_files = rel_attachments
        with self._conn() as c:
            cur = c.execute(INSERT_SQL, (
                archive_id, source_file, mail.index, mail.message_id, mail.subject,
                mail.from_, mail.to, mail.cc, mail.date_iso, mail.date,
                int(mail.has_attachments), len(mail.attachments),
                json.dumps(att_files, ensure_ascii=False),
                json.dumps(att_names, ensure_ascii=False),
                mail.body_text, mail.body_html, mail.body_md, md_file,
                mail.offset, mail.raw_size, mail.parse_error,
            ))
            return cur.lastrowid

    def list_mails(self, source_file: str = '', archive_id: int = 0, page: int = 1,
                   per_page: int = 50, search: str = '', order: str = 'date_desc') -> dict:
        """分页列表 + 搜索；archive_id>0 时按存档过滤"""
        offset = (page - 1) * per_page
        order_clause = {
            'date_desc': 'date_iso DESC',
            'date_asc': 'date_iso ASC',
            'subject': 'subject',
        }.get(order, 'date_iso DESC')
        scope, sargs = '1=1', []
        if archive_id:
            scope, sargs = 'archive_id = ?', [archive_id]
        elif source_file:
            scope, sargs = 'source_file = ?', [source_file]

        with self._conn() as c:
            if search:
                # LIKE 匹配：FTS5 unicode61 无法切分 CJK 文本，中文搜索必须用 LIKE
                like = f'%{search}%'
                cond = (f'({scope}) AND (subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ? '
                        'OR body_text LIKE ? OR body_md LIKE ?)')
                args = (*sargs, like, like, like, like, like)
                count_row = c.execute(
                    f'SELECT COUNT(*) FROM emails WHERE {cond}', args
                ).fetchone()
                total = count_row[0]
                rows = c.execute(
                    f'SELECT * FROM emails WHERE {cond} '
                    f'ORDER BY {order_clause} LIMIT ? OFFSET ?',
                    (*args, per_page, offset),
                ).fetchall()
            else:
                count_row = c.execute(
                    f'SELECT COUNT(*) FROM emails WHERE {scope}', sargs
                ).fetchone()
                total = count_row[0]
                rows = c.execute(
                    f'SELECT * FROM emails WHERE {scope} '
                    f'ORDER BY {order_clause} LIMIT ? OFFSET ?',
                    (*sargs, per_page, offset),
                ).fetchall()

        return {
            'total': total,
            'page': page,
            'per_page': per_page,
            'items': [self._row_to_list_item(r) for r in rows],
        }

    def get_mail(self, mail_id: int) -> Optional[dict]:
        """获取单封邮件详情"""
        with self._conn() as c:
            r = c.execute('SELECT * FROM emails WHERE id = ?', (mail_id,)).fetchone()
        return self._row_to_detail(r) if r else None

    def stats(self, source_file: str = '', archive_id: int = 0) -> dict:
        """统计信息；archive_id>0 时按存档过滤"""
        scope, sargs = '1=1', []
        if archive_id:
            scope, sargs = 'archive_id = ?', [archive_id]
        elif source_file:
            scope, sargs = 'source_file = ?', [source_file]
        with self._conn() as c:
            r = c.execute(
                f'SELECT COUNT(*) as total, '
                'SUM(has_attachments) as with_att, '
                'SUM(attachment_count) as att_count, '
                'SUM(raw_size) as total_size '
                f'FROM emails WHERE {scope}', sargs
            ).fetchone()
            date_r = c.execute(
                f'SELECT MIN(date_iso) as min_date, MAX(date_iso) as max_date '
                f'FROM emails WHERE {scope}', sargs
            ).fetchone()
        return {
            'total': r['total'] or 0,
            'with_attachments': r['with_att'] or 0,
            'attachment_count': r['att_count'] or 0,
            'total_size': r['total_size'] or 0,
            'min_date': date_r['min_date'],
            'max_date': date_r['max_date'],
        }

    def list_sources(self) -> list:
        """已解析的源文件列表"""
        with self._conn() as c:
            rows = c.execute(
                'SELECT DISTINCT source_file FROM emails ORDER BY created_at DESC'
            ).fetchall()
        return [r['source_file'] for r in rows]

    @staticmethod
    def _row_to_list_item(r) -> dict:
        return {
            'id': r['id'],
            'index': r['mail_index'],
            'subject': r['subject'] or '(无主题)',
            'from': r['from_addr'] or '',
            'date': r['date_iso'] or r['date_text'] or '',
            'has_attachments': bool(r['has_attachments']),
            'attachment_count': r['attachment_count'],
        }

    @staticmethod
    def _row_to_detail(r) -> dict:
        return {
            'id': r['id'],
            'index': r['mail_index'],
            'message_id': r['message_id'],
            'subject': r['subject'] or '(无主题)',
            'from': r['from_addr'] or '',
            'to': r['to_addr'] or '',
            'cc': r['cc_addr'] or '',
            'date': r['date_iso'] or r['date_text'] or '',
            'has_attachments': bool(r['has_attachments']),
            'attachment_count': r['attachment_count'],
            'attachment_files': json.loads(r['attachment_files'] or '[]'),
            'attachment_names': json.loads(r['attachment_names'] or '[]'),
            'body_text': r['body_text'] or '',
            'body_html': r['body_html'] or '',
            'body_md': r['body_md'] or '',
            'md_file': r['md_file'] or '',
            'raw_size': r['raw_size'] or 0,
            'parse_error': r['parse_error'],
        }
