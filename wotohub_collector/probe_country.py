from main import Session, load_config, ROOT
import json
with Session(load_config()) as s:
    p=s.open()
    # The second inline filter is the real "国家" control, verified from the rendered page.
    p.locator('.checkbox-search-wrap.select-item').nth(1).click()
    p.wait_for_timeout(400)
    data=p.evaluate('''() => { const ok=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.display!=="none"}; return [...document.querySelectorAll('.el-popover,.el-popper,[role=dialog],.v-modal ~ *')].filter(ok).map(e=>({cls:e.className,html:e.outerHTML})); }''')
    (ROOT/'country_probe.json').write_text(json.dumps(data,ensure_ascii=True,indent=2),encoding='ascii')
    p.screenshot(path=str(ROOT/'country_probe.png'),full_page=True)
    print(len(data))
