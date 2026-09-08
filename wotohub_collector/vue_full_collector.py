"""Production runner using WotoHub's own Vue search method and browser session.

It attaches to the always-open dedicated browser on localhost:9222.  Every
query is made by the page's `getBloggerList()` method; its matching HTTP
response is used for total/next-page verification and Vue rows are cross-checked.
"""
from __future__ import annotations
import argparse, json, sys, time, traceback
from collections import deque
from pathlib import Path
from playwright.sync_api import sync_playwright
from main import DB, ROOT, atomic_csv, js_extract, now, task_path, load_config, SPAN_TYPES, follower_ranges

MAX=10000; PAGE_SIZE=500
# A range is split only when its result count reaches the platform's 10,000 cap,
# and is re-run at the next finer span (1w->5k->1k->500->250, then halving).  The
# upper endpoint is included, so 10,000 is never a separate follow-up task.
PLATFORM_ALIASES={'youtube':'youtube', 'ins':'instagram', 'instagram':'instagram', 'tiktok':'tiktok'}
RECENT_OPTIONS={0:'', 30:'RECENT_30D', 60:'RECENT_60D', 90:'RECENT_90D'}
TIMING=load_config().get('timing',{})
QUERY_DELAY=max(700,int(TIMING.get('query_delay_ms',1200)))
PAGE_DELAY=max(500,int(TIMING.get('page_delay_ms',900)))
def log(message, pause=0.25):
 # WotoHub option labels can contain invisible Unicode direction marks.  Windows
 # console code pages reject some of them, so preserve the message while making
 # logging itself non-fatal.
 encoding = sys.stdout.encoding or 'utf-8'
 try:
  rendered = str(message).encode(encoding, errors='replace').decode(encoding, errors='replace')
 except LookupError:
  rendered = str(message)
 print(rendered,flush=True)
 if pause: time.sleep(pause)
class Collector:
 def __init__(self, platform):
  self.platform=platform
  self.pw=sync_playwright().start(); self.browser=self.pw.chromium.connect_over_cdp('http://127.0.0.1:9222')
  self.page=next((p for c in self.browser.contexts for p in c.pages if 'wotohub.com' in p.url),None)
  if not self.page: raise RuntimeError('Open WotoHub in the persistent browser first.')
  self.page.goto('https://www.wotohub.com/workbenchSearch',wait_until='domcontentloaded'); self.page.wait_for_timeout(3000)
  self.page.evaluate('''() => { for(const el of document.querySelectorAll('*')){const c=el.__vue__||el.__vueParentComponent,x=c?.proxy||c?.ctx||c;if(x?.queryParams&&typeof x.getBloggerList==='function'){x.queryParams.pageSize=500;return}} }''')
  log(f'页面每页结果数已固定为 {PAGE_SIZE} 条；查询间隔 {QUERY_DELAY/1000:.1f} 秒，翻页间隔 {PAGE_DELAY/1000:.1f} 秒')
 def close(self): self.pw.stop()  # detach only; keep the visible browser open
 def query(self,country,lang,low,high,recent_days,page_no):
  recent=RECENT_OPTIONS[recent_days]
  # The page can issue unrelated background searches while the list changes.
  # Accept only the request whose filters and page match this task exactly.
  def is_target_response(r):
   if not r.url.endswith('/dataService/home/search') or r.request.method != 'POST': return False
   try:
    body=r.request.post_data_json or {}; regions=body.get('regionList') or []
    return (any(country in (item.get('country') or []) for item in regions)
            and body.get('platform') == self.platform
            and body.get('blogLangs') == ([lang] if lang else [])
            and body.get('minFansNum') == low and body.get('maxFansNum') == high
            and body.get('searchRecent') == recent
            and body.get('pageNum') == page_no and body.get('pageSize') == PAGE_SIZE)
   except Exception: return False
  with self.page.expect_response(is_target_response,timeout=30000) as event:
   self.page.evaluate('''async a => {
    let x,seen=new Set(); for(const el of document.querySelectorAll('*')){const c=el.__vue__||el.__vueParentComponent, q=c?.proxy||c?.ctx||c;if(q&&!seen.has(q)&&q.queryParams&&typeof q.getBloggerList==='function'){x=q;break}if(q)seen.add(q)}
    if(!x) throw Error('WotoHub Vue search parent unavailable');
    const group=x.countryObj.optionsArr.find(g=>g.optionsItem.some(o=>o.id===a.country));
    if(!group) throw Error('country group unavailable: '+a.country);
    Object.assign(x.queryParams,{platform:a.platform,regionList:[{id:group.id,country:[a.country]}],blogLangs:a.lang?[a.lang]:[],minFansNum:a.low,maxFansNum:a.high,searchRecent:a.recent,pageNum:a.page,pageSize:a.size,searchFilterList:[]});
    await x.getBloggerList();
   }''',{'platform':self.platform,'country':country,'lang':lang,'low':low,'high':high,'recent':recent,'page':page_no,'size':PAGE_SIZE})
  r=event.value; raw=r.json()
  if raw.get('code')!='0' or not raw.get('data'): raise RuntimeError(raw.get('message','search response missing data'))
  data=raw['data']; body=r.request.post_data_json
  expected_langs=[lang] if lang else []
  regions=body.get('regionList') or []
  if (body.get('platform') != self.platform
      or not any(country in (item.get('country') or []) for item in regions)
      or body.get('blogLangs') != expected_langs or body.get('minFansNum') != low
      or body.get('maxFansNum') != high or body.get('searchRecent') != recent
      or body.get('pageNum') != page_no or body.get('pageSize') != PAGE_SIZE):
   raise RuntimeError('search request did not retain requested filters')
  rows=data['bloggerList']; response_handles=[str(x.get('username') or '').lstrip('@').strip() for x in rows if x.get('username')]
  self.page.wait_for_timeout(150)
  vue=js_extract(self.page)['handles']
  if response_handles and not set(response_handles).intersection(vue): raise RuntimeError('Vue current list does not match search response')
  time.sleep(QUERY_DELAY/1000)
  return data,response_handles

LOCAL_LANGS={
 'us':['en','es'],'ca':['en','fr'],'mx':['es','en'],'br':['pt','en','es'],'es':['es','en'],'fr':['fr','en'],
 'de':['de','en'],'it':['it','en'],'pt':['pt','en'],'gb':['en','es'],'ie':['en','ga'],'au':['en','zh'],
 'in':['en','hi'],'id':['id','en'],'ph':['en','tl'],'jp':['ja','en'],'kr':['ko','en'],'th':['th','en'],
 'vn':['vi','en'],'tr':['tr','en'],'sa':['ar','en'],'ae':['ar','en'],'eg':['ar','en'],'ru':['ru','en'],
 'ua':['uk','en'],'pl':['pl','en'],'nl':['nl','en'],'se':['sv','en'],'no':['no','en'],'dk':['da','en'],
 'fi':['fi','en'],'gr':['el','en'],'ro':['ro','en'],'hu':['hu','en'],'cz':['cs','en'],'ar':['es','en'],
 'co':['es','en'],'cl':['es','en'],'pe':['es','en'],'ve':['es','en'],'za':['en','af'],'ng':['en','yo'],
}
ESTIMATED_COUNTRY_ORDER=[
 'us','br','in','id','mx','jp','ph','gb','de','fr','es','it','tr','ca','au','kr','th','vn','ar','co',
 'pl','nl','sa','za','ng','pk','bd','eg','ru','ua','pe','cl','my','sg','pt','ro','be','se','ch','at',
 'dk','no','fi','ie','nz','ae','ma','dz','ke','tz','gh','ve','ec','gt','do','co','ar','uy','py','bo'
]
def selected_languages(country, available):
 preferred=LOCAL_LANGS.get(country,['en','es'])
 chosen=[x for x in preferred if x in available]
 for x in ('en','es','pt'):
  if len(chosen)>=2: break
  if x in available and x not in chosen: chosen.append(x)
 return chosen[:2]
def build_plan(col,countries,languages,plan_file):
 rows=[]
 log(f'开始统计国家排序：共 {len(countries)} 个国家，统计口径为 5000-10000 粉丝、近30天、不限制语言')
 for i,c in enumerate(countries,1):
  data,_=col.query(c,None,5000,10000,30,1)
  rows.append({'country_code':c,'count':int(data['count']),'languages':selected_languages(c,languages)})
  if i==1 or i==len(countries) or i%10==0: log(f'国家统计进度：{i}/{len(countries)}｜{c}｜结果数 {data["count"]}',0.15)
 rows.sort(key=lambda x:(-x['count'],x['country_code']))
 plan_file.write_text(json.dumps({'created_at':now(),'scope':'followers 5000-10000; recent 30 days; no language','countries':rows},ensure_ascii=False,indent=2),encoding='utf-8')
 log(f'国家语言计划已生成：{plan_file}')
 return rows
def estimated_plan(countries,languages,plan_file,preserve_order=False):
 """Fast deterministic order: platform audience strength, then population."""
 rank={code:i for i,code in enumerate(ESTIMATED_COUNTRY_ORDER)}
 rows=[{'country_code':c,'estimated_rank':rank.get(c,10000),'languages':selected_languages(c,languages)} for c in countries]
 if not preserve_order: rows.sort(key=lambda x:(x['estimated_rank'],x['country_code']))
 plan_file.write_text(json.dumps({'created_at':now(),'scope':'estimated country order; followers 5000-10000; recent 30 days','countries':rows},ensure_ascii=False,indent=2),encoding='utf-8')
 log(f'已按估算优先级生成国家语言计划：{plan_file}')
 return rows
def seed(db,batch,plan,ranges,recent_days):
 total=sum(len(x['languages']) for x in plan)*len(ranges); inserted=0
 log(f"开始创建任务：国家 {len(plan)} 个，每国 2 种语言，初始跨度区间 {len(ranges)} 个，预计初始任务 {total} 个")
 with db.tx() as cur:
  for ci,item in enumerate(plan,1):
   c=item['country_code']
   rows=[]
   for l in item['languages']:
    for lo,hi in ranges:
     rows.append((batch['id'],None,c,c,l,l,lo,hi,recent_days,now(),now()))
   cur.executemany('INSERT OR IGNORE INTO tasks(batch_id,parent_id,country_value,country_code,language_value,language_code,low,high,recent_days,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',rows)
   inserted+=len(rows)
   if ci==1 or ci==len(plan) or ci%10==0: log(f"任务创建进度：国家 {ci}/{len(plan)}（{c}），已写入 {inserted}/{total}",0.15)
 log('初始任务创建完成')

def run_task(col,db,t):
 db.update(t['id'],'running',failure_reason=None); db.reset_capture(t['id'])
 log(f"开始查询｜任务 {t['id']}｜国家 {t['country_code']}｜语言 {t['language_code']}｜粉丝 {t['low']:g}-{t['high']:g}｜第 1 页")
 first,hs=col.query(t['country_value'],t['language_value'],int(t['low']),int(t['high']),int(t['recent_days']),1); count=int(first['count'])
 db.update(t['id'],'running',result_total=count,current_page=1)
 log(f"查询结果｜共 {count} 条")
 if count>=MAX:
  log(f"结果达到 {MAX} 条上限，拆分当前粉丝区间后重试"); db.split(t); return
 if count==0:
  db.update(t['id'],'empty'); path=atomic_csv(task_path(db,t),[]); log(f"空结果，已生成表头 CSV：{path}"); return
 page=1
 while True:
  if page>1: log(f"翻页查询｜任务 {t['id']}｜第 {page} 页")
  data,handles=(first,hs) if page==1 else col.query(t['country_value'],t['language_value'],int(t['low']),int(t['high']),int(t['recent_days']),page)
  db.save_page(t['id'],page,handles,json.dumps(handles,ensure_ascii=False)); log(f"本页完成｜第 {page} 页 {len(handles)} 条｜累计唯一 handle {len(db.handles(t['id']))}")
  if not data.get('hasNextPage'): break
  page+=1; time.sleep(PAGE_DELAY/1000)
 unique=db.handles(t['id'])
 if len(unique)!=count:
  db.update(t['id'],'incomplete',failure_reason=f'unique={len(unique)} response_count={count}'); atomic_csv(task_path(db,t),unique,partial=True)
 else:
  db.update(t['id'],'done'); path=atomic_csv(task_path(db,t),unique); log(f"任务完成｜唯一 handle {len(unique)} 个｜CSV：{path}")

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--verify',action='store_true'); ap.add_argument('--resume'); ap.add_argument('--retry-failed',action='store_true')
 ap.add_argument('--stop-after-country',help='Resume only this country, then stop cleanly before the next country.')
 ap.add_argument('--start-after-country',help='For a new batch, begin with the country after this code in the estimated order.')
 ap.add_argument('--platform',default='youtube',choices=tuple(PLATFORM_ALIASES),help='Output/batch platform; ins sends Instagram, tiktok sends TikTok searches.')
 ap.add_argument('--span',choices=tuple(SPAN_TYPES),help='Initial follower span: 1b/10m/1m/100k/1w/5k/1k/500/250. A capped range is re-run at the next finer span.')
 ap.add_argument('--follower-min',type=int,help='Initial follower range lower bound (inclusive).')
 ap.add_argument('--follower-max',type=int,help='Initial follower range upper bound (exclusive).')
 ap.add_argument('--countries',nargs='+',help='Country codes, separated by spaces and/or commas, e.g. us br, or us,br.')
 ap.add_argument('--all-countries',action='store_true',help='Use every country in discovered_options.json.')
 ap.add_argument('--recent',choices=('all','30','60','90'),help='Latest publish window; all means no limit.')
 ap.add_argument('--preserve-country-order',action='store_true',help='Run supplied --countries in their given priority order.')
 args=ap.parse_args()
 output_platform='ins' if args.platform in ('ins','instagram') else args.platform
 backend_platform=PLATFORM_ALIASES[args.platform]
 state_file=ROOT/f'run_state_{output_platform}.json'
 plan_file=ROOT/f'country_language_plan_{output_platform}.json'
 cfg_filters=load_config().get('filters') or {}
 fmin=args.follower_min if args.follower_min is not None else int(cfg_filters.get('follower_min',5000))
 fmax=args.follower_max if args.follower_max is not None else int(cfg_filters.get('follower_max',MAX))
 if fmin < 0 or fmax <= fmin: ap.error('--follower-max must be greater than --follower-min')
 width=SPAN_TYPES[args.span] if args.span else int(cfg_filters.get('span',500))
 if width not in SPAN_TYPES.values(): ap.error('filters.span must be one of: '+', '.join(SPAN_TYPES))
 recent_days=int(args.recent) if args.recent and args.recent != 'all' else (0 if args.recent == 'all' else int(cfg_filters.get('recent_days',30)))
 if recent_days not in RECENT_OPTIONS: ap.error('recent_days must be 0, 30, 60, or 90')
 ranges=follower_ranges(fmin,fmax-1,width)
 log(f"粉丝跨度方案：{'宽度 '+str(width) if args.span else '配置 span='+str(width)}；初始区间 {len(ranges)} 个（{fmin}-{fmax-1}），结果达 {MAX} 会自动降级更细跨度")
 opts=json.loads((ROOT/'discovered_options.json').read_text(encoding='utf-8')); all_countries=[x['value'].lower() for x in opts['countries']]; languages=[x['value'] for x in opts['languages']]
 configured=cfg_filters.get('countries','all')
 raw_codes = [] if args.all_countries else (args.countries if args.countries is not None else configured)
 if raw_codes == 'all' or raw_codes is None: countries=all_countries
 else:
  if isinstance(raw_codes,str): raw_codes=[raw_codes]
  countries=[]
  for item in raw_codes:
   countries.extend(code.strip().lower() for code in str(item).split(',') if code.strip())
  countries=list(dict.fromkeys(countries))
  unknown=sorted(set(countries)-set(all_countries))
  if unknown: ap.error('unknown country code(s): '+', '.join(unknown))
 db=DB(ROOT/'collector.sqlite3')
 if args.resume=='latest':
  row=db.c.execute("select id from batches where platform=? order by created_at desc limit 1",(output_platform,)).fetchone()
  if not row: raise RuntimeError(f'no previous {output_platform} batch to resume')
  args.resume=row['id']
 batch=db.batch(output_platform,args.resume)
 if batch['platform'] != output_platform: raise RuntimeError(f'batch {batch["id"]} belongs to {batch["platform"]}, not {output_platform}')
 # Organize results by platform/country, not by temporary batch folders.
 output_root=ROOT/'output'/output_platform; output_root.mkdir(parents=True,exist_ok=True)
 with db.tx() as cur: cur.execute('update batches set output_dir=? where id=?',(str(output_root),batch['id']))
 col=Collector(backend_platform)
 if not args.resume:
  if args.verify: db.add_task(batch['id'],'us','us','en','en',fmin,min(fmin+width-1,fmax-1),recent_days=recent_days)
  else:
   plan=estimated_plan(countries,languages,plan_file,args.preserve_country_order)
   if args.start_after_country:
    marker=args.start_after_country.lower()
    pos=next((i for i,item in enumerate(plan) if item['country_code']==marker),None)
    if pos is None: raise RuntimeError(f'country not in plan: {marker}')
    plan=plan[pos+1:]
    if not plan: raise RuntimeError(f'no country remains after: {marker}')
    log(f'新批次从 {plan[0]["country_code"]} 开始；跳过至 {marker} 为止的旧批次国家')
   seed(db,batch,plan,ranges,recent_days)
 def state(**values):
  data={'updated_at':now(),**values}; state_file.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
 state(status='running',batch_id=batch['id'],page_size=PAGE_SIZE,platform=output_platform)
 log(f"采集批次：{batch['id']}｜待执行任务：{len(db.tasks(batch['id']))}")
 try:
  queue=deque(db.tasks(batch['id'],args.retry_failed))
  target=None
  if args.stop_after_country:
   target=args.stop_after_country.lower()
   queue=deque(t for t in queue if t['country_code'].lower()==target)
  while queue:
   t=queue.popleft()
   prev=queue
   try: run_task(col,db,t)
   except Exception as e:
    db.update(t['id'],'failed',failure_reason=str(e)); old=db.handles(t['id']);
    if old: atomic_csv(task_path(db,t),old,partial=True)
    log(f"严重错误｜任务 {t['id']}｜{e}",0)
    traceback.print_exc()
    state(status='failed',batch_id=batch['id'],task_id=t['id'],error=str(e))
    raise SystemExit(1)
   # A split task leaves pending children; enqueue them depth-first so the
   # finer spans are retried immediately (before any sibling country).
   children=[c for c in db.c.execute('select * from tasks where parent_id=? order by id',(t['id'],)).fetchall() if c['status']=='pending']
   if children:
    log(f"拆分后子任务 {len(children)} 个，立即重试更细跨度",0)
    for ch in reversed(children): queue.appendleft(ch)
  if target is not None:
   state(status='country_complete',batch_id=batch['id'],country=target,summary=db.counts(batch['id']))
   log(f'国家 {target} 已完成；已在下一个国家开始前停止。')
  summary=db.counts(batch['id']); state(status='complete',batch_id=batch['id'],summary=summary); log(f"全部完成｜批次 {batch['id']}｜状态汇总：{summary}")
 except KeyboardInterrupt:
  state(status='stopped',batch_id=batch['id'],reason='terminal interrupt'); log(f'已终止｜批次 {batch["id"]}。双击 resume_latest.cmd 可继续运行。',0)
 finally: col.close(); db.close()
if __name__=='__main__': main()
