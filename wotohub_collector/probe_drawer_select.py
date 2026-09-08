from main import Session, load_config, ROOT
import json, sys
i=int(sys.argv[1]) if len(sys.argv)>1 else 1
with Session(load_config()) as s:
 p=s.open(); p.locator('.wrap-center .all').click(); p.locator('.search-terms-drawer').wait_for(state='visible',timeout=10000)
 q=p.locator('.search-terms-drawer .el-col').nth(i).locator('[class*=search-wrap]'); print('control count',q.count(),flush=True)
 print(q.evaluate('(e) => e.outerHTML').encode('ascii','backslashreplace').decode()[:2500],flush=True)
 q.locator('.el-popover__reference').click(); p.wait_for_timeout(500)
 d=p.evaluate('''() => {const ok=e=>{let r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.display!='none'}; return [...document.querySelectorAll('.el-select-dropdown,.el-popover,.el-popper')].filter(ok).map(e=>({cls:e.className,html:e.outerHTML}));}''')
 (ROOT/f'drawer_select_{i}.json').write_text(json.dumps(d,ensure_ascii=True,indent=2),encoding='ascii')
 p.screenshot(path=str(ROOT/f'drawer_select_{i}.png'),full_page=True); print([x['cls'] for x in d])
