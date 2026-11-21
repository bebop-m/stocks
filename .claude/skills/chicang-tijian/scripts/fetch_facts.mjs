// 拉打工仓公司的财报关键数（东方财富接口），给 facts.py 整理。
// 用法：复制到 scratchpad，填好下面四个常量，再运行
//   ego-browser nodejs -e "await import('file:///<scratchpad>/fetch_facts.mjs')"
// 每次体检用一个新的、能说清目的的 taskSpace 名字；拉完 task.finish({ keep: [] })。
const OUT = '<scratchpad>/tijian/facts-raw/';          // 原始文件放这里（新建的空目录）
const HK = ['<五位港股代码>'];                          // 如 '00700'
const A = [['<六位A股代码>', 'SH']];                    // 交易所 SH 或 SZ
const A_DATES = { latest: '<最近一期报告期，如 2026-09-30>', years: ['<上一财年末>', '<前一年>', '<再前一年>'] };

const fs = await import('node:fs/promises');
await fs.mkdir(OUT, { recursive: true });
const task = await taskSpace('持仓体检：拉财报关键数');
const page = task.page('p1');
const log = [];
async function grab(name, url, wait = 1200) {
  try { await page.goto(url, { timeout: 30000 }); } catch (e) { log.push(name + ' goto-timeout'); }
  await page.waitForTimeout(wait);
  const text = await page.evaluate(() => document.body ? document.body.innerText : '');
  await fs.writeFile(OUT + name + '.txt', text, 'utf8');
  log.push(name + ':' + text.length);
}
const base = 'https://datacenter.eastmoney.com/securities/api/data/v1/get?';
const cols = 'SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,DATE_TYPE_CODE,FISCAL_YEAR,STD_ITEM_CODE,STD_ITEM_NAME,AMOUNT';
for (const c of HK) {
  const f = '&quoteColumns=&filter=(SECUCODE%3D%22' + c + '.HK%22)&pageNumber=1&pageSize=400&sortTypes=-1,1&sortColumns=REPORT_DATE,STD_ITEM_CODE&source=F10&client=PC';
  await grab(c + '-bal', base + 'reportName=RPT_HKF10_FN_BALANCE_PC&columns=' + cols + f);
  await grab(c + '-cf', base + 'reportName=RPT_HKF10_FN_CASHFLOW_PC&columns=' + cols + f);
  await grab(c + '-inc', base + 'reportName=RPT_HKF10_FN_INCOME_PC&columns=' + cols + f);
}
const em = 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/';
for (const [c, ex] of A) {
  await grab(c + '-zcfz', em + 'zcfzbAjaxNew?companyType=4&reportDateType=0&reportType=1&dates=' + A_DATES.latest + ',' + A_DATES.years[0] + '&code=' + ex + c);
  await grab(c + '-xjll', em + 'xjllbAjaxNew?companyType=4&reportDateType=0&reportType=1&dates=' + A_DATES.years.join(',') + '&code=' + ex + c);
  await grab(c + '-lrb', em + 'lrbAjaxNew?companyType=4&reportDateType=0&reportType=1&dates=' + A_DATES.years.join(',') + '&code=' + ex + c);
}
console.log(log.join(' '));
