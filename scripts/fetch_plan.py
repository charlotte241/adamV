#!/usr/bin/env python3
"""Pull the week's marketing plan from the RPM monday.com boards and write plan.json.

Requires env var MONDAY_TOKEN (a personal API token from monday.com ->
avatar -> Developers -> My access tokens, stored as an encrypted repo secret).
Exits quietly if the token is not configured, so the workflow still succeeds.
Only item names, dates, statuses, channels and owner FIRST names are published.
"""
import json, os, sys, time
import datetime as dt
import urllib.request, urllib.error

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping plan.json")
    sys.exit(0)

BOARDS = {
    # column ids verified against the live boards - they have been restructured once already
    "18393775762": {"label": "Newsletter", "date": "date4", "alt_date": "date_mkz8qkg8",
                    "status": "status", "owner": "checked_by3__1", "channel": None},
    "18393778157": {"label": "Social", "date": "date_mkvvc2q4", "alt_date": "date_mkzvrajt",
                    "status": "color_mm0mfs5m", "owner": "multiple_person_mm6daz4z",
                    "channel": "color_mm006mmw"},
    "18393924530": {"label": "WhatsApp", "date": "date_mkvvc2q4", "alt_date": None,
                    "status": "color_mkvv1ny9", "owner": "multiple_person_mkvv1k99", "channel": None},
}

# Who needs to act, per the agreed rules:
#   Lorenza posts          -> Lorenza, always
#   Needs Review / CHANGES -> Henry, the checker
#   Done on Social         -> nobody, it is finished
#   Done on WhatsApp       -> whoever posted it, Eni by default
#   Done on Newsletter     -> Hannah
#   Anything still open    -> Hannah
DEFAULT_OWNER, CHECKER, WHATSAPP_POSTER = "Hannah", "Henry", "Eni"
DONE = {"scheduled", "posted", "done", "sent", "completed"}

def responsible(board, name, status, board_owner):
    s = (status or "").strip().lower()
    if "lorenza" in (name or "").lower():
        return "Lorenza"
    if "review" in s or "change" in s:
        return CHECKER
    if s in DONE:
        if board == "Social":
            return ""
        if board == "WhatsApp":
            return board_owner or WHATSAPP_POSTER
        return DEFAULT_OWNER
    return DEFAULT_OWNER

query = """{ boards(ids:[18393775762,18393778157,18393924530]) {
  id items_page(limit:500){ items { name column_values { id text } } } } }"""
req = urllib.request.Request(
    "https://api.monday.com/v2",
    data=json.dumps({"query": query}).encode(),
    headers={"Authorization": TOKEN, "Content-Type": "application/json"})
resp = None
for attempt in range(4):
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=30))
        break
    except Exception as e:
        wait = min(2 ** attempt * 5, 40)
        print("monday fetch failed", e, "- retry", attempt + 1, "of 4 in", wait, "s")
        time.sleep(wait)

if resp is None:
    # the plan panel is non-critical: keep the existing plan.json rather than
    # failing the run and emailing a false alarm
    print("monday unreachable after retries - leaving existing plan.json in place")
    sys.exit(0)
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
        board_owner = owner_full.split(",")[0].split()[0] if owner_full else ""
        status_val = cv.get(cfg["status"], "")
        owner = responsible(cfg["label"], it["name"], status_val, board_owner)
        items.append({
            "board": cfg["label"], "name": it["name"], "date": d.isoformat(),
            "status": status_val, "owner": owner, "boardOwner": board_owner,
            "channel": (cv.get(cfg["channel"], "") if cfg["channel"] else "") or cfg["label"],
        })
items.sort(key=lambda x: x["date"])
with open("plan.json", "w") as f:
    json.dump({"snapshot": dt.datetime.utcnow().isoformat(timespec="minutes"), "items": items},
              f, separators=(",", ":"))
print(f"plan.json written: {len(items)} items")
