"""WotoHub serial collector: persistent browser, SQLite checkpoints, and CSV export.

Run `python main.py login`, sign in in the opened browser, then press Enter in the
terminal. Run `inspect` next; it records real-page controls without guessing them.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, os, re, shutil, sqlite3, sys, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import BrowserContext, Error, Page, TimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parent
URL = "https://www.wotohub.com/workbenchSearch"
STATUS = ("pending", "running", "split", "done", "empty", "failed", "incomplete")
# Follower-span ladder.  A range whose result reaches the platform's 10,000 cap
# is re-run at the next finer span; below 250 the collector falls back to halving.
SPAN_LADDER = [1_000_000_000, 10_000_000, 1_000_000, 100_000, 10_000, 5_000, 1_000, 500, 250]
SPAN_TYPES = {
    "1b": 1_000_000_000, "10m": 10_000_000, "1m": 1_000_000, "100k": 100_000,
    "1w": 10_000, "5k": 5_000, "1k": 1_000, "500": 500, "250": 250,
}

def now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")
def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f: return yaml.safe_load(f) or {}
def safe_code(v: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", v.lower()).strip("-") or "unknown"
def smaller_span(width: int) -> int|None:
    """Next finer span strictly below `width`, or None when already at 250."""
    for s in SPAN_LADDER:
        if s < width: return s
    return None
def follower_ranges(follower_min, follower_max, width: int) -> list[tuple[int,int]]:
    """Cover [follower_min, follower_max] with fixed-width, inclusive, non-overlapping spans."""
    lo, ranges = int(follower_min), []
    while lo <= int(follower_max):
        hi = min(lo + width - 1, int(follower_max))
        ranges.append((lo, hi)); lo = hi + 1
    return ranges
def atomic_csv(path: Path, values: list[str], partial=False) -> Path:
    output = path.with_suffix(".partial.csv") if partial else path
    temp = output.with_name(output.name + ".tmp")
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        w=csv.writer(f); w.writerow(["handle"]); w.writerows([[x] for x in values])
    os.replace(temp, output); return output

class DB:
    def __init__(self, path: Path):
        self.c=sqlite3.connect(path); self.c.row_factory=sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL"); self.c.execute("PRAGMA foreign_keys=ON"); self.init()
    def init(self):
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, platform TEXT NOT NULL, output_dir TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS options(kind TEXT, value TEXT, label TEXT, code TEXT, discovered_at TEXT, PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS combinations(country_value TEXT, language_value TEXT, enabled INTEGER, note TEXT, PRIMARY KEY(country_value,language_value));
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), parent_id INTEGER REFERENCES tasks(id), country_value TEXT NOT NULL, country_code TEXT NOT NULL, language_value TEXT NOT NULL, language_code TEXT NOT NULL, low REAL NOT NULL, high REAL NOT NULL, recent_days INTEGER NOT NULL, result_total INTEGER, status TEXT NOT NULL DEFAULT 'pending', current_page INTEGER NOT NULL DEFAULT 1, failure_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(batch_id,country_value,language_value,low,high,recent_days));
        CREATE TABLE IF NOT EXISTS pages(task_id INTEGER, page_no INTEGER, signature TEXT, handles_json TEXT NOT NULL, captured_at TEXT NOT NULL, PRIMARY KEY(task_id,page_no));
        CREATE TABLE IF NOT EXISTS handles(task_id INTEGER, handle TEXT, first_page INTEGER, captured_at TEXT, PRIMARY KEY(task_id,handle));
        """); self.c.commit()
    def close(self): self.c.close()
    @contextmanager
    def tx(self):
        try: yield self.c; self.c.commit()
        except Exception: self.c.rollback(); raise
    def batch(self, platform: str, resume: str|None) -> sqlite3.Row:
        if resume:
            r=self.c.execute("SELECT * FROM batches WHERE id=?",(resume,)).fetchone()
            if not r: raise ValueError(f"batch not found: {resume}")
            return r
        bid=datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6]; out=ROOT/"output"/safe_code(platform)/bid; out.mkdir(parents=True)
        with self.tx() as c: c.execute("INSERT INTO batches VALUES(?,?,?,?)",(bid,platform,str(out),now()))
        return self.c.execute("SELECT * FROM batches WHERE id=?",(bid,)).fetchone()
    def add_task(self,b,cv,cc,lv,lc,low,high,parent=None,recent_days=30):
        with self.tx() as c: c.execute("INSERT OR IGNORE INTO tasks(batch_id,parent_id,country_value,country_code,language_value,language_code,low,high,recent_days,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(b,parent,cv,cc,lv,lc,low,high,recent_days,now(),now()))
    def tasks(self,bid, retry=False):
        statuses=("pending","running","failed") if retry else ("pending","running")
        q=','.join('?'*len(statuses)); return self.c.execute(f"SELECT * FROM tasks WHERE batch_id=? AND status IN ({q}) ORDER BY id",(bid,*statuses)).fetchall()
    def update(self,tid,status,**kw):
        cols=["status=?","updated_at=?"]; vals=[status,now()]
        for k,v in kw.items(): cols.append(k+"=?"); vals.append(v)
        with self.tx() as c: c.execute(f"UPDATE tasks SET {','.join(cols)} WHERE id=?",(*vals,tid))
    def split(self,t):
        # The verified UI accepts integer follower counts. Both endpoints are
        # inclusive. Re-run the interval at the next finer span (1w->5k->1k->500->250);
        # below 250 fall back to halving so dense ranges still terminate.
        low,high=int(t['low']),int(t['high'])
        if low >= high: self.update(t['id'],'incomplete',failure_reason='single follower count still reports >=10000'); return
        self.update(t['id'],'split')
        target=smaller_span(high-low+1)
        if target is None:
            mid=(low+high)//2
            self.add_task(t['batch_id'],t['country_value'],t['country_code'],t['language_value'],t['language_code'],low,mid,t['id'],t['recent_days'])
            self.add_task(t['batch_id'],t['country_value'],t['country_code'],t['language_value'],t['language_code'],mid+1,high,t['id'],t['recent_days'])
        else:
            start=low
            while start<=high:
                end=min(start+target-1,high)
                self.add_task(t['batch_id'],t['country_value'],t['country_code'],t['language_value'],t['language_code'],start,end,t['id'],t['recent_days'])
                start=end+1
    def save_page(self,tid,no,handles,signature):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)",(tid,no,signature,json.dumps(handles),now()))
            c.executemany("INSERT OR IGNORE INTO handles VALUES(?,?,?,?)",[(tid,h,no,now()) for h in handles])
            c.execute("UPDATE tasks SET current_page=?,updated_at=? WHERE id=?",(no,now(),tid))
    def reset_capture(self,tid):
        """A resumed task is rescanned after applying filters again.

        Keeping prior handles while page ordering changes can silently turn a
        partial result into an apparently complete one, therefore delete only
        this task's page/handle checkpoint in one transaction.
        """
        with self.tx() as c:
            c.execute("DELETE FROM pages WHERE task_id=?",(tid,))
            c.execute("DELETE FROM handles WHERE task_id=?",(tid,))
            c.execute("UPDATE tasks SET current_page=1,updated_at=? WHERE id=?",(now(),tid))
    def handles(self,tid): return [r[0] for r in self.c.execute("SELECT handle FROM handles WHERE task_id=? ORDER BY rowid",(tid,))]
    def counts(self,bid): return dict(self.c.execute("SELECT status,count(*) FROM tasks WHERE batch_id=? GROUP BY status",(bid,)).fetchall())

class Session:
    def __init__(self,cfg): self.cfg=cfg; self.pw=None; self.ctx=None; self.page=None
    def __enter__(self):
        self.pw=sync_playwright().start(); b=self.cfg.get('browser',{}); state=ROOT/'state'; state.mkdir(exist_ok=True)
        args=dict(user_data_dir=str(state/'profile'),headless=b.get('headless',False),viewport=b.get('viewport'),slow_mo=b.get('slow_mo_ms',0))
        if b.get('channel'): args['channel']=b['channel']
        try: self.ctx=self.pw.chromium.launch_persistent_context(**args)
        except Error:
            args.pop('channel',None); self.ctx=self.pw.chromium.launch_persistent_context(**args)
        self.page=self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page(); self.page.set_default_timeout(self.cfg.get('timing',{}).get('navigation_timeout_ms',30000)); return self
    def __exit__(self,*x): self.ctx.close(); self.pw.stop()
    def open(self): self.page.goto(URL,wait_until='domcontentloaded'); self.page.wait_for_timeout(1200); return self.page

def js_extract(page:Page)->dict:
    source=(ROOT/'wotohub_page.js').read_text(encoding='utf-8'); return page.evaluate(source)
def report_inspect(page:Page):
    controls=page.evaluate("""() => [...document.querySelectorAll('button,input,[role=button],.el-select,.ant-select')].filter(e=>{let r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.display!='none'}).map(e=>({tag:e.tagName,cls:e.className,txt:(e.innerText||e.placeholder||e.getAttribute('aria-label')||'').trim().slice(0,100)})).slice(0,500)""")
    page.screenshot(path=str(ROOT/'inspection.png'),full_page=True)
    data={'url':page.url,'title':page.title(),'controls':controls,'extract':js_extract(page),'at':now()}
    (ROOT/'inspection.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('inspection.json and inspection.png saved. Visible controls:',len(controls)); print(json.dumps(controls[:40],ensure_ascii=False,indent=2))

def require_selectors(cfg):
    s=cfg.get('selectors') or {}; required=['country','language','followers_min','followers_max','recent_publish','apply','total','next']
    missing=[x for x in required if not s.get(x)]
    if missing: raise RuntimeError('selectors not confirmed: '+', '.join(missing)+'. Run inspect after login and fill config.yaml selectors.')
    return s
def get_total(text:str)->int|None:
    # Deliberately returns None, never zero, if the site format is unknown.
    m=re.search(r'(?:total|共|结果|达人)[^0-9]{0,20}([0-9][0-9,]*)',text,re.I)
    return int(m.group(1).replace(',','')) if m else None
def configure(page,t,s,cfg):
    # Selectors are confirmed during inspect; fill inputs directly and use UI clicks for dropdowns.
    def one(k): return page.locator(s[k]).first
    one('country').click(); page.locator(s['country_option'].format(value=t['country_value'])).click()
    one('language').click(); page.locator(s['language_option'].format(value=t['language_value'])).click()
    one('followers_min').fill(str(t['low'])); one('followers_max').fill(str(t['high']))
    one('recent_publish').click(); page.locator(s['recent_option'].format(days=t['recent_days'])).click(); one('apply').click()
    page.wait_for_timeout(cfg['timing']['query_delay_ms'])
def collect_task(page,db,t,cfg,s):
    db.update(t['id'],'running',failure_reason=None); configure(page,t,s,cfg)
    db.reset_capture(t['id'])
    snap=js_extract(page); total=get_total(page.locator(s['total']).inner_text())
    if total is None: raise RuntimeError('result total could not be parsed')
    db.update(t['id'],'running',result_total=total,current_page=1)
    print(f"{t['country_code']}/{t['language_code']} {t['low']}-{t['high']}: total={total}")
    if total>=cfg['filters']['maximum_count']:
        db.split(t); return
    if total==0: db.update(t['id'],'empty'); atomic_csv(task_path(db,t),[]); return
    page_no=1; seen_signatures=set()
    while True:
        snap=js_extract(page); hs=snap['handles']; signature=hashlib.sha256(json.dumps([snap['activePage'],hs],ensure_ascii=False).encode()).hexdigest()
        if signature in seen_signatures: raise RuntimeError('repeated page signature before real last page')
        seen_signatures.add(signature); db.save_page(t['id'],page_no,hs,signature)
        print(f"  page={page_no} page_handles={len(hs)} unique={len(db.handles(t['id']))}")
        if snap['nextDisabled']: break
        before={'page':snap['activePage'],'first':snap['firstHandle'],'signature':signature}; page.locator(s['next']).first.click(); ok=False
        for _ in range(40):
            page.wait_for_timeout(250); later=js_extract(page)
            sig=hashlib.sha256(json.dumps([later['activePage'],later['handles']],ensure_ascii=False).encode()).hexdigest()
            # Require data difference plus either page progression or stable network-render delay.
            if sig!=before['signature'] and (later['activePage']!=before['page'] or later['firstHandle']!=before['first']): ok=True; break
        if not ok: raise RuntimeError('next-page load was not verified')
        page_no+=1; page.wait_for_timeout(cfg['timing']['page_delay_ms'])
    hs=db.handles(t['id'])
    if len(hs)!=total: db.update(t['id'],'incomplete',failure_reason=f'unique handles {len(hs)} != reported total {total}'); atomic_csv(task_path(db,t),hs,partial=True)
    else: db.update(t['id'],'done'); atomic_csv(task_path(db,t),hs)
def task_path(db,t):
    b=db.c.execute('SELECT platform,output_dir FROM batches WHERE id=?',(t['batch_id'],)).fetchone()
    folder=Path(b['output_dir'])/safe_code(t['country_code'])
    folder.mkdir(parents=True,exist_ok=True)
    recent = "recentall" if not t['recent_days'] else f"recentdays{t['recent_days']}"
    return folder/f"{safe_code(b['platform'])}_{safe_code(t['country_code'])}_{safe_code(t['language_code'])}_{t['low']:g}-{t['high']:g}_{recent}.csv"
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True); sub.add_parser('login'); sub.add_parser('inspect'); a=sub.add_parser('collect'); a.add_argument('--resume'); a.add_argument('--retry-failed',action='store_true'); a.add_argument('--span',choices=tuple(SPAN_TYPES),help='Initial follower span: 1b/10m/1m/100k/1w/5k/1k/500/250.'); st=sub.add_parser('status'); st.add_argument('--batch',required=True); args=p.parse_args(); cfg=load_config()
    if args.cmd=='status':
        db=DB(ROOT/'collector.sqlite3'); print(db.counts(args.batch)); return
    with Session(cfg) as ses:
        page=ses.open()
        if args.cmd=='login':
            print('请在可见浏览器完成 WotoHub 站内登录。本进程会保持浏览器开启，检测到登录弹窗消失后自动保存会话。')
            deadline=time.monotonic()+1800
            while time.monotonic()<deadline:
                # The known modal class is checked only as a login-completion signal;
                # it does not assume anything about the later search controls.
                modal=page.locator('.loginnew,.login-new,[class*=loginnew]').count()
                if not modal:
                    page.goto(URL,wait_until='domcontentloaded'); page.wait_for_timeout(1200)
                    if not page.locator('.loginnew,.login-new,[class*=loginnew]').count():
                        print('登录会话已保存。下一步：python main.py inspect'); return
                page.wait_for_timeout(1000)
            print('等待登录超时；浏览器会话已保留，可重新运行 start_login.cmd。'); return
        if args.cmd=='inspect': report_inspect(page); return
        s=require_selectors(cfg); db=DB(ROOT/'collector.sqlite3'); platform=(cfg.get('discovered') or {}).get('platform_code','current-platform'); batch=db.batch(platform,args.resume)
        # `discovered.combinations` is populated only from confirmed inspect results.
        combos=(cfg.get('discovered') or {}).get('combinations',[])
        if not combos: raise RuntimeError('No confirmed country/language combinations in config.yaml discovered.combinations.')
        for x in combos:
            # Spans now use the same ladder as the Vue collector: config
            # filters.span (or --span), covering inclusive [min, max-1] with
            # fixed-width, non-overlapping intervals.  A ==max task is never made;
            # reaching the 10k cap re-runs at a finer span via DB.split().
            fmin=int(cfg['filters']['follower_min']); fmax=int(cfg['filters']['follower_max'])
            width=SPAN_TYPES[args.span] if args.span else int(cfg['filters'].get('span',500))
            for lo,hi in follower_ranges(fmin,fmax-1,width):
                db.add_task(batch['id'],x['country_value'],x['country_code'],x['language_value'],x['language_code'],lo,hi)
        for t in db.tasks(batch['id'],args.retry_failed):
            try: collect_task(page,db,t,cfg,s)
            except Exception as e:
                db.update(t['id'],'failed',failure_reason=str(e))
                # A checkpoint from a prior page remains inspectable; exporting
                # it is explicitly labelled partial and never overwrites success.
                existing=db.handles(t['id'])
                if existing: atomic_csv(task_path(db,t),existing,partial=True)
                print('FAILED',t['id'],e)
        print('batch',batch['id'],db.counts(batch['id']))
if __name__=='__main__': main()
