#!/usr/bin/env python3
"""Push Eventbrite attendance onto the RPM Outreach CRM board.

Runs after fetch_data.py in the same workflow, so data.json is already current.
Matches a board row to a person by hashing the row's email the same way
fetch_data.py hashes the order email - no name matching, no fuzzy guessing.

Writes SIX columns only, all of which the team never edits by hand:
    First visit · Times attended · Last attended · Total spent
    Attendance status · Brings guests

Everything a human types - Best fit, Owner, Next action, Relationship stage,
notes, replies - is never touched.

Requires MONDAY_TOKEN. Exits 0 and changes nothing if it cannot run, so a
monday outage never fails the dashboard build.
"""
import json, os, sys, time, hashlib
import datetime as dt
import urllib.request, urllib.error

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping CRM sync")
    sys.exit(0)

BOARD = "18422366230"
COL = {
    "first":  "date_mm6htxkc",     # First visit
    "times":  "numeric_mm6h749y",  # Times attended
    "last":   "date_mm6haf03",     # Last attended
    "spent":  "numeric_mm6h2g6t",  # Total spent
    "status": "color_mm6h4j9k",    # Attendance status
    "guest":  "boolean_mm6he4jy",  # Brings guests
}
EMAIL_COL = "email_mm5adkxb"
LAPSED_AFTER_DAYS = 120            # not seen in ~4 cycles
REGULAR_AT = 5                     # events attended

def api(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "errors" in r:
                raise RuntimeError(r["errors"])
            return r["data"]
        except Exception as e:
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday call failed ({e}) - retry {attempt+1}/4 in {wait}s")
            time.sleep(wait)
    raise RuntimeError("monday unreachable after retries")

def ident_hash(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]

def classify(evs, spend, today):
    """One label per person, evaluated in order - each is mutually exclusive."""
    if not evs:
        return "Never been"
    future = [e for e in evs if e > today.isoformat()]
    if future:
        return "Booked ahead"
    past = [e for e in evs if e <= today.isoformat()]
    if not past:
        return "Never been"
    gap = (today - dt.date.fromisoformat(past[-1])).days
    if gap > LAPSED_AFTER_DAYS:
        return "Lapsed"
    if len(past) >= REGULAR_AT:
        return "Regular"
    if dt.date.fromisoformat(past[0]).year == today.year:
        return "New this year"
    return "Occasional"

def main():
    try:
        data = json.load(open("data.json"))
    except FileNotFoundError:
        print("data.json missing - run fetch_data.py first")
        return 0

    # person hash -> their orders
    people = {}
    for o in data["orders"]:
        people.setdefault(o["h"], []).append(o)
    print(f"{len(people)} distinct ticket buyers in Eventbrite data")

    # pull the board
    items, cursor = [], None
    while True:
        q = ("query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor "
             "items { id column_values(ids:[\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\"]) "
             "{ id text } } } } }") % (BOARD, EMAIL_COL, COL["first"], COL["times"],
                                       COL["last"], COL["spent"], COL["status"], COL["guest"])
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(items)} rows on the CRM board")

    today = dt.date.today()
    updates, matched, no_email, unknown = [], 0, 0, 0
    for it in items:
        cv = {c["id"]: (c["text"] or "") for c in it["column_values"]}
        email = cv.get(EMAIL_COL, "").strip()
        if not email:
            no_email += 1
            continue
        orders = people.get(ident_hash(email))
        if not orders:
            unknown += 1
            want = {COL["status"]: "Never been"}
        else:
            matched += 1
            evs = sorted({o["edate"] for o in orders})
            past = [e for e in evs if e <= today.isoformat()]
            spend = round(sum(o["gross"] for o in orders), 2)
            want = {
                COL["first"]:  evs[0],
                COL["times"]:  str(len(past)),
                COL["last"]:   (past[-1] if past else ""),
                COL["spent"]:  str(spend),
                COL["status"]: classify(evs, spend, today),
                COL["guest"]:  ("v" if any(o["qty"] > 1 for o in orders) else ""),
            }
        # only write what actually differs, so the board's activity log stays readable
        payload = {}
        for cid, val in want.items():
            cur = cv.get(cid, "")
            if cid == COL["guest"]:
                if bool(cur) != bool(val):
                    payload[cid] = {"checked": "true"} if val else {"checked": "false"}
            elif cid in (COL["first"], COL["last"]):
                if cur[:10] != val:
                    payload[cid] = {"date": val} if val else {}
            elif cid == COL["status"]:
                if cur != val:
                    payload[cid] = {"label": val}
            else:
                if (cur or "").replace(",", "") != val:
                    payload[cid] = val
        if payload:
            updates.append((it["id"], payload))

    print(f"matched {matched} · not ticket buyers {unknown} · no email on row {no_email}")
    print(f"{len(updates)} rows need changing")

    # batched aliased mutations - 12 per request keeps well inside complexity limits
    done = 0
    for i in range(0, len(updates), 12):
        chunk = updates[i:i+12]
        parts, variables = [], {}
        for n, (iid, payload) in enumerate(chunk):
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'm{n}: change_multiple_column_values(board_id: {BOARD}, '
                         f'item_id: {iid}, column_values: $v{n}) {{ id }}')
        sig = ", ".join(f"$v{n}: JSON!" for n in range(len(chunk)))
        api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        print(f"  written {done}/{len(updates)}")
        time.sleep(0.4)

    print("CRM sync complete")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # never fail the workflow over the CRM sync - the dashboard matters more
        print(f"CRM sync failed, leaving board untouched: {e}")
        sys.exit(0)
