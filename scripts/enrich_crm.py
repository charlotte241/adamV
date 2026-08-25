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
import datetime as dt
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
EVLIST   = "text_mm6j6s45"     # Events attended (list)
SPONSOR  = "boolean_mm6jztgt"  # Power Team sponsor
BOUGHT   = "text_mm6j7dys"     # What they have bought
LIFECYCLE= "color_mm6jmajt"   # Lifecycle
GUESTS   = "numeric_mm6j6aw5"  # Guests brought
HOWATT   = "color_mm6j5ydz"    # How they attend
TRAVEL   = "color_mm6j1p5b"    # Travel
EARLY    = "color_mm6jse3v"    # Books how early
AVGLEAD  = "numeric_mm6jxmgs"  # Avg days before booking
PREDICT  = "color_mm6jvxjq"    # How predictable
BOOKHIST = "long_text_mm6jtm2j" # Booking history
USUALTIX = "color_mm6jzwz1"    # Usual ticket

def booking_window(avg):
    if avg >= 30: return "A month or more"
    if avg >= 14: return "Two to four weeks"
    if avg >= 5:  return "About a week"
    if avg >= 1:  return "Last few days"
    return "Same day"

def spread(leads):
    """Population standard deviation, without importing statistics."""
    if len(leads) < 2: return None
    m = sum(leads) / len(leads)
    return (sum((x - m) ** 2 for x in leads) / len(leads)) ** 0.5

def ticket_kind(names):
    """Collapse Eventbrite ticket names into something worth filtering on."""
    kinds = set()
    for n in names:
        s = (n or "").lower()
        if not s: continue
        if any(w in s for w in ("zoom", "online", "virtual", "livestream")): kinds.add("Zoom")
        elif "early" in s: kinds.add("Early bird")
        elif any(w in s for w in ("free", "comp", "guest", "speaker")): kinds.add("Free or comp")
        else: kinds.add("Standard")
    if not kinds: return None
    return kinds.pop() if len(kinds) == 1 else "Mixed"

# Reading and the villages people can reach without a real journey.
LOCAL = {"reading","wokingham","bracknell","earley","caversham","tilehurst","woodley",
         "winnersh","twyford","sonning","shinfield","theale","pangbourne","burghfield","calcot"}

def lifecycle(n, rec, has_future):
    """One box per person. rec is months since their last visit.

    Regular splits at 3 months so a quiet regular is visible before they are lost;
    Slipping starts at 6 months, which is the line Charlotte set.
    """
    if n == 0:
        return "Booked, not been yet" if has_future else "Never been"
    if n >= 5:
        if rec <= 3:  return "Regular"
        if rec <= 6:  return "Regular - but quiet"
        if rec <= 12: return "Slipping"
        return "Lost regular"
    if n == 1:
        if rec <= 2:  return "Just arrived"
        if rec <= 12: return "Came once, drifted"
        return "Cold - came once"
    if rec <= 2:  return "In and out"
    if rec <= 12: return "Gave up after a few"
    return "Cold - tried a few"

# From the RPM Power Team directory. Matched on name, since sponsors book with
# whatever address suits them on the night.
SPONSOR_NAMES = {
    "dean cripps","kate hulcoop-allen","katie hulcoop-allen","katie allen",
    "martin bowers","sarah gillbe","des taylor","desmond taylor","emily temple",
    "steve long","stuart stanley","jason povey","daniel norquoy","martin duncan",
}

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
             'items { id name column_values(ids:["%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s"]) '
             '{ id text } } } } }'
             ) % (BOARD, EMAIL, FULLNAME, WEBSITE, SOURCE, CITY, EVLIST, SPONSOR, BOUGHT,
                        LIFECYCLE, GUESTS, HOWATT, TRAVEL,
                        EARLY, AVGLEAD, PREDICT, BOOKHIST, USUALTIX)
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

            if orders and not cv.get(EVLIST):
                months = sorted({o["edate"] for o in orders})
                pretty = ", ".join(dt.date.fromisoformat(m).strftime("%b %y") for m in months)
                payload[EVLIST] = pretty[:250]
                filled["Events attended (list)"] += 1

            if orders and not cv.get(BOUGHT):
                tix = sum(o["qty"] for o in orders)
                spend = round(sum(o["gross"] for o in orders), 2)
                payload[BOUGHT] = f"{tix} RPM ticket{'s' if tix != 1 else ''}, GBP {spend:.2f}"
                filled["What they have bought"] += 1

            nm = " ".join(f"{it['name']}".split()).lower()
            if orders:
                o0 = orders[0]
                nm = " ".join(f"{(o0.get('first') or '')} {(o0.get('last') or '')}".split()).lower() or nm
            if (nm in SPONSOR_NAMES or any(o.get("sp") for o in orders or [])) and not cv.get(SPONSOR):
                payload[SPONSOR] = {"checked": "true"}
                filled["Power Team sponsor"] += 1

            if orders:
                evd = sorted({o["edate"] for o in orders})
                today = dt.date.today().isoformat()
                past = [e for e in evd if e <= today]
                fut = [e for e in evd if e > today]
                rec = ((dt.date.today() - dt.date.fromisoformat(past[-1])).days / 30.44
                       if past else 999)
                lc = lifecycle(len(past), rec, bool(fut))
                if cv.get(LIFECYCLE) != lc:
                    payload[LIFECYCLE] = {"label": lc}
                    filled["Lifecycle"] += 1

                extra = sum(max(0, o["qty"] - 1) for o in orders)
                if str(extra) != (cv.get(GUESTS) or "").replace(",", ""):
                    payload[GUESTS] = str(extra)
                    filled["Guests brought"] += 1

                z = sum(o["zoom"] for o in orders)
                tix = sum(o["qty"] for o in orders)
                how = "Zoom only" if z and z == tix else "Both" if z else "In the room"
                if cv.get(HOWATT) != how:
                    payload[HOWATT] = {"label": how}
                    filled["How they attend"] += 1

                twn = (cities.get(h) or "").strip().lower()
                trv = ("Town unknown" if not twn
                       else "Local to Reading" if twn in LOCAL else "Travels in")
                if cv.get(TRAVEL) != trv:
                    payload[TRAVEL] = {"label": trv}
                    filled["Travel"] += 1

                # --- booking behaviour ---
                leads, hist, names = [], [], []
                for o in sorted(orders, key=lambda x: x["dt"]):
                    ev = dt.date.fromisoformat(o["edate"])
                    bought = dt.date.fromisoformat(o["dt"][:10])
                    d = (ev - bought).days
                    if d < 0:
                        continue
                    leads.append(d)
                    names.append(o.get("tc", ""))
                    hist.append(f"{ev.strftime('%b %y')}: {d}d ahead, GBP {o['gross']:.0f}")

                if leads:
                    avg = round(sum(leads) / len(leads))
                    if str(avg) != (cv.get(AVGLEAD) or "").replace(",", ""):
                        payload[AVGLEAD] = str(avg)
                        filled["Avg days before booking"] += 1

                    win = booking_window(avg) if len(leads) > 1 else "Only booked once"
                    if cv.get(EARLY) != win:
                        payload[EARLY] = {"label": win}
                        filled["Books how early"] += 1

                    sd = spread(leads)
                    pred = ("Only one booking" if sd is None
                            else "Predictable" if sd < 7
                            else "Fairly steady" if sd < 14 else "Varies a lot")
                    if cv.get(PREDICT) != pred:
                        payload[PREDICT] = {"label": pred}
                        filled["How predictable"] += 1

                    line = " · ".join(hist)[:1900]
                    if cv.get(BOOKHIST) != line:
                        payload[BOOKHIST] = line
                        filled["Booking history"] += 1

                    kind = ticket_kind(names)
                    if kind and cv.get(USUALTIX) != kind:
                        payload[USUALTIX] = {"label": kind}
                        filled["Usual ticket"] += 1

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
