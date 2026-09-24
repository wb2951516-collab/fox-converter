# -*- coding: utf-8 -*-
"""性能与正确性验证：在真实库副本上对比新旧搜索路径，并验证 FTS 构建/分组语义。

用法：python tools/perf_verify.py
- 复制 %APPDATA%/foxmail2md 配置指向的真实库到临时目录（不动原库）
- OLD：旧实现的 LIKE 全表扫描（两遍）耗时
- NEW：新 Store 构建 FTS + 搜索耗时（含构建时长，用于估算升级迁移耗时）
- 校验：新旧搜索结果一致、分组总数守恒、详情/列表列裁剪生效
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foxmail2md.core.store import Store, norm_subject_key, addr_key  # noqa: E402

TERMS = ['合同', '会议通知', 'project deadline', '发票', '附件']


def main():
    cfg = os.path.expandvars(r'%APPDATA%\foxmail2md\config.json')
    data_dir = ''
    if os.path.exists(cfg):
        import json
        data_dir = json.load(open(cfg, encoding='utf-8')).get('data_dir') or ''
    src = (Path(data_dir) / 'foxmail2md.db') if data_dir else \
        Path(os.path.expandvars(r'%APPDATA%\foxmail2md\foxmail2md.db'))
    if not src.exists():
        print(f'未找到真实库：{src}')
        return 1

    tmp = Path(tempfile.mkdtemp(prefix='fox2md_perf_'))
    dst = tmp / 'foxmail2md.db'
    print(f'复制库 {src} → {dst}')
    t0 = time.time()
    shutil.copy2(src, dst)
    print(f'复制完成 {time.time()-t0:.1f}s，大小 {dst.stat().st_size/1048576:.0f}MB')

    # ── OLD 路径：旧实现的 LIKE 搜索（COUNT 一遍 + 取页一遍） ──
    con = sqlite3.connect(str(dst))
    con.row_factory = sqlite3.Row
    old_times = {}
    old_counts = {}
    for term in TERMS:
        like = f'%{term}%'
        cond = ('subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ? '
                'OR body_text LIKE ? OR body_md LIKE ?')
        t0 = time.time()
        total = con.execute(f'SELECT COUNT(*) FROM emails WHERE {cond}',
                            [like] * 5).fetchone()[0]
        rows = con.execute(
            f'SELECT * FROM emails WHERE {cond} ORDER BY date_iso DESC LIMIT 50 OFFSET 0',
            [like] * 5).fetchall()
        old_times[term] = time.time() - t0
        old_counts[term] = total
    con.close()
    print('\n[OLD] LIKE 全表扫（每次搜索两遍扫描，含全部正文列取页）')
    for t in TERMS:
        print(f'  "{t}": {old_times[t]*1000:.0f}ms，{old_counts[t]} 条')

    # ── NEW 路径：新 Store + FTS 构建 ──
    print('\n[NEW] 初始化新 Store（建列+FTS 表+触发器）…')
    t0 = time.time()
    store = Store(dst)
    print(f'  init {time.time()-t0:.2f}s，fts_ready={store.fts_ready()}')

    print('[NEW] 回填分组键…')
    t0 = time.time()
    n = 0
    while True:
        c = store.backfill_norm_keys()
        n += c
        if c == 0:
            break
    print(f'  回填 {n} 行，{time.time()-t0:.1f}s')

    print('[NEW] 构建 FTS trigram 索引…')
    t0 = time.time()
    store.fts_build_begin()
    while True:
        done, top = store.fts_build_chunk()
        if done:
            break
    store.fts_build_finish()
    build_s = time.time() - t0
    print(f'  构建完成 {build_s:.1f}s（升级迁移时用户等待的时间）')
    print(f'  构建后库大小 {dst.stat().st_size/1048576:.0f}MB（构建前见复制行）')

    print('\n[NEW] FTS 搜索耗时：')
    ok = True
    for term in TERMS:
        t0 = time.time()
        r = store.list_mails(search=term, page=1, per_page=50)
        dt = time.time() - t0
        print(f'  "{term}": {dt*1000:.0f}ms，{r["total"]} 条（旧 {old_counts[term]} 条）')
        if r['total'] != old_counts[term]:
            ok = False
            print(f'    !! 结果数不一致')
    # 无命中词
    t0 = time.time()
    r = store.list_mails(search='zzxx不存在的词qqq', page=1, per_page=50)
    print(f'  无命中词: {r["total"]} 条（应 0），{dt*1000:.0f}ms')
    if r['total'] != 0:
        ok = False
    # 短词回退 LIKE 路径
    t0 = time.time()
    r = store.list_mails(search='发票', page=1, per_page=50)  # 2 字 → 回退 LIKE
    dt = time.time() - t0
    print(f'  2字短词回退 LIKE: {dt*1000:.0f}ms，{r["total"]} 条')
    # 无搜索列表
    t0 = time.time()
    r = store.list_mails(page=1, per_page=50)
    dt = time.time() - t0
    print(f'  无搜索分页列表: {dt*1000:.1f}ms，{r["total"]} 条/页，共 {r["total"]} 条')

    # ── 分组守恒 ──
    total_all = store.email_count()
    for g in ('subject', 'from', 'to'):
        t0 = time.time()
        gr = store.list_mails(group=g)
        dt = time.time() - t0
        s = sum(x['count'] for x in gr['groups'])
        status = 'OK' if s == total_all else '!! 守恒失败'
        if s != total_all:
            ok = False
        print(f'分组 {g}: {len(gr["groups"])} 组，成员合计 {s}/{total_all}，{dt*1000:.0f}ms {status}')
        # 抽查第一组成员
        first = gr['groups'][0]
        mem = store.list_group_members(g, first['key'], per_page=2000)
        if mem['total'] < first['count']:
            ok = False
            print('  !! 组成员数小于组计数')

    # ── 列表列裁剪验证：列表响应不含正文 ──
    r = store.list_mails(page=1, per_page=5)
    assert 'body_text' not in r['items'][0], '列表项不应包含正文列'
    print('列表项列裁剪：OK（无正文列）')

    # ── 清理：删除一封验证 FTS 触发器同步 ──
    one = store.list_mails(search='会议通知', per_page=1)['items'][0]
    before = store.list_mails(search='会议通知')['total']
    store.delete_mails([one['id']])
    after = store.list_mails(search='会议通知')['total']
    print(f'删除后 FTS 同步：{before} → {after}', 'OK' if after == before - 1 else '!! 失败')
    if after != before - 1:
        ok = False

    store.close()
    print('\n结论：', 'ALL OK' if ok else '存在问题，见上行 !!')
    print(f'临时目录保留供检查：{tmp}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
