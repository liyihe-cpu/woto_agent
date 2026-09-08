import urllib.request
urls=open('script_urls.txt',encoding='utf-8').read().splitlines()
for url in urls:
    src=urllib.request.urlopen(url).read().decode('utf-8','ignore')
    for needle in ('allInnerParams','searchParams','confirmSearch'):
        pos=src.find(needle)
        if pos >= 0:
            print('URL',url,'NEEDLE',needle)
            print(src[max(0,pos-1000):pos+2500].encode('ascii','backslashreplace').decode())
