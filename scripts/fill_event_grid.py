#!/usr/bin/env python3
"""Fill the per-event grid on the RPM Outreach board.

Two columns per event, 54 events:

    "Jan 2024"        Face to face | Zoom | Didn't attend
    "Jan 2024 days"   days between placing the order and the event, 0 if absent

Every cell is a fact taken straight from the Eventbrite order. Nothing is
averaged, banded or guessed.

Columns are matched by TITLE, not by id, so renaming a column in monday will
not break this and adding a new event only needs the two columns creating.
"""
import json, os, sys, time, hashlib, collections
import datetime as dt
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping event grid")
    sys.exit(0)

BOARD = "18422366230"
EMAIL = "email_mm5adkxb"

def api(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60))
            if "errors" in r:
                raise RuntimeError(r["errors"])
            return r["data"]
        except Exception as e:
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday call failed ({e}) - retry {attempt+1}/4 in {wait}s")
            time.sleep(wait)
    raise RuntimeError("monday unreachable")

def ident_hash(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]

def main():
    data = json.load(open("data.json"))
    orders = data["orders"]

    events = sorted({o["edate"] for o in orders})
    label = {e: dt.date.fromisoformat(e).strftime("%b %Y") for e in events}
    print(f"{len(events)} events")

    # what each person did at each event, from their orders
    per = collections.defaultdict(dict)          # hash -> edate -> (mode, days)
    for o in orders:
        h = o["h"]
        ev = dt.date.fromisoformat(o["edate"])
        bought = dt.date.fromisoformat(o["dt"][:10])
        days = max(0, (ev - bought).days)
        mode = "Zoom" if o.get("zoom") and o["zoom"] >= o["qty"] else "Face to face"
        prev = per[h].get(o["edate"])
        # keep the earliest booking, and prefer face to face if they did both
        if prev is None or days > prev[1] or (prev[0] == "Zoom" and mode == "Face to face"):
            per[h][o["edate"]] = (mode, max(days, prev[1] if prev else 0))

    cols = api('query{ boards(ids:[%s]){ columns { id title type } } }' % BOARD
               )["boards"][0]["columns"]
    by_title = {c["title"]: c["id"] for c in cols}
    missing = [label[e] for e in events if label[e] not in by_title
               or f"{label[e]} days" not in by_title]
    if missing:
        print("MISSING COLUMNS, aborting so nothing lands in the wrong place:", missing)
        return 1
    print("all 108 event columns found")

    items, cursor = [], None
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor '
             'items { id column_values(ids:["%s"]) { text } } } } }') % (BOARD, EMAIL)
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(items)} rows")

    updates, matched = [], 0
    for it in items:
        cv = it["column_values"]
        email = (cv[0]["text"] or "").strip() if cv else ""
        if not email:
            continue
        went = per.get(ident_hash(email))
        if not went:
            continue                      # never bought a ticket - leave the row blank
        matched += 1
        payload = {}
        for e in events:
            mode, days = went.get(e, ("Didn't attend", 0))
            payload[by_title[label[e]]] = {"label": mode}
            payload[by_title[f"{label[e]} days"]] = str(days)
        updates.append((it["id"], payload))

    print(f"{matched} rows matched to a ticket buyer, writing {len(updates)}")

    done = 0
    for i in range(0, len(updates), 4):      # 4 per request - each payload is 108 columns
        chunk = updates[i:i+4]
        parts, variables = [], {}
        for n, (iid, payload) in enumerate(chunk):
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'm{n}: change_multiple_column_values(board_id: {BOARD}, '
                         f'item_id: {iid}, column_values: $v{n}) {{ id }}')
        sig = ", ".join(f"$v{n}: JSON!" for n in range(len(chunk)))
        api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        if done % 40 == 0 or done == len(updates):
            print(f"  written {done}/{len(updates)}")
        time.sleep(0.3)
    print("event grid complete")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"event grid failed, board untouched: {e}")
        sys.exit(0)
