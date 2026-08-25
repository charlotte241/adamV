#!/usr/bin/env python3
"""Fill in what we actually know about each person on the RPM Outreach board.

The bar is 80% confidence. Only four fields clear it, and each comes from the
person themselves rather than from inference:

  Full Name   the name they gave Eventbrite when they paid
  Town/City   the town they gave at checkout
  Website     their own email domain, where that domain is not a free provider
  Source      "Bought a ticket", set only where we can match them to an order

Deliberately NOT filled, because nothing available reaches 80%:

  Company Name  a squashed domain gives "Morsewebb" and "Ramsayandwhite".
                Right company, wrong text - and only 7% of domains carry a
                separator that makes the word breaks knowable.
  Category      only 3% of domains contain an unambiguous trade word, and
                almost all of those are the scraped construction URLs.
  Phone         the Eventbrite export carries none.

A cell is only ever written when it is EMPTY. Anything a human typed wins.
"""
import json, os, sys, time, re, hashlib, collections
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping enrichment")
    sys.exit(0)

BOARD = "18422366230"
EMAIL    = "email_mm5adkxb"
FULLNAME = "text_mm5b3my9"
WEBSITE  = "link_mm5b1y5g"
SOURCE   = "color_mm6h8m86"
CITY     = "text_mm6jc0wa"

FREE = {"gmail","googlemail","hotmail","outlook","live","yahoo","icloud","me","aol","msn",
        "btinternet","sky","virginmedia","protonmail","proton","mail","ymail","rocketmail",
        "gmx","talktalk","blueyonder","tiscali","8alias","yopmail","zoho","fastmail","aim",
        "hotmail","gmai","googlemai","yahoo","live","email"}
TWO_PART = {"co.uk","org.uk","ac.uk","me.uk","ltd.uk","plc.uk","net.uk","gov.uk","sch.uk",
            "com.au","co.za","co.nz","co.in"}

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
    raise RuntimeError("monday unreachable")

def ident_hash(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]

def domain(email):
    return email.split("@")[-1].lower().strip().strip(".")

def registrable(d):
    p = d.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in TWO_PART:
        return p[-3]
    if len(p) >= 2:
        return p[-2]
    return p[0] if p else ""

def business_site(email):
    """Their own domain, or None for a free mailbox. This is the person telling
    us where they work, so it needs no inference."""
    d = domain(email)
    if not d or "." not in d:
        return None
    if registrable(d) in FREE:
        return None
    return "https://" + d

def main():
    try:
        data = json.load(open("data.json"))
    except FileNotFoundError:
        print("data.json missing - run fetch_data.py first")
        return 0
    try:
        cities = json.load(open("city_lookup.json"))
    except FileNotFoundError:
        cities = {}
        print("city_lookup.json missing - Town/City will be skipped")

    people = collections.defaultdict(list)
    for o in data["orders"]:
        people[o["h"]].append(o)
    print(f"{len(people)} ticket buyers known · {len(cities)} towns on file\n")

    items, cursor = [], None
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor '
             'items { id name column_values(ids:["%s","%s","%s","%s","%s"]) { id text } } } } }'
             ) % (BOARD, EMAIL, FULLNAME, WEBSITE, SOURCE, CITY)
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(items)} rows on the board")

    filled = collections.Counter()
    skipped = collections.Counter()
    updates = []
    for it in items:
        cv = {c["id"]: (c["text"] or "").strip() for c in it["column_values"]}
        email = cv.get(EMAIL, "")
        payload = {}

        if not email:
            skipped["no email on the row"] += 1
        else:
            h = ident_hash(email)
            orders = people.get(h)

            if orders and not cv.get(FULLNAME):
                o = orders[0]
                nm = f"{(o.get('first') or '').strip()} {(o.get('last') or '').strip()}".strip()
                if nm:
                    payload[FULLNAME] = nm
                    filled["Full Name"] += 1

            if orders and not cv.get(SOURCE):
                payload[SOURCE] = {"label": "Bought a ticket"}
                filled["Source"] += 1

            town = cities.get(h)
            if town and not cv.get(CITY):
                payload[CITY] = town
                filled["Town/City"] += 1
            elif not town:
                skipped["no town on file"] += 1

            site = business_site(email)
            if site and not cv.get(WEBSITE):
                payload[WEBSITE] = {"url": site, "text": domain(email)}
                filled["Website"] += 1
            elif not site:
                skipped["free mailbox - no company site"] += 1

        if payload:
            updates.append((it["id"], payload))

    print("\nWILL FILL")
    for k, v in filled.most_common():
        print(f"  {k:<12} {v}")
    print("\nLEFT BLANK ON PURPOSE")
    for k, v in skipped.most_common():
        print(f"  {k:<34} {v}")
    print(f"\n{len(updates)} rows to update\n")

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
    print("enrichment complete")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"enrichment failed, board untouched: {e}")
        sys.exit(0)
