# -*- coding: utf-8 -*-
"""命令行入口：foxmail2md <input.fox|eml目录> -o <输出目录>

用法:
    python -m foxmail2md archive.fox -o ./output
    python -m foxmail2md archive.fox -o ./output --limit 20     # 测试前20封
    python -m foxmail2md archive.fox -o ./output --incremental  # 增量
"""
import argparse
import os
import sys
import time
import json
from pathlib import Path

from .core import FoxArchive, count_emails, parse_mail, export_mail, write_manifest
from .core.fox_parser import is_fox_file


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='foxmail2md',
        description='Foxmail .fox 存档 → Markdown + 附件 提取工具',
    )
    parser.add_argument('input', help='输入 .fox 文件路径')
    parser.add_argument('-o', '--output', required=True, help='输出目录')
    parser.add_argument('--limit', type=int, default=0, help='只处理前 N 封（测试用，0=全部）')
    parser.add_argument('--incremental', action='store_true', help='增量模式：跳过已处理邮件')
    parser.add_argument('--quiet', action='store_true', help='静默模式（不输出进度）')
    args = parser.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f'错误：输入文件不存在: {src}', file=sys.stderr)
        return 1
    if not is_fox_file(src):
        print(f'错误：不是 .fox 存档文件（缺少 ARCF 头）: {src}', file=sys.stderr)
        return 1

    out_dir = Path(args.output)
    att_dir = out_dir / 'attachments'
    out_dir.mkdir(parents=True, exist_ok=True)

    # 增量模式：加载已处理 Message-ID
    done_ids = set()
    state_path = out_dir / 'state.json'
    if args.incremental and state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        done_ids = set(state.get('processed_ids', []))
        if not args.quiet:
            print(f'[增量] 已处理 {len(done_ids)} 封，将跳过', file=sys.stderr)

    total = count_emails(src)
    if args.limit:
        total = min(total, args.limit)
    if not args.quiet:
        print(f'邮件总数: {total}', file=sys.stderr)

    entries = []
    ok = fail = 0
    t0 = time.time()
    last_report = t0

    archive = FoxArchive(src)
    for index, offset, raw in archive:
        if args.limit and index >= args.limit:
            break

        mail = parse_mail(index, offset, raw)

        # 增量去重
        if args.incremental and mail.message_id in done_ids:
            continue

        try:
            entry = export_mail(mail, out_dir, att_dir)
            entries.append(entry)
            done_ids.add(mail.message_id)
            ok += 1
        except Exception as e:
            fail += 1
            if not args.quiet:
                print(f'[#{index}] 导出失败: {e}', file=sys.stderr)

        # 进度
        now = time.time()
        if not args.quiet and (now - last_report >= 2.0 or index % 50 == 0):
            rate = (index + 1) / (now - t0) if now > t0 else 0
            print(f'[{index+1}/{total}] {rate:.1f}封/秒  {mail.subject[:40]}',
                  file=sys.stderr)
            last_report = now

    elapsed = time.time() - t0

    # manifest
    write_manifest(out_dir / 'manifest.json', entries, src.name, total)

    # state
    state_path.write_text(
        json.dumps({'processed_ids': sorted(done_ids), 'source': str(src)},
                   ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    if not args.quiet:
        print(f'\n=== 完成 ===', file=sys.stderr)
        print(f'成功导出: {ok}', file=sys.stderr)
        print(f'失败: {fail}', file=sys.stderr)
        print(f'耗时: {elapsed:.1f}s ({ok/elapsed:.1f}封/秒)' if elapsed > 0 else '', file=sys.stderr)
        print(f'输出目录: {out_dir}', file=sys.stderr)
        print(f'Manifest: {out_dir / "manifest.json"}', file=sys.stderr)

    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
