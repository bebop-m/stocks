#!/usr/bin/env python3
"""持仓体检：把 stocks.md 里定好的规矩，对着账本里的真实持仓算一遍。只算数，不下结论。

在仓库根目录运行。数据文件都放 scratchpad，不要放进 stocks 仓库（它是公开的）：

  python -I .claude/skills/chicang-tijian/scripts/checkup.py <portfolio.json> <market.json> \
      --fundamentals <fundamentals.json> --facts <facts.json> --notes <notes.json> [--costs <costs.json>] \
      --expense <当年开支> --reserve <备用金目标> [--margin-rate <融资年利率>] \
      --core-caps "<代码>=<上限>,<代码>=<上限>" --benchmark <基准代码> --bond-yield "<收益率>@<日期>" \
      --gray "<代码>,<代码>" \
      --groups "<组名>=<代码>,<代码>;<组名>=<代码>" \
      --out <scratchpad>/checkup.md --html <scratchpad>/checkup.html

分工：
- 脚本：全部算术和规矩的状态（仓位、一成线、停手、5% 锚、规则余量、八折、同类减派、今年至今、融资）。
- facts.json（facts.py 从财报接口整理）：每家公司最近一个财年的净利润、派息、经营现金流、资本开支、
  收到的分红，最近一期的现金、有息借款、净资产、应收账款，以及按 净利润 ÷ 每股收益 估的股本。
- notes.json（AI 每次体检写）：每家公司的类型标签、行业归属（按利润和风险真正来自哪里）、派息核对结论、
  特别股息每股多少和能不能持续、要不要补一个这家公司特有的数、一句话看法；整个组合的几句复盘。
  格式见 SKILL.md。脚本不替 AI 下判断，AI 也不做加减乘除。
- costs.json（可选）：账本开始记交易以前买的打工仓，投进去多少钱 {"<代码>": 人民币金额}。
  管我财那条“仓位上限按买入价算”要用；没有的就照实写“成本没补”。

口径：
- 股数、税率、融资、现金：portfolio.json 最后一天的 dailySnapshots（holdings[].quantity 是期初股数）。
- 股息：过去 12 个月每股派息（按除净日，正好满 12 个月那天的不算）× 现在股数 × 汇率 × (1 - 税率)，含特别股息；
  和账本显示的数不一样时（账本会把正好满 12 个月那天的也算进去），首屏写出来；
  去掉特别股息的数 = 账本口径 - notes 里核对过的特别股息（行情源常把特别股息并进常规，以核对为准）。
- 仓位都按组合现在的净值（持仓市值 + 账本现金）算。买入成本只用在管我财那条的上限，不用来判断拿不拿。
- “你这一份”= 公司数 × 持有股数 ÷ 总股本，换成人民币。
"""
import argparse
import html
import json
from datetime import date, timedelta


def load(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def d(s):
    return date.fromisoformat(str(s)[:10])


MINUS = '−'


def pct(x, nd=1):
    if x is None:
        return '—'
    s = ('%.' + str(nd) + 'f%%') % (x * 100)
    return s.replace('-', MINUS)


def wan(x, nd=None, sign=False):
    """万元。10 万以下两位小数，以上一位。"""
    if x is None:
        return '—'
    v = x / 10000
    if nd is None:
        nd = 2 if abs(v) < 10 else 1
    s = ('%+.' + str(nd) + 'f' if sign else '%.' + str(nd) + 'f') % v
    return s.replace('-', MINUS) + ' 万'


def yi(x):
    if x is None:
        return '—'
    v = x / 1e8
    return ('%.1f 亿' % v if abs(v) < 1000 else '%.0f 亿' % v).replace('-', MINUS)


def parse_kv(s):
    out = {}
    for part in (s or '').split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = float(v)
    return out


def parse_groups(s):
    out = {}
    for part in (s or '').split(';'):
        if '=' in part:
            name, syms = part.split('=', 1)
            out[name.strip()] = [x.strip() for x in syms.split(',') if x.strip()]
    return out


def in_ttm(e, asof):
    return asof - timedelta(days=365) < d(e['exDate']) <= asof


def main():
    ap = argparse.ArgumentParser(description='持仓体检')
    ap.add_argument('portfolio')
    ap.add_argument('market')
    ap.add_argument('--fundamentals', help='账本网页的年报数据 fundamentals.json（每股收益走势）')
    ap.add_argument('--facts', help='facts.py 整理的财报关键数')
    ap.add_argument('--notes', help='AI 写的每家公司判断和组合复盘')
    ap.add_argument('--costs', help='账本开始以前买的打工仓投入了多少钱（可选）')
    ap.add_argument('--expense', type=float, help='当年开支（人民币），从 stocks.md 第 1 节读')
    ap.add_argument('--margin-rate', type=float, help='融资年利率（如 0.045），以券商为准；不知道就不传')
    ap.add_argument('--reserve', type=float, help='生活费备用金的目标（人民币），从 stocks.md 第 1 节读；算“差额备用金能顶几年”')
    ap.add_argument('--bond-yield', help='10 年期国债收益率和日期，如 "1.58@2026-11-20"（茅台自己的底线要比它，stocks.md 第 2 节）')
    ap.add_argument('--core-caps', help='核心仓买入上限，如 "<代码>=<上限>,<代码>=<上限>"')
    ap.add_argument('--benchmark', help='机会成本的基准（stocks.md 第 2 节），显示它现在的股息率')
    ap.add_argument('--gray', help='灰色地带的代码，逗号分隔')
    ap.add_argument('--industries', help='notes 里没写行业时的后备，如 "<行业>=<代码>,<代码>;..."')
    ap.add_argument('--groups', help='同一类风险的分组，如 "<组名>=<代码>,<代码>"')
    ap.add_argument('--out', required=True, help='给 AI 看的底稿（utf-8 markdown）')
    ap.add_argument('--html', help='给用户在手机上看的 HTML')
    a = ap.parse_args()

    pf, mk = load(a.portfolio), load(a.market)
    funds = (load(a.fundamentals).get('companies') or {}) if a.fundamentals else {}
    facts = load(a.facts) if a.facts else {}
    notes = load(a.notes) if a.notes else {}
    costs = load(a.costs) if a.costs else {}
    snap = pf['dailySnapshots'][-1]
    asof = d(snap['date'])
    rates = mk.get('rates') or pf.get('rates') or {'CNY': 1}
    quotes = mk['quotes']
    gray = {x.strip() for x in (a.gray or '').split(',') if x.strip()}
    caps = parse_kv(a.core_caps)
    groups = parse_groups(a.groups)
    ind_fallback = {}
    for g, ss in parse_groups(a.industries).items():
        for s in ss:
            ind_fallback[s] = g

    def fx(cur):
        return float(rates.get(cur or 'CNY', 1))

    total_mv = float(snap.get('totalMarketValueCny') or 0)
    cash = snap.get('cashCny')
    net = float(snap.get('netCny') or total_mv)
    margin = -cash if (cash is not None and cash < 0) else 0.0

    # 账本开始以后的买卖：算管我财那条的投入，以及最近的买卖
    opening = pf.get('positionOpeningDate') or ''
    base_qty = {h['symbol']: float(h.get('quantity') or 0) for h in pf.get('holdings') or []}
    trades = sorted(pf.get('trades') or [], key=lambda t: (t['date'], t.get('id') or ''))
    pos = {}
    for t in trades:
        s = t['symbol']
        p = pos.setdefault(s, {'sh': base_qty.get(s, 0.0), 'known_sh': 0.0, 'cost': 0.0})
        sh = float(t['shares'])
        v = sh * float(t['price']) * float(t.get('fxRate') or 1) + float(t.get('feeCny') or 0) * (1 if t['side'] == 'buy' else -1)
        if t['side'] == 'buy':
            p['sh'] += sh
            p['known_sh'] += sh
            p['cost'] += v
        else:
            unknown = p['sh'] - p['known_sh']
            out_unknown = min(sh, max(0.0, unknown))
            out_known = sh - out_unknown
            avg = p['cost'] / p['known_sh'] if p['known_sh'] > 0 else 0
            p['cost'] -= avg * out_known
            p['known_sh'] -= out_known
            p['sh'] -= sh

    rows = []
    for h in snap['holdings']:
        sh = float(h['shares'])
        if sh <= 0:
            continue
        s = h['symbol']
        q = quotes.get(s, {})
        cur = q.get('currency', 'CNY')
        price = float(q.get('price') or 0)
        tax = float(h.get('taxRate') or 0)
        ttm_ledger = float(q.get('dividendPerShareTtm') or 0)
        evs = [e for e in q.get('dividends') or [] if in_ttm(e, asof)]
        if q.get('dividends') and all((e.get('currency') or cur) == cur for e in evs):
            ttm = sum(float(e['amountPerShare']) for e in evs)
        else:
            ttm = ttm_ledger
        n = notes.get(s) or {}
        div_cur = n.get('div_cur') or cur
        k = 1 - tax
        ttm_net = ttm * sh * fx(cur) * k
        ledger_net = ttm_ledger * sh * fx(cur) * k
        if 'special_ps' in n:
            spc_net = float(n['special_ps']) * sh * fx(div_cur) * k
        else:
            spc_net = sum(float(e['amountPerShare']) for e in q.get('dividends') or []
                          if e.get('kind') == 'special' and in_ttm(e, asof)) * sh * fx(cur) * k
        spc_net = min(spc_net, ttm_net)
        kind = '核心仓' if h.get('bucket') == 'core' else ('灰色地带' if s in gray else '打工仓')
        mv = price * sh * fx(cur)
        r = {
            'sym': s, 'name': q.get('name', s), 'kind': kind, 'shares': sh, 'cur': cur, 'price': price, 'tax': tax,
            'mv': mv, 'w': mv / net if net else 0, 'net': ttm_net, 'spc': spc_net, 'reg': ttm_net - spc_net, 'note': n,
            'ledger_net': ledger_net,
        }
        if n.get('runrate_ps'):
            r['run'] = float(n['runrate_ps']) * sh * fx(div_cur) * k
        if n.get('restore_ps'):
            r['restore'] = float(n['restore_ps']) * sh * fx(div_cur) * k
        if n.get('buyback') and n['buyback'].get('pct') is not None:
            r['bb'] = float(n['buyback']['pct'])
        # 你这一份
        f = facts.get(s) or {}
        fund = funds.get(s) or {}
        if f.get('shares_est'):
            frac = sh / float(f['shares_est'])
            stmt = n.get('stmt_cur') or fund.get('statementCurrency') or f.get('currency') or 'CNY'
            c = frac * fx(stmt)

            def sl(x):
                return None if x is None else float(x) * c
            r['slice'] = {
                'earn': sl(f.get('net_profit')), 'earn_prev': sl(f.get('net_profit_prev')),
                'paid': sl(f.get('dividends_paid')),
                'fcf': sl(f['ocf'] - f['capex']) if f.get('ocf') is not None and f.get('capex') is not None else None,
                'recv_div': sl(f.get('dividends_received')),
                'cash': sl((f.get('cash') or 0) + (f.get('fin_assets') or 0)) if f.get('cash') is not None else None,
                'cash_only': sl(f.get('cash')), 'fin': sl(f.get('fin_assets') or 0),
                'ocf': sl(f.get('ocf')),
                'debt': sl(f.get('debt') or 0),
                'equity': sl(f.get('equity')),
                'fy': f.get('fy'), 'bal_date': f.get('balance_date'),
            }
            if f.get('revenue') and f.get('revenue_prev'):
                r['slice']['rev_g'] = f['revenue'] / f['revenue_prev'] - 1
            if f.get('receivables') and f.get('receivables_1y_ago'):
                r['slice']['recv_g'] = f['receivables'] / f['receivables_1y_ago'] - 1
            if f.get('revenue_same_g') is not None:
                r['slice']['rev_same_g'] = f['revenue_same_g']
            if f.get('advances') is not None:
                r['slice']['advances'] = sl(f['advances'])
            ex = n.get('extra') or {}
            if ex.get('amount') is not None:
                r['extra'] = {'label': ex.get('label', ''), 'val': sl(ex['amount']),
                              'sub': ('%s %s' % (ex.get('sub_label', ''), wan(sl(ex['sub_amount'])))) if ex.get('sub_amount') is not None else ''}
        eps = [(y['year'], y['eps']) for y in fund.get('years') or [] if y.get('eps') is not None]
        r['eps'] = eps[-4:]
        # 投入（管我财那条）
        p = pos.get(s)
        cost = None
        if p and abs(p['sh'] - sh) < 1e-6:
            unknown = p['sh'] - p['known_sh']
            if unknown <= 1e-6:
                cost = p['cost']
            elif s in costs:
                cost = float(costs[s]) * (unknown / base_qty[s] if base_qty.get(s) else 1) + p['cost']
        elif s in costs and not p:
            cost = float(costs[s])
        r['cost'] = cost
        r['cost_partial'] = (p['cost'] if p and p['known_sh'] > 0 and cost is None else None)
        r['industry'] = n.get('industry') or ind_fallback.get(s) or fund.get('industry')
        rows.append(r)

    T = sum(r['net'] for r in rows)
    T_ledger = sum(r['ledger_net'] for r in rows)
    ttm_diff = [r for r in rows if abs(r['ledger_net'] - r['net']) >= 50]
    T_spc = sum(r['spc'] for r in rows)
    T_reg = T - T_spc
    for r in rows:
        r['share'] = r['net'] / T if T else 0
        r['share_reg'] = r['reg'] / T_reg if T_reg else 0
        r['y'] = r['net'] / r['mv'] if r['mv'] else 0
        r['y_reg'] = r['reg'] / r['mv'] if r['mv'] else 0
    rows.sort(key=lambda r: -r['mv'])
    idx = {r['sym']: r for r in rows}
    work = [r for r in rows if r['kind'] == '打工仓']

    # 实际到账（过去 12 个月）和今年已确认到账
    ignored = set(pf.get('dividendLedgerIgnored') or [])
    recv_12m = 0.0
    recv_year = 0.0
    for x in pf.get('dividendLedger') or []:
        if x.get('sourceId') in ignored or x.get('id') in ignored:
            continue
        v = float(x.get('netCny') or 0)
        if asof - timedelta(days=365) < d(x['exDate']) <= asof:
            recv_12m += v
        when = x.get('receivedDate') or x.get('payDate') or x.get('exDate') or ''
        if x.get('confirmed') is True and str(when)[:4] == str(asof.year):
            recv_year += v

    # 融资
    snaps = [s for s in pf['dailySnapshots'] if s.get('cashCny') is not None]

    def margin_at(days):
        t = asof - timedelta(days=days)
        older = [s for s in snaps if d(s['date']) <= t]
        if not older:
            return None, None
        c = older[-1]['cashCny']
        return older[-1]['date'], (-c if c < 0 else 0.0)
    interest = margin * a.margin_rate if a.margin_rate else None
    recent = [t for t in trades if d(t['date']) > asof - timedelta(days=30)]
    buys30 = sum(float(t['shares']) * float(t['price']) * float(t.get('fxRate') or 1) for t in recent if t['side'] == 'buy')
    sells30 = sum(float(t['shares']) * float(t['price']) * float(t.get('fxRate') or 1) for t in recent if t['side'] != 'buy')

    # 今年至今（账本的算法：资金收益 ÷ 年初净值）
    ytd = None
    manual = {m.get('year'): m for m in pf.get('yearlyManual') or []}
    prev = manual.get(asof.year - 1) or {}
    start = prev.get('yearEndNetCny')
    if start:
        def signed(c):
            v = abs(float(c.get('amountCny') or 0))
            return -v if str(c.get('type', '')).lower() in ('withdraw', 'withdrawal', 'out', 'outflow') else v
        flows = [c for c in pf.get('cashFlows') or [] if str(c.get('date', ''))[:4] == str(asof.year)]
        flow = sum(signed(c) for c in flows)
        capret = net - float(start) - flow
        ytd = {'start': float(start), 'end': net, 'flow': flow, 'cap': capret, 'rate': capret / float(start)}
        open_snap = next((s for s in pf['dailySnapshots'] if opening and s['date'] >= opening), None)
        if open_snap and str(opening)[:4] == str(asof.year):
            f_before = sum(signed(c) for c in flows if c['date'] < opening)
            f_after = flow - f_before
            o = float(open_snap['netCny'])
            ytd['split'] = {'opening': opening, 'open_net': o, 'seg1': o - float(start) - f_before,
                            'seg2': net - o - f_after, 'flow_after': f_after, 'flow_before_known': bool(f_before)}

    # 行业（打工仓，按市值，上限 20%）
    ind = {}
    for r in work:
        if not r['industry'] or r['industry'] == '指数基金':
            continue
        g = ind.setdefault(r['industry'], {'name': r['industry'], 'members': [], 'mv': 0.0, 'cost': 0.0, 'cost_known': True})
        g['members'].append(r)
        g['mv'] += r['mv']
        if r['cost'] is None:
            g['cost_known'] = False
        else:
            g['cost'] += r['cost']
    ind_rows = sorted(ind.values(), key=lambda g: -g['mv'])
    for g in ind_rows:
        g['w'] = g['mv'] / net
        for m in g['members']:
            m['ind_w'] = g['w']
            m['ind_mv'] = g['mv']

    # 每只打工仓：规矩的状态和余量
    for r in work:
        n = r['note']
        stop, watch = [], []
        status = n.get('status')
        if r['net'] <= 0:
            watch.append('不派息')
        if status == 'cut':
            stop.append('停手 · 派息降了')
        elif status == 'mixed':
            watch.append('派息有升有降')
        elif not status:
            watch.append('派息待核对')
        over = max(r['share'], r['share_reg'])
        if r['net'] > 0 and n.get('industry') != '指数基金':
            if over >= 0.10:
                stop.append('过一成线 %s / %s' % (pct(r['share']), pct(r['share_reg'])))
            elif over >= 0.08:
                watch.append('接近一成线 %s / %s' % (pct(r['share']), pct(r['share_reg'])))
        if r['w'] >= 0.08:
            (stop if r['w'] >= 0.10 else watch).append('市值占 %s' % pct(r['w']))
        if r['net'] > 0 and r['y_reg'] < 0.05 and n.get('industry') != '指数基金':
            watch.append('常规息 %s，低于 5%%' % pct(r['y_reg']))
        # 规则余量
        rooms = [('市值 10%', 0.10 * net - r['mv'])]
        if r.get('ind_mv') is not None:
            rooms.append(('行业 20%', 0.20 * net - r['ind_mv']))
        if r['cost'] is not None:
            rooms.append(('投入 10%', 0.10 * net - r['cost']))
        if r['net'] > 0 and r['y'] > 0:
            rooms.append(('一成线', (0.10 * T - r['net']) / (0.9 * r['y'])))
        if r['reg'] > 0 and r['y_reg'] > 0:
            rooms.append(('一成线·去掉特别', (0.10 * T_reg - r['reg']) / (0.9 * r['y_reg'])))
        name, room = min(rooms, key=lambda x: x[1])
        tenth = {k2: v2 for k2, v2 in rooms if k2.startswith('一成线')}
        if stop:
            room, name = 0.0, '、'.join(x.split(' ')[0] for x in stop)
        r['tenth'] = tenth
        r['stop'], r['watch'] = stop, watch
        r['room'], r['room_by'] = max(0.0, room), name
        hard = stop or any(x.startswith(('接近一成线', '市值占')) for x in watch)
        r['level'] = 2 if hard else (1 if watch else 0)

    # 一成线的例外（第 4 节）：过线的那只彻底停派，剩下的股息打八折还够当年开支，才可以超过一成。
    over = [r for r in work if any(x.startswith('过一成线') for x in r['stop'])]  # noqa
    for r in over:
        left = T - r['net']
        r['exception'] = {'left': left, 'left80': 0.8 * left, 'ok': bool(a.expense) and 0.8 * left >= a.expense}
    if len(over) > 1:
        left = T - sum(r['net'] for r in over)
        for r in over:
            r['exception_joint'] = {'left': left, 'left80': 0.8 * left, 'ok': bool(a.expense) and 0.8 * left >= a.expense,
                                    'names': '、'.join(x['name'] for x in over)}

    # 同一类风险一起减派
    grp_rows = []
    for g, syms in groups.items():
        rs = [idx[s] for s in syms if s in idx]
        gi = sum(x['net'] for x in rs)
        gr = sum(x['reg'] for x in rs)
        grp_rows.append({'name': g, 'members': [x['name'] for x in rs], 'share': gi / T if T else 0, 'div': gi,
                         'i4': T - 0.4 * gi, 'i5': T - 0.5 * gi, 'r4': T_reg - 0.4 * gr, 'r5': T_reg - 0.5 * gr})
    grp_rows.sort(key=lambda g: g['i5'])

    bench = None
    if a.benchmark and a.benchmark in idx:
        b = idx[a.benchmark]
        bench = {'name': b['name'], 'y': b['y']}
    elif a.benchmark and a.benchmark in quotes:
        q = quotes[a.benchmark]
        bench = {'name': q.get('name', a.benchmark), 'y': float(q.get('dividendPerShareTtm') or 0) / float(q['price'])}

    bond = None
    if a.bond_yield and '@' in a.bond_yield:
        v, dt = a.bond_yield.split('@', 1)
        bond = {'y': float(v) / 100, 'date': dt}

    ctx = dict(a=a, snap=snap, mk=mk, asof=asof, net=net, total_mv=total_mv, margin=margin, interest=interest,
               T_ledger=T_ledger, ttm_diff=ttm_diff, bond=bond,
               rows=rows, work=work, T=T, T_spc=T_spc, T_reg=T_reg, recv_12m=recv_12m, recv_year=recv_year,
               margin_at=margin_at, recent=recent, buys30=buys30, sells30=sells30, quotes=quotes, ytd=ytd,
               ind_rows=ind_rows, grp_rows=grp_rows, caps=caps, bench=bench, notes=notes)
    write_md(ctx)
    if a.html:
        write_html(ctx)
    print('ok', a.out, a.html or '', len(rows), 'holdings')


def write_md(c):
    a, rows = c['a'], c['rows']
    L = []
    w = L.append
    w('# 持仓体检底稿（账本 %s，行情 %s）' % (c['snap']['date'], c['mk'].get('updatedAt', '')[:10]))
    w('')
    w('- 净值 %s，持仓市值 %s，融资 %s' % (wan(c['net']), wan(c['total_mv']), wan(c['margin'])))
    for days in (30, 90):
        dt, m = c['margin_at'](days)
        if dt:
            w('- %d 天前（%s）融资 %s' % (days, dt, wan(m)))
    w('- 最近 30 天买进 %s，卖出 %s' % (wan(c['buys30']), wan(c['sells30'])))
    w('- 股息（按一年算，含特别）%s（账本今天显示 %s）；特别 %s；去掉特别 %s；过去 12 个月实际到账 %s；今年已确认到账 %s' % (
        wan(c['T']), wan(c['T_ledger']), wan(c['T_spc']), wan(c['T_reg']), wan(c['recv_12m']), wan(c['recv_year'])))
    if a.expense:
        w('- 打八折：含特别 %s（对开支 %s），不含特别 %s（%s）' % (
            wan(0.8 * c['T']), wan(0.8 * c['T'] - a.expense, sign=True), wan(0.8 * c['T_reg']), wan(0.8 * c['T_reg'] - a.expense, sign=True)))
    y = c['ytd']
    if y:
        w('- 今年至今：年初 %s → 现在 %s，净存入 %s，资金收益 %s（%s）' % (wan(y['start']), wan(y['end']), wan(y['flow'], sign=True), wan(y['cap'], sign=True), pct(y['rate'])))
        if y.get('split'):
            sp = y['split']
            w('  - %s 以前 %s；以后 %s（这段净存入 %s）' % (sp['opening'], wan(sp['seg1'], sign=True), wan(sp['seg2'], sign=True), wan(sp['flow_after'], sign=True)))
    for g in c['grp_rows']:
        w('- 组 %s（%s）：股息 %s，占 %s；一起减五成剩 含特别 %s / 不含 %s' % (g['name'], '、'.join(g['members']), wan(g['div']), pct(g['share']), wan(g['i5']), wan(g['r5'])))
    for g in c['ind_rows']:
        w('- 行业 %s：%s（%s）' % (g['name'], pct(g['w']), '、'.join(m['name'] for m in g['members'])))
    w('')
    for r in c['work']:
        s = r.get('slice') or {}
        w('## %s（%s）' % (r['name'], r['sym']))
        w('- 市值 %s（%s）；一年到手 %s（含特别，%s），不含特别 %s（%s）；股息占 含特别 %s / 不含 %s' % (
            wan(r['mv']), pct(r['w']), wan(r['net']), pct(r['y']), wan(r['reg']), pct(r['y_reg']), pct(r['share']), pct(r['share_reg'])))
        w('- 状态：%s；规则余量 %s（%s）；投入 %s' % ('、'.join(r['stop'] + r['watch']) or '无', wan(r['room']), r['room_by'],
                                              wan(r['cost']) if r['cost'] is not None else ('账本开始后加了 %s，之前的没补' % wan(r['cost_partial']) if r['cost_partial'] else '没补')))
        if s:
            w('- 你这一份：赚 %s，派 %s，自由现金流 %s，现金 %s，借款 %s，净资产 %s' % (
                wan(s.get('earn')), wan(s.get('paid')), wan(s.get('fcf')), wan(s.get('cash')), wan(s.get('debt')), wan(s.get('equity'))))
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


CSS = r'''
:root{--bg:#faf8f3;--ink:#3b362e;--ink-2:#6b6355;--muted:#a89d86;--hint:#c2b9a6;--gold:#c19a45;--track:#eae4d4;
--up:#bf5a42;--down:#6a8b74;--gold-glow:none;--reg:#8a7d69}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#23201b;--ink:#ece6d8;--ink-2:#b5ad9d;--muted:#8f887a;
--hint:#5f594c;--gold:#d3a84f;--track:#3a352c;--up:#e07a5f;--down:#7fae8e;--gold-glow:0 0 12px rgba(211,168,79,.45);--reg:#b3a68c}}
:root[data-theme="dark"]{--bg:#23201b;--ink:#ece6d8;--ink-2:#b5ad9d;--muted:#8f887a;--hint:#5f594c;--gold:#d3a84f;--track:#3a352c;
--up:#e07a5f;--down:#7fae8e;--gold-glow:0 0 12px rgba(211,168,79,.45);--reg:#b3a68c}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg);color:var(--ink);-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.n{color:var(--ink);font-weight:700}.is-up{color:var(--up)}.is-down{color:var(--down)}.sep{color:var(--hint);margin:0 5px}
.page{width:100%;max-width:430px;margin:0 auto;padding:24px 18px 60px}
.pagebar{display:grid;grid-template-columns:64px 1fr 64px;align-items:center}
.pagebar-left{font-size:12px;font-weight:600;color:var(--ink-2)}
.pagebar-title{text-align:center;font-size:12px;font-weight:600;letter-spacing:5px;padding-left:5px;color:var(--ink-2)}
.pagebar-right{text-align:right;font-size:12px;font-weight:700}
.pagebar-sub{margin-top:8px;text-align:center;font-size:12px;color:var(--ink-2)}
.toc{margin-top:18px;display:flex;justify-content:center;gap:20px;font-size:13px;font-weight:600}
.toc a{color:var(--ink-2);text-decoration:none;padding-bottom:6px}
.sec{margin-top:40px}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.sec-label{font-size:12px;font-weight:600;letter-spacing:3px;color:var(--ink-2)}
.sec-aside{font-size:12px;color:var(--ink-2);text-align:right}
.hero{margin-top:24px;text-align:center}
.hero-label{font-size:12px;font-weight:600;letter-spacing:3px;padding-left:3px;color:var(--ink-2)}
.hero-value{display:block;margin-top:10px;font-size:38px;font-weight:600;letter-spacing:-1px;line-height:1.05}
.hero-value small{font-size:16px;font-weight:600;letter-spacing:0;margin-left:3px;color:var(--ink-2)}
.hero-sub{margin-top:10px;font-size:13px;color:var(--ink-2);line-height:1.65}
.cushion{margin-top:26px;text-align:left;display:flex;flex-direction:column;gap:18px}
.cushion-row{display:flex;flex-direction:column;gap:8px}
.cushion-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px;color:var(--ink-2)}
.c-diff{font-size:12px;font-weight:700;white-space:nowrap}
.ruler{position:relative;height:2px;background:var(--track);margin:5px 0}
.ruler-fill{position:absolute;left:0;top:0;bottom:0;background:var(--gold);box-shadow:var(--gold-glow)}
.ruler-fill.is-reg{background:var(--reg);box-shadow:none}
.ruler-dot{position:absolute;top:50%;transform:translate(-50%,-50%);width:7px;height:7px;border-radius:50%;background:var(--gold);box-shadow:var(--gold-glow)}
.ruler-dot.is-reg{background:var(--ink);box-shadow:none}
.ruler-tick{position:absolute;top:-6px;width:1.5px;height:14px;background:var(--ink)}
.ruler-tick.is-int{background:var(--up)}
.cushion-note{font-size:12px;color:var(--ink-2)}
.ruler-legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--ink-2)}
.legend-item{display:inline-flex;align-items:center;gap:6px}
.tick-swatch{display:inline-block;width:2px;height:11px;background:var(--ink)}
.tick-swatch.is-int{background:var(--up)}
.callout{display:flex;align-items:baseline;gap:8px;font-size:13px;line-height:1.6}
.callout i{width:5px;height:5px;border-radius:50%;background:var(--gold);flex:none;transform:translateY(-2px)}
.callout.is-alert i{background:var(--up)}
.metrics3{margin-top:26px;padding-top:20px;border-top:1px solid var(--track);display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center}
.metrics3>div{display:flex;flex-direction:column;gap:3px}
.metrics3 span,.metrics3 small{font-size:12px;color:var(--ink-2)}
.metrics3 strong{font-size:15px;font-weight:700}
.ytd-head{margin-top:14px;display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.ytd-path{font-size:13px;color:var(--ink-2)}
.ytd-total{font-size:16px;font-weight:700;white-space:nowrap}
.ytd-split{margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.ytd-seg{display:flex;flex-direction:column;gap:5px}
.ytd-seg+.ytd-seg{padding-left:16px;border-left:1px solid var(--track)}
.ytd-seg-label,.ytd-seg-note{font-size:12px;color:var(--ink-2)}
.ytd-seg-val{font-size:16px;font-weight:700}
.ytd-seg-bar{height:2px;background:var(--track);position:relative;margin-top:2px}
.ytd-seg-bar i{position:absolute;left:0;top:0;bottom:0}
.ytd-seg-bar.is-est.dn i{background:repeating-linear-gradient(90deg,var(--down) 0,var(--down) 5px,transparent 5px,transparent 8px)}
.ytd-seg-bar.is-est.up i{background:repeating-linear-gradient(90deg,var(--up) 0,var(--up) 5px,transparent 5px,transparent 8px)}
.ytd-seg-bar.is-rec.up i{background:var(--up)}.ytd-seg-bar.is-rec.dn i{background:var(--down)}
.ytd-foot{margin-top:12px;font-size:12px;color:var(--ink-2);line-height:1.6}
.diag-list{margin-top:20px;display:flex;flex-direction:column;gap:12px;list-style:none}
.diag-list li{position:relative;padding-left:14px;font-size:14px;line-height:1.65}
.diag-list li::before{content:"";position:absolute;left:0;top:10px;width:4px;height:4px;border-radius:50%;background:var(--gold)}
.stack-bar{display:flex;height:2px;margin-top:14px;gap:2px}
.stack-bar i{display:block}.sb-core{background:var(--ink)}.sb-work{background:var(--gold);box-shadow:var(--gold-glow)}.sb-gray{background:var(--hint)}
.struct-rows{margin-top:14px;display:flex;flex-direction:column;gap:12px}
.struct-row{display:grid;grid-template-columns:10px 1fr auto auto;align-items:baseline;gap:8px;font-size:13px;color:var(--ink-2)}
.struct-row b{width:5px;height:5px;border-radius:50%;align-self:center}
.b-core{background:var(--ink)}.b-work{background:var(--gold)}.b-gray{background:var(--hint)}
.sr-name{color:var(--ink);font-weight:700}.sr-meta{font-size:12px;margin-left:4px}
.sr-val{color:var(--ink);font-weight:700;text-align:right}
.sr-div{font-size:12px;color:var(--ink);font-weight:600;text-align:right;min-width:70px}
.para{margin-top:14px;font-size:13px;color:var(--ink-2);line-height:1.65}
.margin-box{margin-top:18px;padding-top:16px;border-top:1px solid var(--track)}
.margin-main{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;color:var(--ink-2)}
.margin-sub{margin-top:6px;font-size:12px;color:var(--ink-2);line-height:1.6}
details.zen-fold{margin-top:12px}
details.zen-fold summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;font-weight:600;padding:4px 0}
details.zen-fold summary::-webkit-details-marker{display:none}
.fold-hint{font-size:12px;color:var(--ink-2);font-weight:600;white-space:nowrap}
details.zen-fold[open] .fold-hint::after{content:" · 收起"}details.zen-fold:not([open]) .fold-hint::after{content:" · 展开"}
.trade-list{margin-top:10px;display:flex;flex-direction:column;gap:8px}
.trade-item{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px;color:var(--ink-2)}
.trade-item strong{color:var(--ink)}
.trade-item em{font-style:normal;font-weight:700}
.stress-list,.ind-list{margin-top:16px;display:flex;flex-direction:column;gap:22px}
.ind-list{gap:14px}
.stress-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.stress-name{font-size:14px;font-weight:700}
.stress-members{margin-top:2px;font-size:12px;color:var(--ink-2)}
.stress-val{font-size:13px;font-weight:700;white-space:nowrap}
.track{position:relative;height:2px;background:var(--track);margin:12px 0 10px}
.track .bar{position:absolute;left:0;top:0;bottom:0;background:var(--gold);box-shadow:var(--gold-glow)}
.track .bar.is-reg{background:var(--reg);box-shadow:none}
.track .bar.is-cost{background:var(--ink);box-shadow:none;opacity:.55}
.track .mark{position:absolute;top:-5px;width:1.5px;height:12px;background:var(--ink)}
.stress-sub{font-size:12px;color:var(--ink-2);line-height:1.65}
.ind-row .stress-head{font-size:13px}.ind-row b{font-weight:700}.ind-mem{margin-left:6px;font-size:12px;color:var(--ink-2)}
.track.thin{margin:6px 0 0}
.work-notice{margin-top:10px;font-size:13px;color:var(--ink-2);line-height:1.6}
.stock-entry{margin-top:26px;padding-top:24px;border-top:1px solid var(--track)}
.st-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.st-title h3{font-size:17px;font-weight:700}
.st-title p{margin-top:3px;font-size:12px;color:var(--ink-2)}
.st-pos{text-align:right;white-space:nowrap}
.st-pos strong{display:block;font-size:16px;font-weight:700}
.st-pos small{font-size:12px;color:var(--ink-2)}
.st-status{margin-top:12px;display:flex;flex-direction:column;gap:6px;font-size:12px}
.st-flags{display:flex;flex-wrap:wrap;gap:6px 14px}
.flag{display:inline-flex;align-items:center;gap:6px;font-weight:700}
.flag i{width:5px;height:5px;border-radius:50%;flex:none}
.flag.is-stop{color:var(--up)}.flag.is-stop i{background:var(--up)}.flag.is-watch i{background:var(--gold)}
.st-quota{color:var(--ink-2);line-height:1.5}
.share-grid{margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}
.sg-cell{display:flex;flex-direction:column;gap:2px;min-width:0}
.sg-label,.sg-sub{font-size:12px;color:var(--ink-2)}.sg-sub{line-height:1.5}
.sg-val{font-size:15px;font-weight:700}
.st-meta{margin-top:14px;display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--ink-2);line-height:1.6}
.st-meta strong{color:var(--ink);font-weight:700}
.st-note{margin-top:12px;font-size:14px;line-height:1.65}
.st-note b{color:var(--ink-2);margin-right:4px}
.quiet-block{margin-top:28px;padding-top:20px;border-top:1px solid var(--track)}
.quiet-rows{margin-top:12px;display:flex;flex-direction:column;gap:10px}
.quiet-row{display:grid;grid-template-columns:1.4fr .9fr .8fr .9fr;align-items:baseline;gap:6px;font-size:12px;color:var(--ink-2)}
.quiet-row.is-head{padding-bottom:6px;border-bottom:1px solid var(--track)}
.quiet-row strong{color:var(--ink)}.quiet-row .r{text-align:right}
.foot{margin-top:44px;padding-top:20px;border-top:1px solid var(--track);font-size:12px;color:var(--ink-2);line-height:1.8}
.toc a.is-active{color:var(--ink);font-weight:700;position:relative}
.toc a.is-active::after{content:"";position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:4px;height:4px;border-radius:50%;background:var(--gold)}
.flag{align-items:flex-start}.flag i{margin-top:6px}
.dot-alert{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--up);margin-right:5px;vertical-align:middle}
.c-diff{color:var(--ink)}
.stress-val b.bad{font-weight:700}
.track.reg{margin-top:-4px}
.diag-list li.is-alert::before{background:var(--up)}
.ytd-seg-label{white-space:nowrap}
.st-line{margin-top:10px;font-size:13px;color:var(--ink-2);line-height:1.6}
.stock-entry details.zen-fold .share-grid{margin-top:10px}
'''


def write_html(c):
    e = html.escape
    a, net, T, T_reg, T_spc = c['a'], c['net'], c['T'], c['T_reg'], c['T_spc']
    exp = a.expense or 0
    intr = c['interest']
    H = []
    h = H.append

    def n(x):
        return '<span class="n">%s</span>' % e(x)

    def color(x):
        return 'is-up' if x > 0 else ('is-down' if x < 0 else '')

    def gap_text(val, need):
        d = val - need
        if d >= 0:
            return '<span class="c-diff">比开支多 %s</span>' % e(wan(d))
        return '<span class="c-diff"><i class="dot-alert"></i>比开支少 %s</span>' % e(wan(-d))

    work = c['work']
    n_stop = sum(1 for r in work if any(x.startswith('停手') for x in r['stop']))
    n_over = sum(1 for r in work if any(x.startswith('过一成线') for x in r['stop']))
    h('<header class="pagebar"><span class="pagebar-left">%s</span><span class="pagebar-title">持仓体检</span>'
      '<span class="pagebar-right">%s</span></header>' % (e(c['snap']['date'][5:]), e(wan(net, 0))))
    h('<p class="pagebar-sub">税后 · 万元人民币 · 账本 %s · 行情 %s</p>' % (e(c['snap']['date']), e(c['mk'].get('updatedAt', '')[:10])))
    h('<nav class="toc"><a href="#hero" class="is-active">安全垫</a><a href="#ytd">今年</a><a href="#stress">压力</a><a href="#work">打工仓</a></nav>')

    # 1. 安全垫
    h('<section id="hero" class="hero"><span class="hero-label">一年税后股息</span>')
    h('<strong class="hero-value">%s<small>万</small></strong>' % e('%.1f' % (T / 10000)))
    sub = '去掉特别股息 %s（特别 %s）<br>过去 12 个月实际到账 %s' % (n(wan(T_reg)), n(wan(T_spc)), n(wan(c['recv_12m'])))
    if c['ttm_diff']:
        sub += '<br>账本今天显示 %s：%s那次派息正好满 12 个月，账本还算在里面，这里按一年只算一次' % (
            n(wan(c['T_ledger'])), e('、'.join(r['name'] for r in c['ttm_diff'])))
    h('<p class="hero-sub">%s</p>' % sub)
    if exp:
        need = exp + (intr or 0)
        scale = max(0.8 * T, 0.8 * T_reg, need) * 1.15
        h('<div class="cushion">')
        for label, val, cls in (('含特别股息打八折', 0.8 * T, ''), ('去掉特别股息打八折', 0.8 * T_reg, ' is-reg')):
            h('<div class="cushion-row"><div class="cushion-top"><span>%s %s</span>%s</div>' % (label, n(wan(val)), gap_text(val, exp)))
            h('<div class="ruler"><div class="ruler-fill%s" style="width:%.1f%%"></div><div class="ruler-tick" style="left:%.1f%%"></div>%s'
              '<div class="ruler-dot%s" style="left:%.1f%%"></div></div>' % (
                  cls, val / scale * 100, exp / scale * 100,
                  ('<div class="ruler-tick is-int" style="left:%.1f%%"></div>' % (need / scale * 100)) if intr else '', cls, val / scale * 100))
            if intr:
                h('<p class="cushion-note">算上一年融资利息 %s：%s</p>' % (n(wan(intr)), ('还多 %s' % wan(val - need)) if val >= need else ('差 %s' % wan(need - val))))
            h('</div>')
        h('<div class="ruler-legend"><span class="legend-item"><i class="tick-swatch"></i>当年开支 %s</span>%s</div>' % (
            n(wan(exp)), ('<span class="legend-item"><i class="tick-swatch is-int"></i>开支 + 融资利息 %s</span>' % n(wan(need))) if intr else ''))
        ok_i, ok_r = 0.8 * T >= exp, 0.8 * T_reg >= exp
        if ok_i != ok_r:
            t = '两种算法结论不同：含特别股息打八折%s，去掉特别股息%s。' % ('够开支' if ok_i else '不够', '够' if ok_r else '不够')
        else:
            t = '两种算法结论一样：打八折%s开支。' % ('够' if ok_i else '不够')
        if not intr:
            t += '融资利率没给，利息没算进来。'
        h('<p class="callout%s"><i></i><span>%s</span></p>' % ('' if ok_i and ok_r else ' is-alert', e(t)))
        if a.reserve and not (ok_i and ok_r):
            gi, gr = max(0.0, exp - 0.8 * T), max(0.0, exp - 0.8 * T_reg)
            yrs = lambda g: ('%.0f 年' % (a.reserve / g)) if g > 0 else '不用动'
            h('<p class="cushion-note">差额每年 %s（含特别）/ %s（去掉特别）。备用金目标 %s，2028 年初前攒齐；攒齐后顶得住 %s / %s。现在攒了多少，账本里没有。</p>' % (
                n(wan(gi)), n(wan(gr)), n(wan(a.reserve)), e(yrs(gi)), e(yrs(gr))))
        h('</div>')
    m30 = c['margin_at'](30)
    h('<div class="metrics3"><div><span>组合净值</span><strong>%s</strong><small>持仓 %s</small></div>' % (e(wan(net)), e(wan(c['total_mv']))))
    h('<div><span>券商融资</span><strong>%s</strong><small>%s</small></div>' % (
        e(wan(c['margin'])), e('30 天 %s' % wan(c['margin'] - m30[1], sign=True)) if m30[0] else ''))
    h('<div><span>打工仓规矩</span><strong>%d 停手 · %d 过线</strong><small>余量看每只</small></div></div>' % (n_stop, n_over))
    h('</section>')

    # 2. 今年至今
    y = c['ytd']
    h('<section id="ytd" class="sec"><div class="sec-head"><span class="sec-label">今年至今</span><span class="sec-aside">账本的算法</span></div>')
    if y:
        h('<div class="ytd-head"><span class="ytd-path">年初 %s → 现在 %s</span><span class="ytd-total %s">%s · %s</span></div>' % (
            n(wan(y['start'])), n(wan(y['end'])), color(y['cap']), e(wan(y['cap'], sign=True)), e(pct(y['rate']))))
        sp = y.get('split')
        if sp:
            big = max(abs(sp['seg1']), abs(sp['seg2'])) or 1
            od = '%d/%d' % (int(sp['opening'][5:7]), int(sp['opening'][8:10]))
            h('<div class="ytd-split">')
            h('<div class="ytd-seg"><span class="ytd-seg-label">1/1–%s（估算）</span><strong class="ytd-seg-val %s">%s</strong>'
              '<div class="ytd-seg-bar is-est %s"><i style="width:%.0f%%"></i></div><span class="ytd-seg-note">没有出入金记录</span></div>' % (
                  e(od), color(sp['seg1']), e(wan(sp['seg1'], sign=True)), 'up' if sp['seg1'] > 0 else 'dn', abs(sp['seg1']) / big * 100))
            h('<div class="ytd-seg"><span class="ytd-seg-label">%s 至今（有记录）</span><strong class="ytd-seg-val %s">%s</strong>'
              '<div class="ytd-seg-bar is-rec %s"><i style="width:%.0f%%"></i></div><span class="ytd-seg-note">已扣净存入 %s</span></div>' % (
                  e(od), color(sp['seg2']), e(wan(sp['seg2'], sign=True)), 'up' if sp['seg2'] > 0 else 'dn', abs(sp['seg2']) / big * 100,
                  e(wan(sp['flow_after'], sign=True))))
            h('</div>')
        h('<p class="ytd-foot">资金收益 = 现在 − 年初 − 今年净存入，含股息和汇率。今年已确认到账的股息 %s<span class="sep">·</span>历年的收益和归因看账本的年度回顾页。</p>' % n(wan(c['recv_year'])))
    else:
        h('<p class="ytd-foot">账本里没有上年末的净值，算不了今年至今。</p>')
    summ = c['notes'].get('_summary') or []
    if summ:
        items = []
        for s_ in summ:
            if isinstance(s_, dict):
                items.append('<li%s>%s</li>' % (' class="is-alert"' if s_.get('alert') else '', e(s_.get('text', ''))))
            else:
                items.append('<li>%s</li>' % e(s_))
        h('<ul class="diag-list">%s</ul>' % ''.join(items))
    h('</section>')

    # 3. 结构与融资
    by = {}
    for r in c['rows']:
        b = by.setdefault(r['kind'], {'mv': 0.0, 'div': 0.0, 'cnt': 0})
        b['mv'] += r['mv']
        b['div'] += r['net']
        b['cnt'] += 1
    h('<section class="sec"><div class="sec-head"><span class="sec-label">结构与融资</span><span class="sec-aside">占比按净值 %s</span></div>' % e(wan(net)))
    tot = sum(b['mv'] for b in by.values()) or 1
    h('<div class="stack-bar">%s</div>' % ''.join('<i class="%s" style="width:%.1f%%"></i>' % (cls, by[k]['mv'] / tot * 100)
                                                for k, cls in (('核心仓', 'sb-core'), ('打工仓', 'sb-work'), ('灰色地带', 'sb-gray')) if k in by))
    h('<div class="struct-rows">')
    for k, cls in (('核心仓', 'b-core'), ('打工仓', 'b-work'), ('灰色地带', 'b-gray')):
        if k in by:
            b = by[k]
            h('<div class="struct-row"><b class="%s"></b><div><span class="sr-name">%s</span><span class="sr-meta">%d 只 · %s</span></div>'
              '<span class="sr-val">%s</span><span class="sr-div">息 %s</span></div>' % (cls, k, b['cnt'], pct(b['mv'] / net), e(wan(b['mv'])), e(wan(b['div']))))
    h('</div>')
    if c['margin'] > 0:
        h('<p class="para">占比的分母是净值（持仓市值减融资），所以三块加起来超过 100%%。按持仓市值 %s 算，单只上限会松 %s。</p>' % (
            e(wan(c['total_mv'])), e(wan(0.10 * (c['total_mv'] - net)))))
    core = [r for r in c['rows'] if r['sym'] in c['caps']]
    if core or c['bench']:
        parts = ['%s %s（买入上限 %g%%）' % (e(r['name']), pct(r['w']), c['caps'][r['sym']]) for r in core]
        t = ('核心仓：' + '，'.join(parts) + '。买入上限是回报最高时的上限，不是持有上限。') if parts else ''
        if c['bench']:
            t += '参照：%s 现在税后股息率 %s，打工仓要明显高于它' % (e(c['bench']['name']), pct(c['bench']['y']))
            if c['bond']:
                t += '；它自己的底线是股东回报高于国债，10 年期国债 %s（%s）' % (pct(c['bond']['y'], 2), e(c['bond']['date']))
            t += '。'
        h('<p class="para">%s</p>' % t)
    gray = [r for r in c['rows'] if r['kind'] == '灰色地带']
    if gray:
        h('<p class="para">灰色地带（慢慢卖）：%s，合计 %s。</p>' % ('，'.join('%s %s' % (e(r['name']), e(wan(r['mv']))) for r in gray), e(wan(sum(r['mv'] for r in gray)))))
    h('<div class="margin-box"><div class="margin-main"><span>融资</span><span class="n">%s</span></div>' % e(
        ' → '.join(wan(m) for m in [c['margin_at'](90)[1], c['margin_at'](30)[1], c['margin']] if m is not None)))
    h('<p class="margin-sub">90 天前 → 30 天前 → 现在。%s账本现金是各账户合起来的数，港股成交后两天才交收；融资余额以券商为准。</p>' % (
        ('年利率 %.2f%%，一年利息约 %s。' % (a.margin_rate * 100, wan(intr))) if intr else '利率没给，不估利息。'))
    if c['recent']:
        bs = c['buys30'] - c['sells30']
        h('<details class="zen-fold"><summary><span>最近 30 天买卖 %d 笔 · 买进比卖出%s %s</span><span class="fold-hint">查看</span></summary><div class="trade-list">' % (
            len(c['recent']), '多' if bs >= 0 else '少', e(wan(abs(bs)))))
        for t in sorted(c['recent'], key=lambda t: t['date'], reverse=True):
            v = float(t['shares']) * float(t['price']) * float(t.get('fxRate') or 1)
            h('<div class="trade-item"><span>%s <em>%s</em> <strong>%s</strong></span><strong>%s</strong></div>' % (
                e(t['date'][5:10]), '买' if t['side'] == 'buy' else '卖', e(c['quotes'].get(t['symbol'], {}).get('name', t['symbol'])), e(wan(v))))
        h('</div></details>')
    h('</div></section>')

    # 4. 同类风险一起减派 + 行业
    h('<section id="stress" class="sec"><div class="sec-head"><span class="sec-label">同一类风险一起减派</span><span class="sec-aside">和八折分开算 · 竖线是开支</span></div>')
    if c['grp_rows'] and exp:
        h('<div class="stress-list">')
        scale = T * 1.05
        for g in c['grp_rows']:
            def mark(v):
                return ('<b class="bad"><i class="dot-alert"></i>%s</b>' % e(wan(v, 1))) if v < exp else e(wan(v, 1))
            h('<div><div class="stress-head"><div><div class="stress-name">%s</div><div class="stress-members">%s · 股息 %s，占 %s</div></div>'
              '<span class="stress-val">减半剩 %s / %s</span></div>' % (
                  e(g['name']), e('、'.join(g['members'])), n(wan(g['div'])), pct(g['share'], 0), mark(g['i5']), mark(g['r5'])))
            h('<div class="track"><div class="bar" style="width:%.1f%%"></div><div class="mark" style="left:%.1f%%"></div></div>'
              '<div class="track reg"><div class="bar is-reg" style="width:%.1f%%"></div><div class="mark" style="left:%.1f%%"></div></div>' % (
                  g['i5'] / scale * 100, exp / scale * 100, g['r5'] / scale * 100, exp / scale * 100))
            h('<p class="stress-sub">上面含特别股息：减四成剩 %s，减五成剩 %s（%s）<br>下面去掉特别：减五成剩 %s（%s）</p>' % (
                n(wan(g['i4'])), n(wan(g['i5'])), '够开支' if g['i5'] >= exp else '比开支少 %s' % wan(exp - g['i5']),
                n(wan(g['r5'])), '够开支' if g['r5'] >= exp else '比开支少 %s' % wan(exp - g['r5'])))
            h('</div>')
        h('</div>')
    if c['ind_rows']:
        h('<div class="sec-head" style="margin-top:32px"><span class="sec-label">行业上限</span><span class="sec-aside">打工仓 · 按市值 · 20%</span></div><div class="ind-list">')
        for g in c['ind_rows']:
            ct = (' · 按投入 %s' % pct(g['cost'] / net)) if g['cost_known'] else ''
            h('<div class="ind-row"><div class="stress-head"><span><b>%s</b><span class="ind-mem">%s</span></span><span class="stress-val">%s%s</span></div>'
              '<div class="track thin"><div class="bar" style="width:%.1f%%"></div></div></div>' % (
                  e(g['name']), e('、'.join(m['name'] for m in g['members'])), pct(g['w']), e(ct), min(100.0, g['w'] / 0.20 * 100)))
        h('</div>')
    h('</section>')

    # 5. 打工仓每只：硬状态展开，要留意的收起细节，没问题的折成账单
    hard = sorted([r for r in work if r['level'] == 2], key=lambda r: (-len(r['stop']), -r['share']))
    soft = sorted([r for r in work if r['level'] == 1], key=lambda r: -r['share'])
    quiet = sorted([r for r in work if r['level'] == 0], key=lambda r: r['y_reg'])
    no_cost = [r for r in work if r['cost'] is None]
    h('<section id="work" class="sec"><div class="sec-head"><span class="sec-label">打工仓每只</span><span class="sec-aside">%d 只 · 单只上限 %s</span></div>' % (len(work), e(wan(0.10 * net))))
    h('<p class="work-notice">停手和过线、近线的 %d 只 · 要留意的 %d 只 · 没问题的 %d 只。金额都折成“你这一份”：按持股比例，公司一年赚的、分的、欠的、有的，算到你头上是多少。%s</p>' % (
        len(hard), len(soft), len(quiet),
        ('<strong style="color:var(--ink)">现在有融资 %s，新买要先卖后买（第 6 节）。</strong>' % e(wan(c['margin']))) if c['margin'] > 0 else ''))
    h('<p class="work-notice">管我财那条按投入算的上限：账本开始以后买的才有成本，现在只有 %s 算得出来，其余 %d 只成本没补。买入理由还没开始记（第 7 节）。</p>' % (
        e('、'.join(r['name'] for r in work if r['cost'] is not None) or '没有一只'), len(no_cost)))
    for r in hard:
        h(stock_entry(r, net, e, n))
    for r in soft:
        h(stock_entry(r, net, e, n, compact=True))
    if quiet:
        h('<div class="quiet-block"><details class="zen-fold"><summary><span>规矩上没有要留意的 %d 只</span><span class="fold-hint">%s</span></summary>' % (
            len(quiet), e(wan(sum(r['mv'] for r in quiet)))))
        h('<div class="quiet-rows"><div class="quiet-row is-head"><span>公司</span><span class="r">持仓</span><span class="r">常规税后</span><span class="r">一年到手</span></div>')
        for r in quiet:
            h('<div class="quiet-row"><strong>%s</strong><span class="r">%s</span><span class="r">%s</span><span class="r">%s</span></div>' % (
                e(r['name']), e(wan(r['mv'])), pct(r['y_reg']), e(wan(r['net']))))
        h('</div>')
        for r in quiet:
            h(stock_entry(r, net, e, n, compact=True))
        h('</details></div>')
    h('</section>')

    h('<footer class="foot">财报数来自东方财富的年报和中报，“你这一份”按 净利润 ÷ 每股收益 估的总股本折算。'
      '借款指银行借款和债券，不含租赁和应付款；现金含存款，理财单列。派息按公司公布的每一期核对过。只摆事实和算术，不是买卖建议，取舍由你定。</footer>')

    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<title>持仓体检</title><style>' + CSS + '</style></head><body><main class="page">' + '\n'.join(H) + '</main></body></html>')
    with open(a.html, 'w', encoding='utf-8') as f:
        f.write(page)


def stock_entry(r, net, e, n, compact=False):
    note = r['note']
    s = r.get('slice') or {}
    H = []
    h = H.append
    tax = '免税' if r['tax'] == 0 else '税 %g%%' % (r['tax'] * 100)
    h('<article class="stock-entry"><div class="st-head"><div class="st-title"><h3>%s</h3><p>%s · %s</p></div>'
      '<div class="st-pos"><strong>%s</strong><small>占净值 %s</small></div></div>' % (
          e(r['name']), e(note.get('type') or r['industry'] or ''), e(tax), e(wan(r['mv'])), pct(r['w'])))
    flags = ''.join('<span class="flag is-stop"><i></i>%s</span>' % e(x) for x in r['stop']) + \
        ''.join('<span class="flag is-watch"><i></i>%s</span>' % e(x) for x in r['watch'])
    lines = []
    if note.get('recover') and note.get('status') == 'cut':
        lines.append('恢复要：%s' % e(note['recover']))
    if r['room_by'].startswith('一成线') and len(r.get('tenth') or {}) == 2:
        t1 = max(0.0, r['tenth'].get('一成线', 0))
        t2 = max(0.0, r['tenth'].get('一成线·去掉特别', 0))
        room = '规则余量约 %s（一成线：含特别约 %s，去掉特别约 %s，静态约数）' % (n(wan(r['room'])), e(wan(t1)), e(wan(t2)))
    else:
        room = '规则余量%s %s（%s）' % ('约' if r['room_by'].startswith('一成线') else '', n(wan(r['room'])), e(r['room_by']))
    if r['cost'] is not None:
        room += e(' · 投入 %s' % wan(r['cost']))
    lines.append(room)
    h('<div class="st-status">%s%s</div>' % (('<div class="st-flags">%s</div>' % flags) if flags else '',
                                             ''.join('<span class="st-quota">%s</span>' % x for x in lines)))
    # 一年到手股息那一行
    sub = '税后 %s' % pct(r['y'])
    if r['spc'] > 0:
        sub = '含特别 %s · 去掉特别 %s（%s）' % (pct(r['y']), wan(r['reg']), pct(r['y_reg']))
    if r.get('run'):
        sub += ' · 按最近一年度 %s' % pct(r['run'] / r['mv'])
    if r.get('restore'):
        sub += ' · 恢复后约 %s（%s）' % (wan(r['restore']), pct(r['restore'] / r['mv']))
    if r.get('bb'):
        sub += ' · 回购注销另加约 %s' % pct(r['bb'])
    cells = [('一年到手股息', wan(r['net']), sub)]
    if s:
        fy = (s.get('fy') or '')[:4]
        es = []
        if s.get('earn_prev') is not None and s.get('earn') is not None:
            es.append('前一年 %s' % wan(s['earn_prev']))
        if s.get('fcf') is not None:
            es.append('自由现金流 %s' % wan(s['fcf']))
        if s.get('recv_div') and s.get('earn') and s['recv_div'] >= 0.1 * abs(s['earn']):
            es.append('另有投资收益到账 %s' % wan(s['recv_div']))
        cells.append(('%s 年赚（你那份）' % fy, wan(s.get('earn')), ' · '.join(es)))
        nc = (s.get('cash') or 0) - (s.get('debt') or 0)
        cash_txt = '现金 %s' % wan(s.get('cash_only'))
        if (s.get('fin') or 0) >= 50:
            cash_txt += '，理财 %s' % wan(s['fin'])
        debt_txt = ('借款 %s' % wan(s.get('debt'))) if (s.get('debt') or 0) >= 50 else '没有借款'
        cells.append(('净现金（你那份）' if nc >= 0 else '净负债（你那份）', wan(abs(nc)),
                      '%s · %s · 截至 %s' % (cash_txt, debt_txt, (s.get('bal_date') or '')[:7])))
        eq = s.get('equity')
        cells.append(('净资产（你那份）', wan(eq), ('你花 %s 买到的' % wan(r['mv'])) if eq else ''))
    elif note.get('industry') != '指数基金':
        cells.append(('财报数', '没拉', '体检时补'))
    grid = '<div class="share-grid">%s</div>' % ''.join(
        '<div class="sg-cell"><span class="sg-label">%s</span><span class="sg-val">%s</span><span class="sg-sub">%s</span></div>' % (e(a), e(b), e(c)) for a, b, c in cells)
    keep, fold = [], []
    if r.get('exception'):
        x = r['exception']
        keep.append('<div><strong>一成线的例外：</strong>这一只彻底停派，剩下 %s，打八折 %s，%s当年开支，所以%s。</div>' % (
            e(wan(x['left'])), e(wan(x['left80'])), '够' if x['ok'] else '不够', '例外成立' if x['ok'] else '例外不成立'))
    if r.get('exception_joint'):
        x = r['exception_joint']
        keep.append('<div><strong>几只一起算：</strong>%s一起停派，剩下 %s，打八折 %s，%s开支。</div>' % (
            e(x['names']), e(wan(x['left'])), e(wan(x['left80'])), '够' if x['ok'] else '不够'))
    if r.get('extra'):
        x = r['extra']
        keep.append('<div><strong>%s：</strong>%s（你那份）%s</div>' % (e(x['label']), e(wan(x['val'])), e((' · ' + x['sub']) if x['sub'] else '')))
    if note.get('recover') and note.get('status') == 'mixed':
        keep.append('<div><strong>恢复：</strong>%s</div>' % e(note['recover']))
    if note.get('special'):
        sp = note['special']
        keep.append('<div><strong>特别股息：</strong>%s，%s</div>' % (e(sp.get('judgment', '')), e(sp.get('reason', ''))))
    if note.get('buyback'):
        keep.append('<div><strong>回购：</strong>%s</div>' % e(note['buyback'].get('note', '')))
    if note.get('user_view'):
        keep.append('<div><strong>你的判断：</strong>%s。规矩上的停手不因此取消，等公告。</div>' % e(note['user_view']))
    if note.get('industry_change'):
        keep.append('<div><strong>行业：</strong>归“%s”。%s</div>' % (e(note.get('industry', '')), e(note['industry_change'])))
    ex = note.get('extra') or {}
    if ex.get('receivables') and s.get('recv_g') is not None:
        g = s['recv_g']
        if s.get('rev_same_g') is not None:
            rv = '，同一时段营收%s %s' % ('涨' if s['rev_same_g'] >= 0 else '降', pct(abs(s['rev_same_g']), 0))
        elif s.get('rev_g') is not None:
            rv = '，上一财年营收%s %s' % ('涨' if s['rev_g'] >= 0 else '降', pct(abs(s['rev_g']), 0))
        else:
            rv = ''
        keep.append('<div><strong>应收账款：</strong>比一年前%s %s%s</div>' % ('涨' if g >= 0 else '降', e(pct(abs(g), 0)), e(rv)))
    if ex.get('advances') and s.get('advances') is not None and s.get('cash_only'):
        keep.append('<div><strong>预收的钱：</strong>账上现金约 %s 是预收款（主要是业主预交的物业费），占现金的 %s</div>' % (
            e(wan(s['advances'])), e(pct(s['advances'] / s['cash_only'], 0))))
    for p in note.get('periods') or []:
        fold.append('<div><strong>%s：</strong>%s</div>' % (e(p[0]), e(p[1])))
    if r.get('run') and note.get('runrate_note'):
        fold.append('<div><strong>最近一年度：</strong>%s</div>' % e(note['runrate_note']))
    if r.get('restore') and note.get('restore_note'):
        fold.append('<div><strong>恢复后：</strong>%s</div>' % e(note['restore_note']))
    if note.get('context'):
        fold.append('<div><strong>公司说：</strong>%s</div>' % e(note['context']))
    if note.get('next_check'):
        fold.append('<div><strong>下次核对：</strong>%s</div>' % e(note['next_check']))
    if compact:
        if note.get('note'):
            h('<p class="st-note"><b>一句话</b>%s</p>' % e(note['note']))
        h('<p class="st-line">一年到手 %s · %s</p>' % (n(wan(r['net'])), e(sub)))
        body = grid + (('<div class="st-meta">%s</div>' % ''.join(keep)) if keep else '') + (('<div class="st-meta">%s</div>' % ''.join(fold)) if fold else '')
        h('<details class="zen-fold"><summary><span>你这一份和细节</span><span class="fold-hint"></span></summary>%s</details>' % body)
    else:
        h(grid)
        if note.get('note'):
            h('<p class="st-note"><b>一句话</b>%s</p>' % e(note['note']))
        if keep:
            h('<div class="st-meta">%s</div>' % ''.join(keep))
        if fold:
            h('<details class="zen-fold"><summary><span>派息记录和细节</span><span class="fold-hint"></span></summary><div class="st-meta">%s</div></details>' % ''.join(fold))
    h('</article>')
    return ''.join(H)


if __name__ == '__main__':
    main()
