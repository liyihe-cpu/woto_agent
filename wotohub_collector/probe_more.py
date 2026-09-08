from main import Session, load_config, ROOT
import json
with Session(load_config()) as s:
 p=s.open()
 # inspect the four-square button adjacent to the inline basic filters
 d=p.evaluate('''() => document.elementsFromPoint(715,193).map(e=>({tag:e.tagName,cls:e.className,html:e.outerHTML.slice(0,3000)}))''')
 print(json.dumps(d,ensure_ascii=True,indent=2))
 p.locator('div').evaluate_all('''els => els.filter(e => {let r=e.getBoundingClientRect(); return r.x>680&&r.x<760&&r.y>165&&r.y<220}).map(e=>e.outerHTML)''')
