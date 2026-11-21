#!/usr/bin/env python3
"""搜管我财本人的原话：雪球原创帖 + 回复（本地存档 管我财雪球存档/）。

在仓库根目录运行：
  python -I .claude/skills/guan-wocai/scripts/search.py 净现金 派息        # 两个词同时出现
  python -I .claude/skills/guan-wocai/scripts/search.py --any 大存大贷 存贷双高  # 出现任意一个
  python -I .claude/skills/guan-wocai/scripts/search.py 下行保护 --from 2025-01-01 --newest
  python -I .claude/skills/guan-wocai/scripts/search.py --id 357707820     # 打印一条全文和上下文

简体繁体都能搜：他多用繁体（淨現金、負債、閉眼估值），搜的时候两种写法当成同一个词
（用 Windows 自带的简繁转换；不在 Windows 上就只能按原样匹配，这时请同时试繁体写法）。
默认只搜他本人的话：回复里“//@”后面、以及“对方：”那一行是别人的话，不参与匹配，加 --with-context 才一起搜。
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE = '管我财雪球存档'
POSTS = '原创帖_原始数据.jsonl'
REPLY_FILES = ['回复_关键词搜索_原始数据.jsonl', '回复_补抓_2026-11-21.jsonl']
LONG_FILES = ['长帖全文_补抓_2026-11-21.jsonl']      # 原创帖里只存了开头的长帖，这里是全文
URL = 'https://xueqiu.com/9650668145/{}'
TZ = timezone(timedelta(hours=8))

# ---- 简繁归一：先用 Windows 的 LCMapStringEx 转成简体，再补几个它不转的字 ----
_EXTRA = str.maketrans({'後': '后', '乾': '干', '於': '于', '隻': '只', '準': '准', '週': '周', '佔': '占',
                        '凈': '净', '綫': '线', '着': '着', '甯': '宁', '眞': '真', '衆': '众', '爲': '为',
                        '僞': '伪', '囬': '回', '纔': '才', '裡': '里', '麪': '面', '镕': '熔'})
try:
    import ctypes
    _k = ctypes.WinDLL('kernel32')
    _f = _k.LCMapStringEx
    _f.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_int,
                   ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _f.restype = ctypes.c_int

    def _to_simp(s):
        if not s:
            return s
        n = _f('zh-CN', 0x02000000, s, len(s), None, 0, None, None, None)
        if n <= 0:
            return s
        buf = ctypes.create_unicode_buffer(n)
        _f('zh-CN', 0x02000000, s, len(s), buf, n, None, None, None)
        return buf.value
    HAS_CONV = True
except Exception:          # 不在 Windows 上
    def _to_simp(s):
        return s
    HAS_CONV = False


def norm(s):
    return _to_simp(s or '').translate(_EXTRA).casefold()


# ---- 他贴的 AI 回答、转贴的长文不是他的话 ----
AI_HINT_RE = re.compile(r'(以下是\s*(?:AI|Grok|Gemini|ChatGPT|DeepSeek|豆包)|AI\s*的?回答如下|Grok\s*回[覆复]|'
                        r'試了下\s*Grok|试了下\s*Grok|丟給\s*AI|丢给\s*AI|AI\s*回答|生成的?報告|生成的?报告)', re.I)
TABLE_RE = re.compile(r'\|\s*-{3,}|•\s')


def strip_html(s):
    s = re.sub(r'<br\s*/?>', '\n', s or '')
    s = re.sub(r'<img[^>]*title="([^"]*)"[^>]*>', r'\1', s)
    s = re.sub(r'<a [^>]*>(.*?)</a>', r'\1', s)
    return html.unescape(re.sub(r'<[^>]+>', '', s)).strip()


def fmt_date(t, with_time=False):
    return datetime.fromtimestamp(t / 1000, TZ).strftime('%Y-%m-%d %H:%M' if with_time else '%Y-%m-%d')


def load(root, with_context=False):
    base = root / ARCHIVE
    longtext = {}
    for fn in LONG_FILES:
        f = base / fn
        if f.is_file():
            for line in f.read_text(encoding='utf-8').splitlines():
                d = json.loads(line)
                longtext[str(d['id'])] = d.get('text') or ''
    items, seen = [], set()

    def add(d, kind):
        i = str(d['id'])
        if i in seen:
            return
        seen.add(i)
        raw = longtext.get(i) or d.get('text') or d.get('description') or ''
        text = strip_html(raw)
        title = strip_html(d.get('title') or '')
        mine, _, chain = text.partition('//@')
        reply_to = ''
        m = re.match(r'^回复@([^:：]+)[:：]\s*', mine)
        if m:
            reply_to, mine = m.group(1), mine[m.end():]
        rt = d.get('rt')
        if isinstance(rt, str):
            try:
                rt = json.loads(rt.replace("'", '"'))
            except ValueError:
                rt = None
        if chain:
            ctx = '@' + chain
        elif rt:
            ctx = '@%s：%s' % (rt.get('user', ''), strip_html(rt.get('text', '')))
        else:
            ctx = ''
        note = []
        if AI_HINT_RE.search(mine) or (len(mine) > 900 and TABLE_RE.search(mine)):
            note.append('这条里贴了 AI 的回答或分析，多半只有开头一两句是他自己的话，引用前打开链接核对')
        elif len(mine) > 900 and not title:
            note.append('长文，可能大段是他转贴的别人文章或新闻，引用前打开链接核对哪句是他写的')
        if i in longtext:
            note.append('原创帖存档里只有开头，这里是补抓的全文')
        if not mine.strip():
            note.append('他只转发、没写自己的话')
        t = int(d['t'])
        items.append({'id': i, 't': t, 'kind': kind, 'title': title, 'reply_to': reply_to, 'text': mine.strip(),
                      'ctx': ctx, 'note': note,
                      'key': norm(title + '\n' + mine + ('\n' + ctx if with_context else ''))})

    f = base / POSTS
    for line in f.read_text(encoding='utf-8').splitlines():
        add(json.loads(line), '原创帖')
    for fn in REPLY_FILES:
        f = base / fn
        if not f.is_file():
            continue
        for line in f.read_text(encoding='utf-8').splitlines():
            d = json.loads(line)
            add(d, '原创帖' if str(d.get('type') or '') == '0' else '回复')
    return items


def clip(s, n):
    s = s.replace('\n', ' ')
    return s if n <= 0 or len(s) <= n else s[:n] + '…（共 %d 字）' % len(s)


def show(h, n_clip, n_ctx=160):
    print('\n[%s] %s · %s' % (h['kind'], URL.format(h['id']), fmt_date(h['t'], True)))
    for x in h['note']:
        print('〔%s〕' % x)
    if h['ctx']:
        print('对方：' + clip(h['ctx'], n_ctx))
    head = ('回复 @%s：' % h['reply_to']) if h['reply_to'] else ''
    title = ('《%s》' % h['title']) if h['title'] else ''
    print('管我财：' + head + title + clip(h['text'], n_clip))


def main():
    ap = argparse.ArgumentParser(description='搜管我财本人的原话')
    ap.add_argument('words', nargs='*', help='关键词，默认要同时出现；简繁都行')
    ap.add_argument('--any', action='store_true', help='关键词出现任意一个即可')
    ap.add_argument('--source', choices=['posts', 'replies', 'all'], default='all')
    ap.add_argument('--from', dest='date_from', help='起始日期 YYYY-MM-DD')
    ap.add_argument('--to', dest='date_to', help='截止日期 YYYY-MM-DD')
    ap.add_argument('--with-context', action='store_true', help='连他回复的对方原话一起匹配（只当线索）')
    ap.add_argument('--max', type=int, default=30, help='最多打印几条，0 表示全部')
    ap.add_argument('--clip', type=int, default=400, help='每条最多打印几个字，0 表示全文')
    ap.add_argument('--newest', action='store_true', help='按时间从新到旧排')
    ap.add_argument('--id', help='按帖子编号打印一条全文')
    ap.add_argument('--root', help='仓库根目录，默认按脚本位置推算')
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')
    root = Path(a.root) if a.root else Path(__file__).resolve().parents[4]
    if not (root / ARCHIVE).is_dir():
        sys.exit('找不到存档目录：%s' % (root / ARCHIVE))
    items = load(root, a.with_context)

    if a.id:
        hit = [h for h in items if h['id'] == a.id.strip()]
        if not hit:
            sys.exit('没有这一条：%s' % a.id)
        show(hit[0], 0, 0)
        return
    if not a.words:
        ap.error('要给关键词，或者用 --id')
    if not HAS_CONV:
        print('〔提醒：这台机器没有简繁转换，只按原样匹配；他多用繁体，请同时试繁体写法〕')

    words = [norm(w) for w in a.words]
    pick = []
    for h in items:
        if a.source == 'posts' and h['kind'] != '原创帖':
            continue
        if a.source == 'replies' and h['kind'] != '回复':
            continue
        d = fmt_date(h['t'])
        if (a.date_from and d < a.date_from) or (a.date_to and d > a.date_to):
            continue
        found = [w in h['key'] for w in words]
        if any(found) if a.any else all(found):
            pick.append(h)
    pick.sort(key=lambda h: h['t'], reverse=a.newest)
    print('共找到 %d 条%s' % (len(pick), '' if a.max == 0 or len(pick) <= a.max
                              else '，只打印前 %d 条（--max 0 看全部，--newest 先看新的）' % a.max))
    for h in pick[: a.max or None]:
        show(h, a.clip)


if __name__ == '__main__':
    main()
