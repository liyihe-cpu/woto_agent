"""One real-page filter application used before enabling collector automation."""
from main import Session, load_config, ROOT
import json

def open_popover(page, control):
    """Open the popover uniquely owned by one Vue filter component."""
    ref=control.locator('.el-popover__reference')
    pop_id=ref.get_attribute('aria-describedby')
    if not pop_id: raise RuntimeError('filter reference has no aria-describedby')
    ref.click()
    pop=page.locator(f'#{pop_id}')
    pop.wait_for(state='visible',timeout=10000)
    return pop

def dismiss_to_drawer_blank(drawer):
    # WotoHub commits this country multi-select on blur.  Its popup has no
    # usable confirm control in the drawer variant; the title bar is blank,
    # stable and outside every filter popover.
    header=drawer.locator('.el-drawer__header').bounding_box()
    if not header: raise RuntimeError('drawer header is not visible')
    # Coordinate click bypasses Element's transient popover overlay.
    drawer.page.mouse.click(header['x']+header['width']*0.55, header['y']+20)

def trusted_option_click(page, popover, value):
    """Click the rendered label, not Element's off-screen checkbox input."""
    label=popover.locator(f'label.el-checkbox:has(input[value="{value}"])')
    box=label.bounding_box()
    if not box: raise RuntimeError(f'option {value} has no rendered box')
    page.mouse.click(box['x']+min(14,box['width']/2), box['y']+box['height']/2)

def vue_filter_state(page):
    """Return compact reactive state around the all-filter drawer for tracing."""
    return page.evaluate('''() => {
      const root=document.querySelector('.search-terms-drawer');
      const seen=new WeakSet(), found=[];
      function walk(v,path,d){
        if(v==null || d>5) return;
        if(typeof v!=='object') return;
        if(seen.has(v)) return; seen.add(v);
        for(const k of Object.keys(v)){
          if(/^_|parent|root|vnode|proxy|refs/i.test(k)) continue;
          let x; try{x=v[k]}catch(_){continue}
          const p=path+'.'+k;
          if(typeof x==='string'||typeof x==='number'||typeof x==='boolean'){
            if(/region|lang|fan|recent|filter|search|range|country/i.test(p)) found.push([p,x]);
          } else if(Array.isArray(x)) {
            if(x.length && /region|lang|fan|recent|filter|search|range|country/i.test(p)) found.push([p,x.slice(0,20)]);
            walk(x,p,d+1);
          } else walk(x,p,d+1);
        }
      }
      let c=root?.__vueParentComponent; for(let i=0;c&&i<8;i++,c=c.parent){ walk(c.ctx||c.proxy,'component'+i,0); walk(c.setupState,'setup'+i,0); walk(c.data,'data'+i,0); }
      return found;
    }''')

with Session(load_config()) as s:
    p=s.open()
    # The initial default search is asynchronous.  Do not let its late
    # response be mistaken for the response caused by filter confirmation.
    p.wait_for_timeout(5000)
    p.locator('.wrap-center .all').click(); p.locator('.search-terms-drawer').wait_for(state='visible')
    drawer=p.locator('.search-terms-drawer')
    footer=drawer.locator('.footer-btn .btns button')
    print('footer buttons',footer.count())
    footer.nth(0).click(); p.wait_for_timeout(200); print('reset',flush=True)
    cols=drawer.locator('.el-col')
    # followers custom range
    follower=cols.nth(1).locator('[class*=search-wrap]')
    follower_pop=open_popover(p,follower); print('followers opened',flush=True)
    pop=follower_pop.locator('input[type=number]')
    pop.nth(0).fill('5000'); pop.nth(1).fill('5499'); print('followers filled',flush=True)
    dismiss_to_drawer_blank(drawer); print('followers set',flush=True)
    # country, language
    country=cols.nth(2).locator('[class*=search-wrap]')
    country_pop=open_popover(p,country); print('country opened',flush=True)
    trusted_option_click(p,country_pop,'us')
    dismiss_to_drawer_blank(drawer); print('country selected',flush=True)
    language=cols.nth(3).locator('[class*=search-wrap]')
    language_pop=open_popover(p,language); print('language opened',flush=True)
    trusted_option_click(p,language_pop,'en')
    dismiss_to_drawer_blank(drawer); print('language selected',flush=True)
    # recent publish is drawer col 10, not video publish-time col 7.
    recent=cols.nth(10).locator('[class*=search-wrap]')
    recent_pop=open_popover(p,recent); print('recent opened',flush=True)
    label=recent_pop.locator('label.el-radio:has(input[value="RECENT_30D"])')
    box=label.bounding_box()
    if not box: raise RuntimeError('RECENT_30D has no rendered box')
    p.mouse.click(box['x']+min(14,box['width']/2), box['y']+box['height']/2)
    dismiss_to_drawer_blank(drawer); print('recent selected',flush=True)
    state=vue_filter_state(p)
    (ROOT/'live_probe_vue_state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print('vue state candidates',state[:30],flush=True)
    with p.expect_response(lambda r: r.url.endswith('/dataService/home/search'), timeout=30000) as event:
        footer.last.click()
    # The genuine drawer confirmation closes the drawer; keep this as a hard
    # assertion so a stale/covered button can never be reported as success.
    drawer.wait_for(state='hidden',timeout=5000)
    response=event.value; data=response.json()['data']
    # Keep the exact browser-issued request body for diagnosis.  This is the
    # source of truth for whether UI selections were committed to the search.
    request_body=response.request.post_data_json
    out={'status':response.status,'count':data['count'],'hasNextPage':data['hasNextPage'],
         'request_body':request_body,'handles':[x.get('username') for x in data['bloggerList']]}
    (ROOT/'live_probe_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)
