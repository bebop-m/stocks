#!/usr/bin/env python3
"""搜段永平本人的原话：本地问答录存档 + 雪球补充。

在仓库根目录运行：
  python -I .claude/skills/duan-yongping/scripts/search.py 分红 回购      # 两个词同时出现
  python -I .claude/skills/duan-yongping/scripts/search.py --any 烟蒂 烟屁股  # 出现任意一个
  python -I .claude/skills/duan-yongping/scripts/search.py --page wenda-invest-10
  python -I .claude/skills/duan-yongping/scripts/search.py --list

默认只搜他本人的话：问答录和访谈里“段永平：/大道：/段：”开头的回答（访谈里“问：”后面的“答：”也算），
加上雪球补充里他自己的帖子和回复。编者整理页、别人的演讲、网友的提问都不算他的原话。
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ARCHIVE = '段永平大道总纲存档'
XUEQIU_FILE = Path('雪球补充_2026-08-27至11-18') / '原始数据.jsonl'
XUEQIU_URL = 'https://xueqiu.com/1247347556/{}'

HIS_LABELS = {'段永平', '大道', '段'}
ANSWER_LABELS = {'答', 'A'}          # 只在“问：/Q：”之后才算他的回答
QUESTION_LABELS = {'问', 'Q'}
OTHER_SPEAKERS = {'主持人', '记者', '记', '听众', '方三文', '田朴珺', '王石', '徐中', '刘坚', '孔繁敏',
                  '观众', '现场观众', '校友', '本刊记者', '采访者手记', '有同学问', '胡海平', '袁鸣', '纪宝成',
                  '刘志超', '不明真相的群众', '《财经》', '《21世纪》', '《财富》', '东方早报'}
# 编者插进问答录的别人的话（巴菲特、芒格语录，库克、黄峥的原话，新闻），不是他的原话
QUOTE_SPEAKERS = {'巴菲特', '芒格', '格雷厄姆', '费雪', '乔布斯', '库克', '黄峥', '韦尔奇', '李开复', '马蔚华',
                  '译文', '引用', '【引用】巴菲特', '新浪财经', '快讯', '链接'}
OTHER_SPEECH_PREFIXES = ('shenwei-', 'feidele-', 'tim-cook-', 'buffett-')

LABEL_RE = re.compile(r'^([^：:\s]{1,12})\s*[：:]\s*(.*)$')
PAREN_DATE_RE = re.compile(r'[（(][^（）()]{0,40}?(\d{4}-\d{2}-\d{2})[^（）()]{0,10}[）)]')
SECTION_RE = re.compile(r'^第[一二三四五六七八九十百零〇]+[节章卷]')
BLOCK_TAGS = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote',
              'tr', 'section', 'article', 'header', 'footer'}
SKIP_TAGS = {'script', 'style', 'nav', 'noscript', 'svg', 'button'}
TZ = timezone(timedelta(hours=8))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in BLOCK_TAGS:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def page_lines(path):
    p = _Text()
    p.feed(path.read_text(encoding='utf-8'))
    return [ln.strip() for ln in ''.join(p.out).split('\n') if ln.strip()]


def page_kind(slug, title):
    if slug.startswith('wenda-') and slug != 'wenda-topic-index':
        return '问答录'
    if slug.startswith(OTHER_SPEECH_PREFIXES):
        return '别人的演讲'
    if '主题迁移导读' in title:
        return '导读'
    if slug.startswith('duanyongping-'):
        return '访谈演讲'
    return '编者整理'


AI_NAMES = ('AI', 'Gemini', 'ChatGPT', 'GPT', 'Grok', 'Claude', 'DeepSeek', 'Kimi', '豆包', '元宝', '千问')
_AI = '|'.join(AI_NAMES)
AI_SPLIT_RE = re.compile(r'\s*(?=(?:%s)[^\s：:，。！？、]{0,3}\s*[：:])' % _AI)   # “Gemini：”“Gemini数据：”
AI_MADE_RE = re.compile(r'(?:%s)\s*(?:翻译|数据|整理|生成)' % _AI)           # “Gemini翻译的”
AI_FORMAT_RE = re.compile(r'\|\s*-{3,}|\\text\{')                           # Markdown 表格、LaTeX 残留
BULLET_RE = re.compile(r'(?:^|[。：:\s])\*\s')                                # “* ”开头的分条


def speaker_of(label):
    """返回 'his' / 'answer' / 'question' / 'quote' / None（None 表示不是说话人，当正文接着上一段）。"""
    if '转' in label or label in QUOTE_SPEAKERS or any(n.casefold() in label.casefold() for n in AI_NAMES):
        return 'quote'      # 他转贴的 AI 回答、编者插进来的别人的话，不是他的原话
    if label in HIS_LABELS:
        return 'his'
    if label in ANSWER_LABELS:
        return 'answer'
    if label in QUESTION_LABELS or label.startswith('网友') or label.startswith('主持人'):
        return 'question'
    if label in OTHER_SPEAKERS:
        return 'question'
    return None


def parse_page(slug, path):
    lines = page_lines(path)
    title = lines[0] if lines else slug
    kind = page_kind(slug, title)
    year = None
    m = re.search(r'(\d{4})(?:nian|-)', slug)
    if m and 1990 <= int(m.group(1)) <= 2030:
        year = m.group(1)
    blocks = []          # dict(role, label, text, section)
    section = ''
    started = False
    for ln in lines[1:]:
        if ln == '评论' and started:
            break         # 页脚
        if ln == '目录':
            continue
        if SECTION_RE.match(ln) or ln == '原始出处':
            section = ln
            started = True
            blocks.append({'role': 'section', 'label': '', 'text': ln, 'section': ln})
            continue
        if ln.startswith('【引用】'):
            blocks.append({'role': 'quote', 'label': '引用', 'text': ln[4:], 'section': section})
            started = True
            continue
        m = LABEL_RE.match(ln)
        role = speaker_of(m.group(1)) if m else None
        if role == 'answer':
            prev_q = next((b for b in reversed(blocks) if b['role'] in ('question', 'his')), None)
            role = 'his' if prev_q and prev_q['label'] in QUESTION_LABELS else None
        if role:
            started = True
            blocks.append({'role': role, 'label': m.group(1), 'text': m.group(2), 'section': section})
        elif started and blocks and blocks[-1]['role'] in ('his', 'question', 'quote'):
            blocks[-1]['text'] += '\n' + ln
        else:
            blocks.append({'role': 'narrative', 'label': '', 'text': ln, 'section': section})
    # 给他的回答配上提问和日期
    hits = []
    question = ''
    for i, b in enumerate(blocks):
        if b['role'] == 'question':
            question = b['text']
            continue
        if b['role'] == 'section':
            question = ''
            continue
        if b['role'] != 'his':
            continue
        dates = PAREN_DATE_RE.findall(b['text'])
        date = dates[-1] if dates else ''
        if not date and kind == '问答录':
            # 同一问下面连着的几段回答，日期常标在最后一段
            for nb in blocks[i + 1:]:
                if nb['role'] != 'his':
                    break
                d2 = PAREN_DATE_RE.findall(nb['text'])
                if d2:
                    date = d2[-1]
                    break
        hits.append({'source': kind, 'slug': slug, 'title': title, 'section': b['section'],
                     'date': date or (year or ''), 'dated': bool(date), 'question': question,
                     'text': b['text']})
    return title, kind, blocks, hits


def load_site(root):
    site = root / ARCHIVE / 'site'
    pages = {}
    for d in sorted(site.iterdir()):
        f = d / 'index.html'
        if d.is_dir() and f.is_file():
            pages[d.name] = parse_page(d.name, f)
    return pages


def strip_html(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()


def load_xueqiu(root):
    f = root / ARCHIVE / XUEQIU_FILE
    out = []
    if not f.is_file():
        return out
    for line in f.read_text(encoding='utf-8').splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get('t')
        date = datetime.fromtimestamp(t / 1000, TZ).strftime('%Y-%m-%d') if isinstance(t, (int, float)) else ''
        text = strip_html(d.get('text', ''))
        # 回复的格式是“回复@某人: 他的话//@某人:对方的话//@……”，// 后面是别人的话
        mine, _, chain = text.partition('//@')
        reply_to = ''
        m = re.match(r'^回复@([^:：]+)[:：]\s*', mine)
        if m:
            reply_to, mine = m.group(1), mine[m.end():]
        # 他有时把 AI 的回答贴在后面（“Gemini：……”），那部分不是他的原话
        parts = AI_SPLIT_RE.split(mine, maxsplit=1)
        mine = parts[0].strip()
        ai_part = parts[1].strip() if len(parts) > 1 else ''
        rt = d.get('rt') or {}
        if chain:
            q = '（回复 @%s）%s' % (reply_to, '@' + chain) if reply_to else '@' + chain
        elif rt:
            q = '@%s：%s' % (rt.get('user', ''), strip_html(rt.get('text', '')))
        else:
            q = ''
        # 有的 AI 回答没有署名，只能从格式看出来（表格、LaTeX、大段分条），整条提醒，不切分
        note = ''
        if ai_part:
            note = '他在后面贴了 AI 的回答，这里只留了他自己的话；AI 那段不是他的原话：%s' % clip(ai_part, 60)
        elif AI_FORMAT_RE.search(mine) or AI_MADE_RE.search(mine) or (len(mine) > 600 and BULLET_RE.search(mine)):
            note = '这条带表格或大段分条，或者他说明是 AI 写的，多半是他贴的 AI 回答，可能只有开头或结尾一两句是他自己的话；引用前打开链接核对'
        elif not mine:
            note = '他只是转发，没有写自己的话；“问：”那段是别人的话'
        out.append({'source': '雪球', 'slug': XUEQIU_URL.format(d.get('id')), 'title': '', 'section': '',
                    'date': date, 'dated': bool(date), 'question': q, 'text': mine, 'note': note})
    return out


def matches(hit_text, words, any_mode):
    t = hit_text.casefold()
    found = [w.casefold() in t for w in words]
    return any(found) if any_mode else all(found)


def clip(s, n):
    s = s.replace('\n', ' ')
    return s if n <= 0 or len(s) <= n else s[:n] + '…（共 %d 字）' % len(s)


def main():
    ap = argparse.ArgumentParser(description='搜段永平本人的原话')
    ap.add_argument('words', nargs='*', help='关键词，默认要同时出现')
    ap.add_argument('--any', action='store_true', help='关键词出现任意一个即可')
    ap.add_argument('--source', choices=['site', 'xueqiu', 'all'], default='all')
    ap.add_argument('--all', action='store_true', help='连编者整理页、导读、别人的演讲一起搜（只当线索，不是他的原话）')
    ap.add_argument('--answer-only', action='store_true', help='只在他的回答里匹配（默认提问和回答都算）')
    ap.add_argument('--max', type=int, default=30, help='最多打印几条，0 表示全部')
    ap.add_argument('--clip', type=int, default=500, help='每条回答最多打印几个字，0 表示全文')
    ap.add_argument('--newest', action='store_true', help='按时间从新到旧排')
    ap.add_argument('--page', help='按顺序打印一页（slug），看上下文')
    ap.add_argument('--list', action='store_true', help='列出全部页面、类型和标题')
    ap.add_argument('--root', help='仓库根目录，默认按脚本位置推算')
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')
    root = Path(a.root) if a.root else Path(__file__).resolve().parents[4]
    if not (root / ARCHIVE).is_dir():
        sys.exit('找不到存档目录：%s' % (root / ARCHIVE))

    if a.list:
        pages = load_site(root)
        for slug, (title, kind, _b, hits) in pages.items():
            print('%s\t%s\t%s\t他的回答 %d 段' % (slug, kind, title.split('｜')[0], len(hits)))
        return

    if a.page:
        f = root / ARCHIVE / 'site' / a.page / 'index.html'
        if not f.is_file():
            sys.exit('没有这一页：%s（用 --list 看全部 slug）' % a.page)
        title, kind, blocks, _h = parse_page(a.page, f)
        print('%s · %s · %s\n' % (a.page, kind, title))
        for b in blocks:
            if b['role'] == 'narrative' and not b['section']:
                continue      # 页头导航
            if b['role'] == 'his':
                print('段永平：' + b['text'])
            elif b['role'] == 'quote':
                print('%s（不是他的原话）：%s' % (b['label'], b['text']))
            elif b['label']:
                print('%s：%s' % (b['label'], b['text']))
            else:
                print(b['text'])
        return

    if not a.words:
        ap.error('要给关键词，或者用 --page / --list')

    hits = []
    if a.source in ('site', 'all'):
        for slug, (title, kind, blocks, his) in load_site(root).items():
            if kind in ('问答录', '访谈演讲'):
                hits.extend(his)
            if not a.all:
                continue
            for b in blocks:
                if b['role'] == 'section' or (b['role'] == 'narrative' and not b['section'] and kind == '问答录'):
                    continue
                if kind in ('问答录', '访谈演讲') and b['role'] in ('his', 'question'):
                    continue      # 他的回答已经算过；提问随回答一起显示
                tag = '访谈叙述' if kind == '访谈演讲' else kind
                hits.append({'source': tag + '（不是原话）', 'slug': slug, 'title': title,
                             'section': b['section'], 'date': '', 'dated': False,
                             'question': '', 'text': b['text']})
    if a.source in ('xueqiu', 'all'):
        hits.extend(load_xueqiu(root))

    found = [h for h in hits
             if matches(h['text'] if a.answer_only else h['question'] + '\n' + h['text'], a.words, a.any)]
    found.sort(key=lambda h: (h['date'] == '', h['date']), reverse=False)
    if a.newest:
        found.sort(key=lambda h: h['date'], reverse=True)
    print('共找到 %d 条%s' % (len(found), '' if a.max == 0 or len(found) <= a.max else '，只打印前 %d 条（--max 0 看全部）' % a.max))
    for h in found[: a.max or None]:
        where = h['slug'] if h['source'] == '雪球' else '%s · %s' % (h['slug'], h['title'].split('｜')[0])
        sec = (' · ' + h['section']) if h['section'] else ''
        date = h['date'] if h['dated'] else ('%s（页面年份，回答未标日期）' % h['date'] if h['date'] else '未标日期')
        print('\n[%s] %s%s · %s' % (h['source'], where, sec, date))
        if h.get('note'):
            print('〔%s〕' % h['note'])
        if h['question']:
            print('问：' + clip(h['question'], 160))
        print(('段永平：' if '不是原话' not in h['source'] else '') + clip(h['text'], a.clip))


if __name__ == '__main__':
    main()
