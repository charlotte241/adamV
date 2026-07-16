#!/usr/bin/env python3
"""Pull the week's marketing plan from the RPM monday.com boards and write plan.json.

Requires env var MONDAY_TOKEN (a personal API token from monday.com ->
avatar -> Developers -> My access tokens, stored as an encrypted repo secret).
Exits quietly if the token is not configured, so the workflow still succeeds.
Only item names, dates, statuses, channels and owner FIRST names are published.
"""
import json, os, sys
import datetime as dt
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping plan.json")
    sys.exit(0)

BOARDS = {
    "18393775762": {"label": "Newsletter", "date": "date4", "alt_date": "date_mkz8qkg8",
                    "status": "status", "owner": "multiple_person_mm1r847t", "channel": None},
    "18393778157": {"label": "Social", "date": "date_mkvvc2q4", "alt_date": None,
                    "status": "color_mkvv1ny9", "owner": "multiple_person_mkvv1k99", "channel": "color_mm006mmw"},
    "18393924530": {"label": "WhatsApp", "date": "date_mkvvc2q4", "alt_date": None,
                    "status": "color_mkvv1ny9", "owner": "multiple_person_mkvv1k99", "channel": None},
}

query = """{ boards(ids:[18393775762,18393778157,18393924530]) {
  id items_page(limit:500){ items { name column_values { id text } } } } }"""
req = urllib.request.Request(
    "https://api.monday.com/v2",
    data=json.dumps({"query": query}).encode(),
    headers={"Authorization": TOKEN, "Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req, timeout=60))
if "errors" in resp:
    print("monday API error:", resp["errors"]); sys.exit(1)

today = dt.date.today()
lo, hi = today - dt.timedelta(days=14), today + dt.timedelta(days=21)
items = []
for b in resp["data"]["boards"]:
    cfg = BOARDS[str(b["id"])]
    for it in b["items_page"]["items"]:
        cv = {c["id"]: (c["text"] or "") for c in it["column_values"]}
        dstr = cv.get(cfg["date"]) or (cv.get(cfg["alt_date"]) if cfg["alt_date"] else "")
        if not dstr:
            continue
        try:
            d = dt.date.fromisoformat(dstr[:10])
        except ValueError:
            continue
        if not (lo <= d <= hi):
            continue
        owner_full = cv.get(cfg["owner"], "")
        owner = owner_full.split(",")[0].split()[0] if owner_full else ""
        items.append({
            "board": cfg["label"], "name": it["name"], "date": d.isoformat(),
            "status": cv.get(cfg["status"], ""), "owner": owner,
            "channel": (cv.get(cfg["channel"], "") if cfg["channel"] else "") or cfg["label"],
        })
items.sort(key=lambda x: x["date"])
with open("plan.json", "w") as f:
    json.dump({"snapshot": dt.datetime.utcnow().isoformat(timespec="minutes"), "items": items},
              f, separators=(",", ":"))
print(f"plan.json written: {len(items)} items")
