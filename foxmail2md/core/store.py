# -*- coding: utf-8 -*-
"""SQLite 邮件索引 + FTS5 trigram 正文搜索

性能设计：
- 线程本地持久连接：FastAPI 同步端点跑在Starlette线程池里，连接随线程复用，
  PRAGMA 只在建连时设置一次（旧实现每方法开关连接、每次重设 WAL 且从不关闭）。
- 事务显式控制：isolation_level=None + BEGIN IMMEDIATE，导入按 .fox 文件粒度
  批量提交（旧实现每封邮件一次连接+提交=一次 fsync）。
- 列表/分组查询只取头字段列，正文仅在详情与搜索路径读取（旧实现 SELECT *
  连列表页也拖三份正文列，分组视图一次拖 2000 封全文）。
- 正文搜索走 FTS5 trigram（external content + 触发器同步，支持中英文子串）；
  头字段始终 LIKE（全表头字段扫描毫秒级）；查询串 <3 字符或索引未就绪时
  整体回退 LIKE。
- 分组键（规范化主题 / 提取邮箱地址）在写入时预计算并存索引列，分组走
  SQL GROUP BY（旧实现前端取 2000 封在浏览器分组，超出部分不可见）。
"""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

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
    norm_subject  TEXT,         -- 分组键：剥 Re:/转发 前缀的主题
    from_key      TEXT,         -- 分组键：<> 内邮箱地址
    to_key        TEXT,         -- 分组键：<> 内邮箱地址
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_source ON emails(source_file);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_iso);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_addr);
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_archive ON emails(archive_id);
CREATE INDEX IF NOT EXISTS idx_emails_archive_date ON emails(archive_id, date_iso);
CREATE INDEX IF NOT EXISTS idx_archives_path ON archives(source_path);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    body_text, body_md,
    content='emails', content_rowid='id',
    tokenize='trigram'
);
"""

# 同步触发器只允许在 FTS 索引就绪后创建：
# 对"从未进过索引"的行执行 fts5 'delete' 命令会报 database disk image is malformed，
# 老库升级路径必须先全量重建索引，再挂触发器。
# 注意：触发器体内含分号，必须整体作为单条语句执行（不能按分号切分）。
FTS_TRIGGER_STMTS = [
    """CREATE TRIGGER IF NOT EXISTS emails_fts_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, body_text, body_md)
    VALUES (new.id, new.body_text, new.body_md);
END""",
    """CREATE TRIGGER IF NOT EXISTS emails_fts_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, body_text, body_md)
    VALUES ('delete', old.id, old.body_text, old.body_md);
END""",
    """CREATE TRIGGER IF NOT EXISTS emails_fts_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, body_text, body_md)
    VALUES ('delete', old.id, old.body_text, old.body_md);
    INSERT INTO emails_fts(rowid, body_text, body_md)
    VALUES (new.id, new.body_text, new.body_md);
END""",
]

INSERT_SQL = """
INSERT INTO emails (
    archive_id,
    source_file, mail_index, message_id, subject, from_addr, to_addr, cc_addr,
    date_iso, date_text, has_attachments, attachment_count, attachment_files,
    attachment_names, body_text, body_html, body_md, md_file, source_offset,
    raw_size, parse_error, norm_subject, from_key, to_key
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# 列表/分组等只需要头字段的场合使用，避免拖出三份正文列
LIST_COLS = (
    'id, mail_index, subject, from_addr, date_iso, date_text, '
    'has_attachments, attachment_count'
)
# 查找清理需要 md_file/attachment_files/archive_id，但同样不需要正文
CLEAN_COLS = (
    'id, archive_id, subject, from_addr, date_iso, date_text, raw_size, '
    'attachment_count, md_file, attachment_files'
)

GROUP_COL = {'subject': 'norm_subject', 'from': 'from_key', 'to': 'to_key'}

# 与前端 normSubject/addrKey 逻辑保持一致（剥 Re:/转发 前缀与【】标签、提取地址）
_RE_PREFIX = re.compile(r'^\s*((re|fw|fwd|aw|sv)\s*[:：]\s*)', re.I)
_CN_PREFIX = re.compile(r'^\s*(回复|转发|答复|转送)\s*[:：]\s*')
_TAG_PREFIX = re.compile(r'^\s*【[^】]{1,12}】\s*')
_ADDR_RE = re.compile(r'<([^>]+)>')


def norm_subject_key(subject: str) -> str:
    out = str(subject or '').strip()
    for _ in range(20):
        nxt = _RE_PREFIX.sub('', out, count=1)
        nxt = _CN_PREFIX.sub('', nxt, count=1)
        nxt = _TAG_PREFIX.sub('', nxt, count=1)
        if nxt == out:
            break
        out = nxt
    return out.strip() or '(no subject)'


def addr_key(addr: str) -> str:
    s = str(addr or '')
    m = _ADDR_RE.search(s)
    return (m.group(1) if m else s).strip() or '(empty)'


class Store:
    """SQLite 邮件存储（线程本地持久连接 + 显式事务）"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns = set()
        self._conns_lock = threading.Lock()
        self._init_schema()

    # ── 连接与事务 ──
    def _conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path), timeout=30,
                isolation_level=None,   # autocommit；事务由 transaction() 显式管理
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=10000')
            conn.execute('PRAGMA cache_size=-16000')
            conn.execute('PRAGMA mmap_size=268435456')
            conn.execute('PRAGMA temp_store=MEMORY')
            self._local.conn = conn
            with self._conns_lock:
                self._conns.add(conn)
        return conn

    @contextmanager
    def transaction(self):
        """显式事务；BEGIN IMMEDIATE 在与其他写者（导入/索引构建）竞争时重试"""
        conn = self._conn()
        deadline = time.time() + 30
        while True:
            try:
                conn.execute('BEGIN IMMEDIATE')
                break
            except sqlite3.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)
        try:
            yield conn
        except Exception:
            conn.execute('ROLLBACK')
            raise
        else:
            conn.execute('COMMIT')

    def close(self):
        """关闭全部连接（迁移换库时调用；正在被其他线程使用的连接交给 GC）"""
        with self._conns_lock:
            conns, self._conns = list(self._conns), set()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # ── 元信息 ──
    def meta_get(self, key: str) -> Optional[str]:
        r = self._conn().execute(
            'SELECT value FROM schema_meta WHERE key = ?', (key,)).fetchone()
        return r['value'] if r else None

    def meta_set(self, key: str, value: str):
        self._conn().execute(
            'INSERT INTO schema_meta (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))

    def _init_schema(self):
        conn = self._conn()
        conn.executescript(SCHEMA)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(emails)').fetchall()]
        if 'archive_id' not in cols:
            conn.execute('ALTER TABLE emails ADD COLUMN archive_id INTEGER DEFAULT 0')
            with self.transaction():
                for r in conn.execute(
                    'SELECT DISTINCT source_file FROM emails WHERE archive_id = 0'
                ).fetchall():
                    src = r['source_file']
                    cur = conn.execute(
                        'INSERT INTO archives (name, source_name, source_path, email_total)'
                        ' VALUES (?, ?, NULL, (SELECT COUNT(*) FROM emails WHERE source_file = ?))',
                        (src, src, src),
                    )
                    conn.execute('UPDATE emails SET archive_id = ? WHERE source_file = ?',
                                 (cur.lastrowid, src))
        # 老库补列（列名硬编码，逐一显式执行；避免 f-string 拼 DDL 触发扫描告警）
        if 'norm_subject' not in cols:
            conn.execute('ALTER TABLE emails ADD COLUMN norm_subject TEXT')
        if 'from_key' not in cols:
            conn.execute('ALTER TABLE emails ADD COLUMN from_key TEXT')
        if 'to_key' not in cols:
            conn.execute('ALTER TABLE emails ADD COLUMN to_key TEXT')
        # 分组键索引（列可能在上面刚补，须在 ALTER 之后创建）
        conn.execute('CREATE INDEX IF NOT EXISTS idx_emails_norm_subject ON emails(norm_subject)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_emails_from_key ON emails(from_key)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_emails_to_key ON emails(to_key)')
        # FTS 就绪状态：纯 meta 状态机（external content 表的 COUNT(*) 会镜像
        # 内容表行数，不能用行数比对判断索引是否与内容同步）。
        # 首次见到新库：空库视为就绪（导入走触发器）；非空即老库，待后台重建。
        state = self.meta_get('fts_ready')
        if state is None:
            n = conn.execute('SELECT COUNT(*) FROM emails').fetchone()[0]
            state = '1' if n == 0 else '0'
            self.meta_set('fts_ready', state)
        if state == '1':
            self._ensure_fts_triggers()
        else:
            self._drop_fts_triggers()

    def _ensure_fts_triggers(self):
        """逐条 execute 创建触发器（executescript 会隐式 COMMIT，不能进显式事务）"""
        for stmt in FTS_TRIGGER_STMTS:
            self._conn().execute(stmt)

    def _drop_fts_triggers(self):
        conn = self._conn()
        conn.execute('DROP TRIGGER IF EXISTS emails_fts_ai')
        conn.execute('DROP TRIGGER IF EXISTS emails_fts_ad')
        conn.execute('DROP TRIGGER IF EXISTS emails_fts_au')

    def fts_ready(self) -> bool:
        return self.meta_get('fts_ready') == '1'

    def email_count(self) -> int:
        return self._conn().execute('SELECT COUNT(*) FROM emails').fetchone()[0]

    # ── FTS 构建（老库升级时由后台线程分块调用） ──
    def fts_build_begin(self):
        with self.transaction():
            self._drop_fts_triggers()
            self._conn().execute("INSERT INTO emails_fts(emails_fts) VALUES('delete-all')")
            self.meta_set('fts_last_id', '0')
            self.meta_set('fts_ready', '0')

    def fts_build_chunk(self, batch: int = 300):
        """构建一批，返回 (是否完成, 已处理到的最大 id)"""
        conn = self._conn()
        last = int(self.meta_get('fts_last_id') or 0)
        top_r = conn.execute(
            'SELECT MAX(id) AS top FROM (SELECT id FROM emails WHERE id > ? '
            'ORDER BY id LIMIT ?)', (last, batch)).fetchone()
        top = top_r['top'] if top_r and top_r['top'] is not None else None
        if top is None:
            return True, last
        with self.transaction():
            conn.execute(
                'INSERT INTO emails_fts(rowid, body_text, body_md) '
                'SELECT id, body_text, body_md FROM emails WHERE id > ? AND id <= ?',
                (last, top))
            self.meta_set('fts_last_id', str(top))
        return False, top

    def fts_build_finish(self):
        # 建触发器与置就绪同一事务：任一时刻"就绪"都意味着删除/更新有触发器同步
        with self.transaction():
            self._ensure_fts_triggers()
            self.meta_set('fts_ready', '1')
            self._conn().execute("DELETE FROM schema_meta WHERE key = 'fts_last_id'")

    def backfill_norm_keys(self, batch: int = 500) -> int:
        """为老库回填分组键列，返回本批处理行数（0 表示完成）"""
        conn = self._conn()
        rows = conn.execute(
            'SELECT id, subject, from_addr, to_addr FROM emails '
            'WHERE norm_subject IS NULL LIMIT ?', (batch,)).fetchall()
        if not rows:
            return 0
        payload = [
            (norm_subject_key(r['subject']), addr_key(r['from_addr']),
             addr_key(r['to_addr']), r['id'])
            for r in rows
        ]
        with self.transaction():
            conn.executemany(
                'UPDATE emails SET norm_subject=?, from_key=?, to_key=? WHERE id=?',
                payload)
        return len(rows)

    # ── 搜索条件路由 ──
    def _search_cond(self, search: str):
        """返回 (条件 SQL, 参数)。头字段 LIKE + 正文 FTS（≥3 字符且索引就绪）。"""
        s = (search or '').strip()
        if not s:
            return '', []
        like = f'%{s}%'
        header = '(subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ?)'
        args = [like, like, like]
        if len(s) >= 3 and self.fts_ready():
            match = '"' + s.replace('"', '""') + '"'
            cond = f'({header} OR id IN (SELECT rowid FROM emails_fts WHERE emails_fts MATCH ?))'
            args.append(match)
        else:
            cond = (f'({header} OR body_text LIKE ? OR body_md LIKE ?)')
            args.extend([like, like])
        return cond, args

    # ── 存档管理 ──
    def insert_archive(self, name, source_name, source_path, total_size, export_dir):
        """登记一个存档；同 source_path 已存在则复用（更新信息）"""
        with self.transaction():
            row = self._conn().execute(
                'SELECT id FROM archives WHERE source_path = ?', (source_path,)
            ).fetchone()
            if row:
                self._conn().execute(
                    'UPDATE archives SET name=?, total_size=?, export_dir=? WHERE id=?',
                    (name, total_size, export_dir, row['id']),
                )
                return row['id']
            cur = self._conn().execute(
                'INSERT INTO archives (name, source_name, source_path, total_size, export_dir)'
                ' VALUES (?, ?, ?, ?, ?)',
                (name, source_name, source_path, total_size, export_dir),
            )
            return cur.lastrowid

    def update_archive_counts(self, archive_id, email_total, skipped):
        with self.transaction():
            self._conn().execute('UPDATE archives SET email_total=?, skipped=? WHERE id=?',
                                 (email_total, skipped, archive_id))

    def list_archives(self):
        rows = self._conn().execute('''
            SELECT a.*,
                   (SELECT COUNT(*) FROM emails e WHERE e.archive_id = a.id) AS email_count
            FROM archives a ORDER BY a.created_at DESC, a.id DESC
        ''').fetchall()
        return [dict(r) for r in rows]

    def get_archive(self, archive_id):
        r = self._conn().execute('SELECT * FROM archives WHERE id = ?',
                                 (archive_id,)).fetchone()
        return dict(r) if r else None

    def get_mails_by_ids(self, ids):
        """按 id 列表取邮件的文件登记信息（保序；含所属存档导出目录）"""
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        rows = self._conn().execute(
            f'SELECT e.id, e.md_file, e.attachment_files, e.archive_id, '
            f'a.export_dir FROM emails e LEFT JOIN archives a ON a.id = e.archive_id '
            f'WHERE e.id IN ({marks})', list(ids)).fetchall()
        by_id = {}
        for r in rows:
            d = dict(r)
            d['attachment_files'] = json.loads(d['attachment_files'] or '[]')
            by_id[r['id']] = d
        return [by_id[i] for i in ids if i in by_id]

    def delete_mails(self, ids):
        """按 id 列表删除邮件记录（FTS 由触发器同步）"""
        if not ids:
            return 0
        marks = ','.join('?' * len(ids))
        with self.transaction():
            cur = self._conn().execute(f'DELETE FROM emails WHERE id IN ({marks})', list(ids))
            return cur.rowcount

    def find_mails(self, archive_id=0, sender='', subject='',
                   date_from='', date_to='', min_mb=None, max_mb=None, limit=500):
        """按条件筛选邮件（条件之间 AND；头字段匹配，不搜正文；大小按源邮件体积，单位 MB）"""
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
        rows = self._conn().execute(
            f'SELECT {CLEAN_COLS} FROM emails WHERE {where} '
            'ORDER BY date_iso DESC LIMIT ?',
            (*args, limit),
        ).fetchall()
        return [self._row_to_cleanup_row(r) for r in rows]

    def get_archive_by_path(self, source_path):
        r = self._conn().execute('SELECT * FROM archives WHERE source_path = ?',
                                 (source_path,)).fetchone()
        return dict(r) if r else None

    def delete_archive(self, archive_id):
        """删除存档：先返回其邮件的导出文件所在目录集合，再清数据"""
        arc = self._conn().execute('SELECT export_dir FROM archives WHERE id = ?',
                                   (archive_id,)).fetchone()
        with self.transaction():
            self._conn().execute('DELETE FROM emails WHERE archive_id = ?', (archive_id,))
            self._conn().execute('DELETE FROM archives WHERE id = ?', (archive_id,))
        return arc['export_dir'] if arc else None

    def known_message_ids(self):
        """全局 Message-ID 集合（跨存档去重用）"""
        rows = self._conn().execute(
            "SELECT message_id FROM emails WHERE message_id LIKE '<%'"
        ).fetchall()
        return {r['message_id'] for r in rows}

    def clear_source(self, source_file: str):
        """清除某源文件的所有已索引邮件（重新解析前调用；FTS 由触发器同步）"""
        with self.transaction():
            self._conn().execute('DELETE FROM emails WHERE source_file = ?', (source_file,))

    def insert_mail(self, source_file: str, mail, rel_attachments: list, md_file: str,
                    archive_id: int = 0):
        """插入一封邮件（mail 为 Mail 对象）。事务由调用方（导入按文件粒度）管理。"""
        att_names = [a.filename for a in mail.attachments]
        att_files = rel_attachments
        cur = self._conn().execute(INSERT_SQL, (
            archive_id, source_file, mail.index, mail.message_id, mail.subject,
            mail.from_, mail.to, mail.cc, mail.date_iso, mail.date,
            int(mail.has_attachments), len(mail.attachments),
            json.dumps(att_files, ensure_ascii=False),
            json.dumps(att_names, ensure_ascii=False),
            mail.body_text, mail.body_html, mail.body_md, md_file,
            mail.offset, mail.raw_size, mail.parse_error,
            norm_subject_key(mail.subject), addr_key(mail.from_), addr_key(mail.to),
        ))
        return cur.lastrowid

    # ── 列表 / 搜索 / 分组 ──
    def _scope_cond(self, source_file: str, archive_id: int):
        if archive_id:
            return 'archive_id = ?', [archive_id]
        if source_file:
            return 'source_file = ?', [source_file]
        return '1=1', []

    def list_mails(self, source_file: str = '', archive_id: int = 0, page: int = 1,
                   per_page: int = 50, search: str = '', order: str = 'date_desc',
                   group: str = '') -> dict:
        """分页列表 / 分组头列表；archive_id>0 时按存档过滤"""
        if group in GROUP_COL:
            return self._list_groups(source_file, archive_id, search, group)
        offset = (page - 1) * per_page
        order_clause = {
            'date_desc': 'date_iso DESC',
            'date_asc': 'date_iso ASC',
            'subject': 'subject',
        }.get(order, 'date_iso DESC')
        scope, sargs = self._scope_cond(source_file, archive_id)
        search_cond, cargs = self._search_cond(search)
        where = f'{scope} AND {search_cond}' if search_cond else scope
        args = (*sargs, *cargs)
        conn = self._conn()
        count_row = conn.execute(
            f'SELECT COUNT(*) FROM emails WHERE {where}', args).fetchone()
        total = count_row[0]
        rows = conn.execute(
            f'SELECT {LIST_COLS} FROM emails WHERE {where} '
            f'ORDER BY {order_clause} LIMIT ? OFFSET ?',
            (*args, per_page, offset),
        ).fetchall()
        return {
            'total': total,
            'page': page,
            'per_page': per_page,
            'items': [self._row_to_list_item(r) for r in rows],
        }

    def _list_groups(self, source_file: str, archive_id: int, search: str,
                     group: str) -> dict:
        gcol = GROUP_COL[group]
        gexpr = f'COALESCE({gcol}, \'\')'
        scope, sargs = self._scope_cond(source_file, archive_id)
        search_cond, cargs = self._search_cond(search)
        where = f'{scope} AND {search_cond}' if search_cond else scope
        rows = self._conn().execute(
            f'SELECT {gexpr} AS key, COUNT(*) AS n FROM emails WHERE {where} '
            f'GROUP BY {gexpr} ORDER BY n DESC, key ASC',
            (*sargs, *cargs),
        ).fetchall()
        return {
            'total': sum(r['n'] for r in rows),
            'group': group,
            'groups': [{'key': r['key'], 'count': r['n']} for r in rows],
        }

    def list_group_members(self, group: str, key: str, source_file: str = '',
                           archive_id: int = 0, search: str = '', page: int = 1,
                           per_page: int = 2000, order: str = 'date_desc') -> dict:
        """拉取某个分组内的邮件（头字段），供点开分组时加载"""
        gcol = GROUP_COL.get(group)
        if not gcol:
            raise ValueError(f'未知分组字段: {group}')
        gexpr = f'COALESCE({gcol}, \'\')'
        offset = (page - 1) * per_page
        order_clause = {
            'date_desc': 'date_iso DESC',
            'date_asc': 'date_iso ASC',
            'subject': 'subject',
        }.get(order, 'date_iso DESC')
        scope, sargs = self._scope_cond(source_file, archive_id)
        search_cond, cargs = self._search_cond(search)
        where = f'{scope} AND {search_cond} AND {gexpr} = ?' if search_cond \
            else f'{scope} AND {gexpr} = ?'
        args = (*sargs, *cargs, key)
        conn = self._conn()
        total = conn.execute(
            f'SELECT COUNT(*) FROM emails WHERE {where}', args).fetchone()[0]
        rows = conn.execute(
            f'SELECT {LIST_COLS} FROM emails WHERE {where} '
            f'ORDER BY {order_clause} LIMIT ? OFFSET ?',
            (*args, per_page, offset),
        ).fetchall()
        return {
            'total': total,
            'page': page,
            'per_page': per_page,
            'items': [self._row_to_list_item(r) for r in rows],
        }

    def get_mail(self, mail_id: int) -> Optional[dict]:
        """获取单封邮件详情（含正文）"""
        r = self._conn().execute('SELECT * FROM emails WHERE id = ?', (mail_id,)).fetchone()
        return self._row_to_detail(r) if r else None

    def get_mail_attachment_view(self, mail_id: int) -> Optional[dict]:
        """附件/下载专用轻量查询：不读正文列，直取所属存档导出目录"""
        r = self._conn().execute(
            'SELECT e.id, e.subject, e.attachment_files, e.attachment_names, '
            'e.md_file, a.export_dir FROM emails e '
            'LEFT JOIN archives a ON a.id = e.archive_id '
            'WHERE e.id = ?', (mail_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d['attachment_files'] = json.loads(d['attachment_files'] or '[]')
        d['attachment_names'] = json.loads(d['attachment_names'] or '[]')
        return d

    def stats(self, source_file: str = '', archive_id: int = 0) -> dict:
        """统计信息；archive_id>0 时按存档过滤"""
        scope, sargs = self._scope_cond(source_file, archive_id)
        conn = self._conn()
        r = conn.execute(
            f'SELECT COUNT(*) as total, '
            'SUM(has_attachments) as with_att, '
            'SUM(attachment_count) as att_count, '
            'SUM(raw_size) as total_size '
            f'FROM emails WHERE {scope}', sargs
        ).fetchone()
        date_r = conn.execute(
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
        rows = self._conn().execute(
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
    def _row_to_cleanup_row(r) -> dict:
        return {
            'id': r['id'],
            'archive_id': r['archive_id'],
            'subject': r['subject'] or '(无主题)',
            'from': r['from_addr'] or '',
            'date': r['date_iso'] or r['date_text'] or '',
            'raw_size': r['raw_size'] or 0,
            'attachment_count': r['attachment_count'],
            'md_file': r['md_file'] or '',
            'attachment_files': json.loads(r['attachment_files'] or '[]'),
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
