from main import Session, load_config, ROOT
import json

with Session(load_config()) as s:
    p=s.open()
    p.locator('.filter-work-btn').click()
    p.wait_for_timeout(500)
    data=p.evaluate('''() => {
      const ok=e=>{const r=e.getBoundingClientRect(),x=getComputedStyle(e);return r.width&&r.height&&x.display!=="none"};
      return {
       visible:[...document.querySelectorAll('body *')].filter(ok).map(e=>({tag:e.tagName,cls:String(e.className||''),text:(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,160),ph:e.placeholder||'',name:e.getAttribute('name')||''})).filter(x=>x.text||x.ph||x.name).slice(-600),
       selects:[...document.querySelectorAll('.el-select')].filter(ok).map((e,i)=>({i,html:e.outerHTML.slice(0,1500)})),
       inputs:[...document.querySelectorAll('input')].filter(ok).map((e,i)=>({i,html:e.outerHTML, value:e.value,ph:e.placeholder})),
      }
    }''')
    (ROOT/'filter_probe.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    p.screenshot(path=str(ROOT/'filter_probe.png'),full_page=True)
    print('saved filter_probe.json / filter_probe.png', len(data['selects']), len(data['inputs']))
