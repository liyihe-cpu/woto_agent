from main import Session, load_config
import json
with Session(load_config()) as s:
 def resp(r):
  if r.url.endswith('/dataService/home/search'):
   try:
    open('search_response.json','w',encoding='utf-8').write(r.text())
    print('captured search response',r.status,flush=True)
   except Exception as e: print('response capture error',e,flush=True)
 s.page.on('response',resp)
 p=s.open(); p.wait_for_timeout(1800)
