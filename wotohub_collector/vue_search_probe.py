from main import Session, load_config, ROOT
import json
with Session(load_config()) as s:
    p=s.open(); p.wait_for_timeout(5000)
    with p.expect_response(lambda r:r.url.endswith('/dataService/home/search'),timeout=30000) as event:
        p.evaluate('''async () => {
          let x, seen=new Set();
          for(const el of document.querySelectorAll('*')) {
            const c=el.__vue__||el.__vueParentComponent;
            const candidate=c?.proxy||c?.ctx||c;
            if(candidate && !seen.has(candidate) && candidate.queryParams && typeof candidate.getBloggerList==='function') { x=candidate; break; }
            if(candidate) seen.add(candidate);
          }
          if(!x?.queryParams || typeof x.getBloggerList!=='function') throw Error('search Vue parent unavailable');
          const group=x.countryObj.optionsArr.find(g=>g.optionsItem.some(o=>o.id==='us'));
          if(!group) throw Error('country group for us unavailable');
          Object.assign(x.queryParams,{regionList:[{id:group.id,country:['us']}],blogLangs:['en'],minFansNum:5000,maxFansNum:5499,searchRecent:'RECENT_30D',pageNum:1});
          await x.getBloggerList();
        }''')
    r=event.value; raw=r.json(); d=raw['data']; body=r.request.post_data_json
    if not d: raise RuntimeError(raw.get('message','empty data'))
    out={'body':body,'count':d['count'],'hasNextPage':d['hasNextPage'],'handles':[x.get('username') for x in d['bloggerList']], 'fans':[x.get('fansNum') for x in d['bloggerList']]}
    (ROOT/'vue_search_probe_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)
