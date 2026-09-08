import json, re
x=json.load(open('all_probe.json'))[0]['html']; i=x.find('radio-search-wrap'); z=x[i:i+30000]
for m in re.findall(r'<(?:input|button)[^>]+>',z): print(m.encode('ascii','backslashreplace').decode())
