# -*- coding: utf-8 -*-
import urllib.request, json, time

BASE = "http://localhost:8000"

def post(url, data=None):
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

phones = ["+972-50-500-0001", "+972-50-500-0002", "+972-50-500-0003"]
for phone in phones:
    sess = post("/api/campaigns/9/voice/sessions", {"participant_phone": phone})
    sid = sess["session_id"]
    post(f"/api/sessions/{sid}/demo-run")
    print(f"{phone} -> {sid}")
    time.sleep(4)
