from main import Session, load_config, ROOT
with Session(load_config()) as s:
 p=s.open(); p.wait_for_timeout(3000)
 urls=p.evaluate("() => performance.getEntriesByType('resource').map(x=>x.name).filter(x=>/\\.js($|\\?)/.test(x))")
 (ROOT/'script_urls.txt').write_text('\n'.join(urls),encoding='utf-8')
 print('\n'.join(urls))
