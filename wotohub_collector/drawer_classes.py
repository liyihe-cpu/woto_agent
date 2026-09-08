from main import Session, load_config
with Session(load_config()) as s:
 p=s.open(); p.locator('.wrap-center .all').click(); p.locator('.search-terms-drawer').wait_for(state='visible')
 print(p.locator('.search-terms-drawer').evaluate('''e=>[...e.querySelectorAll('.el-col')].map(x=>({title:x.querySelector('.search-title')?.innerText, classes:[...x.querySelectorAll('[class*=search-wrap]')].map(y=>y.className)})).filter(x=>x.title)'''))
