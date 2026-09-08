from main import Session, load_config
with Session(load_config()) as s:
 p=s.open(); p.wait_for_timeout(4000)
 print(p.evaluate('''() => { const seen=new Set(); for(const e of document.querySelectorAll('*')){let c=e.__vue__||e.__vueParentComponent,x=c?.proxy||c?.ctx||c;if(!x||seen.has(x))continue;seen.add(x);if(x.queryParams&&typeof x.getBloggerList==='function')return x.countryObj.optionsArr;} return null}'''))
