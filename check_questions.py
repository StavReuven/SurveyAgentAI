# -*- coding: utf-8 -*-
import urllib.request, json
resp = urllib.request.urlopen("http://localhost:8000/api/campaigns/9/questions")
questions = json.loads(resp.read())
for q in questions:
    print(q['prompt'])
