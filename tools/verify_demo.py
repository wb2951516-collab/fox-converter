# -*- coding: utf-8 -*-
"""验证 demo.fox 可被解析"""
import sys
sys.path.insert(0, '.')
from foxmail2md.core.fox_parser import count_emails, FoxArchive
from foxmail2md.core.mail_parser import parse_mail

n = count_emails('examples/demo.fox')
print('解析邮件数:', n)
for i, off, raw in FoxArchive('examples/demo.fox'):
    m = parse_mail(i, off, raw)
    print(f'  #{i} {m.subject} | {m.from_} | 附件 {len(m.attachments)}')
    if i == 0:
        print('  正文预览:', (m.body_md or '')[:60].replace('\n', ' '))
