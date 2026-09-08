"""Read WotoHub's actual country/language values from the all-filter drawer.

No page labels or numeric internal IDs are used for filenames: checkbox values
are standard two-letter country/language codes as rendered by the site.
"""
import json
from main import Session, load_config, ROOT, DB, now

def visible_popover(page):
    return page.locator('.el-popover.el-popper').filter(
        has=page.locator('input[type="checkbox"]')
    ).locator('input[type="checkbox"]')

def options_for(page, col_index):
    control=page.locator('.search-terms-drawer .el-col').nth(col_index).locator('[class*=search-wrap]')
    print('opening drawer control',col_index,flush=True)
    control.click(timeout=10000)
    page.wait_for_timeout(300)
    # The drawer keeps all checkbox options in DOM, including ones below its
    # scroll viewport. This reads their actual values without guessing labels.
    result=page.evaluate('''() => {
      const isVisible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'};
      const pop=[...document.querySelectorAll('.el-popover.el-popper')].find(p=>isVisible(p)&&p.querySelector('input[type=checkbox]'));
      if(!pop) throw new Error('expected options popover not visible');
      const seen=new Set();
      return [...pop.querySelectorAll('label.el-checkbox')].map(label=>{
        const input=label.querySelector('input[type=checkbox]');
        return {value:(input?.value||'').trim(),label:(label.innerText||'').trim()};
      }).filter(x=>x.value && !seen.has(x.value) && seen.add(x.value));
    }''')
    # click the reference again to close before opening the next one
    control.click(timeout=10000)
    return result

def main():
    cfg=load_config()
    with Session(cfg) as ses:
        p=ses.open()
        print('opening all filters',flush=True); p.locator('.wrap-center .all').click(timeout=10000)
        p.locator('.search-terms-drawer').wait_for(state='visible',timeout=10000)
        countries=options_for(p,2)  # category=0, followers=1, country=2
        languages=options_for(p,3)  # language=3
    output={'at':now(),'platform_code':'youtube','countries':countries,'languages':languages}
    (ROOT/'discovered_options.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    db=DB(ROOT/'collector.sqlite3')
    with db.tx() as c:
        for x in countries: c.execute('INSERT OR REPLACE INTO options VALUES(?,?,?,?,?)',('country',x['value'],x['label'],x['value'],now()))
        for x in languages: c.execute('INSERT OR REPLACE INTO options VALUES(?,?,?,?,?)',('language',x['value'],x['label'],x['value'],now()))
    db.close()
    print(f"saved discovered_options.json: countries={len(countries)}, languages={len(languages)}")

if __name__=='__main__': main()
