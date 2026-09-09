# -*- coding: utf-8 -*-
"""Foxmail .fox 存档解析器

.fox 格式（逆向确认，详见 docs/fox-format.md）:
  - 文件头: 魔数 'ARCF' (0x41524346) + 版本(4B) + GUID(16B) + 索引区
  - 邮件记录: 16 字节魔数 + 标准 RFC822 MIME 明文 (CRLF)
  - 邮件魔数: 10 10 10 10 10 10 10 11 11 11 11 11 11 53 0D 0A
  - 邮件间有 UTF-16LE 尾部元数据 (不影响 MIME 解析)
"""
import mmap
import os

# 每封邮件记录前的 16 字节魔数
MAGIC = b'\x10\x10\x10\x10\x10\x10\x10\x11\x11\x11\x11\x11\x11\x53\x0d\x0a'
# 文件头魔数
FILE_MAGIC = b'ARCF'


def is_fox_file(path):
    """快速判断是否为 .fox 存档文件"""
    try:
        with open(path, 'rb') as f:
            return f.read(4) == FILE_MAGIC
    except (OSError, IOError):
        return False


def count_emails(path):
    """快速统计 .fox 文件中的邮件数（只扫描魔数，不解析内容）"""
    n = 0
    pos = 0
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        while True:
            idx = mm.find(MAGIC, pos)
            if idx == -1:
                break
            n += 1
            pos = idx + len(MAGIC)
        mm.close()
    return n


def iter_offsets(path):
    """返回所有邮件魔数的文件偏移列表（用于进度/索引，不读内容）"""
    offs = []
    pos = 0
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        while True:
            idx = mm.find(MAGIC, pos)
            if idx == -1:
                break
            offs.append(idx)
            pos = idx + len(MAGIC)
        mm.close()
    return offs


class FoxArchive:
    """流式迭代 .fox 存档中的邮件

    用法::

        for index, offset, mime_bytes in FoxArchive(path):
            mail = parse_mail(index, offset, mime_bytes)

    内存友好：mmap 映射，逐封 yield，单封内存峰值 = 最大邮件块。
    """

    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)

    def __iter__(self):
        """yield (index, file_offset, mime_bytes)"""
        with open(self.path, 'rb') as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            size = len(mm)
            # 先收集所有魔数偏移
            magics = []
            pos = 0
            while True:
                idx = mm.find(MAGIC, pos)
                if idx == -1:
                    break
                magics.append(idx)
                pos = idx + len(MAGIC)
            total = len(magics)
            for i, start in enumerate(magics):
                content_start = start + len(MAGIC)
                content_end = magics[i + 1] if i + 1 < total else size
                raw = mm[content_start:content_end]
                yield i, start, raw
            mm.close()
