# -*- coding: utf-8 -*-
"""核心引擎：.fox 解析 + MIME 解析 + 导出 + 存储"""
from .fox_parser import FoxArchive, count_emails, is_fox_file, iter_offsets
from .mail_parser import parse_mail, Mail, Attachment
from .exporter import export_mail, write_manifest
from .store import Store

__all__ = [
    'FoxArchive', 'count_emails', 'is_fox_file', 'iter_offsets',
    'parse_mail', 'Mail', 'Attachment',
    'export_mail', 'write_manifest', 'Store',
]
