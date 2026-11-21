#!/usr/bin/env python3
"""把 fetch_facts.mjs 拉下来的东方财富财报整理成 facts.json，给 checkup.py 用。

  python -I facts.py <原始文件目录> <输出 facts.json>

每家公司一项（金额是元，财报币种；币种以 fundamentals.json 的 statementCurrency 为准）：
- 最近一个财年：营收、净利润（归属股东）、每股收益、经营现金流、资本开支、派息、收到的分红、权益法收益，
  以及前一年的营收和净利润；
- 最近一期资产负债表：现金和存款、流动的金融资产、有息借款（银行借款和债券，不含租赁）、租赁负债、
  净资产（归属股东）、应收账款和一年前同一期的应收账款、预收款（合同负债，物业公司多是业主预交的物业费），
  以及同一时段营收比一年前涨了多少（港股按中期）；
- shares_est：净利润 ÷ 每股收益，估的总股本（A+H 都算），“你这一份”按它折。
港股的“年报”按 DATE_TYPE_CODE 001 认，所以六月年结的公司也对；资产负债表只用年报和中报（001、002），
季报（003、004）的科目口径不稳，比如会把长期借款归到“长期应付款”。
"""
import glob
import json
import os
import sys


def rows_of(path):
    j = json.loads(open(path, encoding='utf-8').read())
    return (j.get('result') or {}).get('data') or []


def table(rows):
    t, kind = {}, {}
    for r in rows:
        dt = r['REPORT_DATE'][:10]
        t.setdefault(dt, {})[r['STD_ITEM_NAME']] = r['AMOUNT']
        kind[dt] = r.get('DATE_TYPE_CODE')
    return t, kind


def g(row, *names):
    vals = [float(row[n]) for n in names if row.get(n) is not None]
    return sum(vals) if vals else None


def hk(raw, code):
    bal, bk = table(rows_of('%s/%s-bal.txt' % (raw, code)))
    bal = {dt: v for dt, v in bal.items() if bk.get(dt) in ('001', '002')} or bal
    cf, _ = table(rows_of('%s/%s-cf.txt' % (raw, code)))
    inc, ik = table(rows_of('%s/%s-inc.txt' % (raw, code)))
    annual = sorted([dt for dt, k in ik.items() if k == '001'], reverse=True)
    out = {}
    if annual:
        fy = annual[0]
        ir, cr = inc.get(fy, {}), cf.get(fy, {})
        out.update({
            'fy': fy, 'revenue': g(ir, '营业额'), 'net_profit': g(ir, '股东应占溢利'), 'eps': g(ir, '每股基本盈利'),
            'assoc_jv_profit': g(ir, '应占联营公司溢利', '应占合营公司溢利'),
            'ocf': g(cr, '经营业务现金净额'), 'capex': g(cr, '购建固定资产', '购建无形资产及其他资产'),
            'dividends_paid': g(cr, '已付股息(融资)'), 'dividends_received': g(cr, '已收股息(投资)'),
        })
        if len(annual) > 1:
            out['revenue_prev'] = g(inc[annual[1]], '营业额')
            out['net_profit_prev'] = g(inc[annual[1]], '股东应占溢利')
    if bal:
        last = max(bal)
        br = bal[last]
        out.update({
            'balance_date': last,
            'cash': g(br, '现金及等价物', '短期存款', '中长期存款'),
            'fin_assets': g(br, '指定以公允价值记账之金融资产(流动)', '交易性金融资产(流动)', '其他金融资产(流动)'),
            'debt': g(br, '短期贷款', '长期贷款') or 0.0,
            'leases': g(br, '融资租赁负债(流动)', '融资租赁负债(非流动)'),
            'equity': g(br, '股东权益'), 'receivables': g(br, '应收帐款'),
            'advances': g(br, '合同负债', '预收款项'),
        })
        # 和最新一期同一时段的营收（港股中期是上半年），一年前比
        prev_same = last[:4] and ('%d%s' % (int(last[:4]) - 1, last[4:]))
        if inc.get(last) and inc.get(prev_same):
            r_now, r_prev = g(inc[last], '营业额'), g(inc[prev_same], '营业额')
            if r_now and r_prev:
                out['revenue_same_g'] = r_now / r_prev - 1
        same = [dt for dt in bal if dt[5:] == last[5:] and dt < last]
        if same:
            out['receivables_1y_ago'] = g(bal[max(same)], '应收帐款')
    return out


def a_share(raw, code):
    def load(kind):
        return json.loads(open('%s/%s-%s.txt' % (raw, code, kind), encoding='utf-8').read()).get('data') or []
    lrb = sorted(load('lrb'), key=lambda r: r['REPORT_DATE'], reverse=True)
    xj = sorted(load('xjll'), key=lambda r: r['REPORT_DATE'], reverse=True)
    zc = sorted(load('zcfz'), key=lambda r: r['REPORT_DATE'], reverse=True)

    def f(r, *ks):
        vals = [float(r[k]) for k in ks if r.get(k) is not None]
        return sum(vals) if vals else None
    out = {}
    if lrb:
        y = lrb[0]
        out.update({'fy': y['REPORT_DATE'][:10], 'revenue': f(y, 'TOTAL_OPERATE_INCOME'), 'net_profit': f(y, 'PARENT_NETPROFIT'),
                    'eps': f(y, 'BASIC_EPS'), 'invest_income': f(y, 'INVEST_INCOME'), 'assoc_jv_profit': f(y, 'INVEST_JOINT_INCOME')})
        if len(lrb) > 1:
            out['revenue_prev'] = f(lrb[1], 'TOTAL_OPERATE_INCOME')
            out['net_profit_prev'] = f(lrb[1], 'PARENT_NETPROFIT')
    if xj:
        x = xj[0]
        out.update({'ocf': f(x, 'NETCASH_OPERATE'), 'capex': f(x, 'CONSTRUCT_LONG_ASSET'),
                    'dividends_paid': f(x, 'ASSIGN_DIVIDEND_PORFIT'), 'dividends_received': f(x, 'RECEIVE_INVEST_INCOME')})
    if zc:
        b = zc[0]
        out.update({'balance_date': b['REPORT_DATE'][:10], 'cash': f(b, 'MONETARYFUNDS'),
                    'fin_assets': f(b, 'TRADE_FINASSET_NOTFVTPL', 'TRADE_FINASSET'),
                    'debt': f(b, 'SHORT_LOAN', 'LONG_LOAN', 'BOND_PAYABLE', 'NONCURRENT_LIAB_1YEAR') or 0.0,
                    'equity': f(b, 'TOTAL_PARENT_EQUITY'), 'receivables': f(b, 'ACCOUNTS_RECE'),
                    'long_equity_invest': f(b, 'LONG_EQUITY_INVEST'), 'other_equity_invest': f(b, 'OTHER_EQUITY_INVEST')})
    return out


def main():
    raw, out_path = sys.argv[1], sys.argv[2]
    res = {}
    for p in glob.glob(os.path.join(raw, '*-bal.txt')):
        code = os.path.basename(p).split('-')[0]
        res[code + '.HK'] = hk(raw, code)
    for p in glob.glob(os.path.join(raw, '*-zcfz.txt')):
        code = os.path.basename(p).split('-')[0]
        res[code + ('.SH' if code.startswith(('6', '9')) else '.SZ')] = a_share(raw, code)
    for v in res.values():
        if v.get('net_profit') and v.get('eps'):
            v['shares_est'] = v['net_profit'] / v['eps']
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    missing = [k for k, v in res.items() if not v.get('shares_est')]
    print('ok', len(res), 'companies', ('；缺股本（净利润或每股收益没有）：' + '、'.join(missing)) if missing else '')


if __name__ == '__main__':
    main()
