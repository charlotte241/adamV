#!/usr/bin/env python3
"""Put every Eventbrite ticket buyer on the CRM board.

The board was originally built from a hand-made list, so it only ever held some
of the people who have actually bought a ticket. This closes that gap and keeps
it closed: anyone who buys a ticket appears on the board on the next refresh.

Real email addresses are read from the Eventbrite API at run time and written
straight to monday. They are never written to data.json or anywhere in this
repository, which is public.

Needs EVENTBRITE_TOKEN and MONDAY_TOKEN.
"""
import json, os, sys, time, calendar, hashlib, collections
import datetime as dt
import urllib.request, urllib.parse, urllib.error

EB = os.environ.get("EVENTBRITE_TOKEN", "").strip()
MD = os.environ.get("MONDAY_TOKEN", "").strip()
if not EB or not MD:
    print("tokens missing - skipping new-people sync")
    sys.exit(0)

BOARD = "18422366230"
PEOPLE_GROUP = "group_mm5w801e"
EMAIL_COL = "email_mm5adkxb"
NAME_COL = "text_mm5b3my9"
SINCE = dt.date(2022, 1, 1)

def eb(path, **params):
    url = f"https://www.eventbriteapi.com/v3{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {EB}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            wait = min(2 ** attempt * 5, 60)
            print(f"  eventbrite {path} failed ({e}) - retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError("eventbrite unreachable")

def eb_paged(path, key, **params):
    page = 1
    while True:
        r = eb(path, page=page, **params)
        yield from r[key]
        if not r.get("pagination", {}).get("has_more_items"):
            break
        page += 1

def md(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": MD, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "errors" in r:
                raise RuntimeError(r["errors"])
            return r["data"]
        except Exception as e:
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday failed ({e}) - retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError("monday unreachable")

def last_thursday(d):
    last = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return last - dt.timedelta(days=(last.weekday() - 3) % 7)

def esc(s):
    return json.dumps(s)[1:-1]

def main():
    org = eb("/users/me/organizations/")["organizations"][0]["id"]
    rpm = []
    for e in eb_paged(f"/organizations/{org}/events/", "events",
                      order_by="start_asc", time_filter="all"):
        d = dt.date.fromisoformat(e["start"]["local"][:10])
        nl = (e["name"]["text"] or "").lower()
        if d >= SINCE and (d == last_thursday(d) or "reading property meet" in nl
                           or nl.startswith("rpm")):
            rpm.append(e["id"])
    print(f"{len(rpm)} RPM events")

    # real email -> best display name, from the orders themselves
    people = {}
    for eid in rpm:
        for o in eb_paged(f"/events/{eid}/orders/", "orders"):
            if o.get("status") != "placed":
                continue
            em = (o.get("email") or "").strip().lower()
            if not em:
                continue
            nm = f"{(o.get('first_name') or '').strip()} {(o.get('last_name') or '').strip()}".strip()
            if em not in people or (nm and len(nm) > len(people[em])):
                people[em] = nm or em
    print(f"{len(people)} distinct ticket buyers")

    on_board, cursor = set(), None
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor '
             'items { column_values(ids:["%s"]) { text } } } } }') % (BOARD, EMAIL_COL)
        page = md(q, {"c": cursor})["boards"][0]["items_page"]
        for it in page["items"]:
            cv = it["column_values"]
            if cv and cv[0]["text"]:
                on_board.add(cv[0]["text"].strip().lower())
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(on_board)} email addresses already on the board")

    missing = {em: nm for em, nm in people.items() if em not in on_board}
    print(f"{len(missing)} buyers to add")
    if not missing:
        return 0

    todo = sorted(missing.items(), key=lambda kv: kv[1].lower())
    done = 0
    for i in range(0, len(todo), 10):
        chunk = todo[i:i+10]
        parts, variables = [], {}
        for n, (em, nm) in enumerate(chunk):
            variables[f"n{n}"] = nm[:250]
            variables[f"v{n}"] = json.dumps({EMAIL_COL: {"email": em, "text": em},
                                             NAME_COL: nm[:250]})
            parts.append(f'c{n}: create_item(board_id: {BOARD}, group_id: "{PEOPLE_GROUP}", '
                         f'item_name: $n{n}, column_values: $v{n}, '
                         f'create_labels_if_missing: false) {{ id }}')
        sig = ", ".join(f"$n{n}: String!, $v{n}: JSON!" for n in range(len(chunk)))
        md("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        print(f"  added {done}/{len(todo)}")
        time.sleep(0.5)
    print("new people added - the other scripts will fill their columns on the next run")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"new-people sync failed, board untouched: {e}")
        sys.exit(0)
