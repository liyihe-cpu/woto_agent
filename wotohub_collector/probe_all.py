from main import Session, load_config, ROOT
import json
with Session(load_config()) as s:
 p=s.open(); p.locator('.wrap-center .all').click(); p.wait_for_timeout(500)
 d=p.evaluate('''() => {const ok=e=>{let r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.display!='none'};return [...document.querySelectorAll('.el-dialog,.el-drawer,.el-popover,.el-popper')].filter(ok).map(e=>({cls:e.className,html:e.outerHTML}))}''')
 (ROOT/'all_probe.json').write_text(json.dumps(d,ensure_ascii=True,indent=2),encoding='ascii')
 p.screenshot(path=str(ROOT/'all_probe.png'),full_page=True); print(len(d))
