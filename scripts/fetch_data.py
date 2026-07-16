#!/usr/bin/env python3
"""Pull all RPM (last-Thursday) orders from the Eventbrite API and write data.json.

Runs inside GitHub Actions. Requires env var EVENTBRITE_TOKEN (a private token
from eventbrite.com/platform/api-keys, stored as an encrypted repo secret).
Emails are masked before anything is written, so data.json never contains
personal email addresses.
"""
import json, os, sys, calendar, collections
import datetime as dt
import urllib.request, urllib.parse

TOKEN = os.environ["EVENTBRITE_TOKEN"].strip()
API = "https://www.eventbriteapi.com/v3"

def get(path, **params):
    qs = urllib.parse.urlencode(params)
    url = f"{API}{path}?{qs}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def paged(path, key, **params):
    page = 1
    while True:
        r = get(path, page=page, **params)
        yield from r[key]
        if not r.get("pagination", {}).get("has_more_items"):
            break
        page += 1

def last_thursday(d):
    last = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return last - dt.timedelta(days=(last.weekday() - 3) % 7)

def mask(email):
    if "@" not in email:
        return email
    local, dom = email.split("@", 1)
    keep = local[:2] if len(local) > 2 else local[:1]
    return f"{keep}***@{dom}"

def main():
    org = get("/users/me/organizations/")["organizations"][0]["id"]

    events = list(paged(f"/organizations/{org}/events/", "events",
                        order_by="start_asc", time_filter="all"))
    SINCE = dt.date(2022, 1, 1)          # 2019-2021 excluded (Covid era, free events)
    rpm = []
    for e in events:
        d = dt.date.fromisoformat(e["start"]["local"][:10])
        if d >= SINCE and d == last_thursday(d):   # monthly RPM only - skips one-off workshops
            rpm.append((e["id"], d.isoformat(), e["name"]["text"] or ""))
    print(f"{len(events)} events found, {len(rpm)} are last-Thursday RPMs")

    ZOOM_WORDS = ("zoom", "online", "virtual", "livestream", "live stream")

    # Power Team sponsors (from RPM-Power-Team-Directory) - flagged so the
    # dashboard doesn't count them as loyalist attendees
    SPONSOR_DIRECTORY = [
        {"name": "Dean Cripps", "biz": "Ramsay & White", "cat": "Mortgage & Finance Broker"},
        {"name": "Kate Hulcoop-Allen", "biz": "Simply Seven", "cat": "Bookkeeping & Xero"},
        {"name": "Martin Bowers", "biz": "Bowers Broker Services", "cat": "Property Insurance Broker"},
        {"name": "Sarah Gillbe", "biz": "Setfords Solicitors", "cat": "Property Solicitor"},
        {"name": "Des Taylor", "biz": "Landlords Defence", "cat": "Licensing & Defence"},
        {"name": "Emily Temple", "biz": "ET Planning", "cat": "Town Planning Consultant"},
        {"name": "Steve Long", "biz": "KSM Remedial", "cat": "Builder & Damp Specialist"},
        {"name": "Stuart Stanley", "biz": "Stanley Electrical", "cat": "Electrician (NICEIC)"},
        {"name": "Jason Povey", "biz": "JP Fire & Security", "cat": "Fire & Security"},
        {"name": "Daniel Norquoy", "biz": "Voila Solutions", "cat": "Virtual Assistants"},
        {"name": "Martin Duncan", "biz": "Waste Clearance & Removals", "cat": "Waste & Clearance"},
    ]
    SPONSOR_EMAILS = {
        "dean@ramsayandwhite.com", "kate@simplyseven.co.uk",
        "info@bowersbrokerservices.co.uk", "sgillbe@setfords.co.uk",
        "des.taylor@landlordsdefence.co.uk", "emily.temple@etplanning.co.uk",
        "info@ksmremedial.co.uk", "stuart@stanleyelectricalservices.com",
        "info@jpfiresecurity.co.uk", "info@voilasolutions.co.uk",
        "martin.duncan5@btinternet.com",
    }
    SPONSOR_NAMES = {
        "dean cripps", "kate hulcoop-allen", "katie hulcoop-allen", "katie allen",
        "martin bowers", "sarah gillbe", "des taylor", "desmond taylor",
        "emily temple", "steve long", "stuart stanley", "jason povey",
        "daniel norquoy", "martin duncan",
    }

    def fetch_event_orders(eid):
        # richer expansion first (promo codes); fall back if the API rejects it
        for expand in ("attendees,attendees.promotional_code", "attendees"):
            try:
                return list(paged(f"/events/{eid}/orders/", "orders", expand=expand))
            except Exception as ex:
                print(f"  expand '{expand}' failed for {eid}: {ex}")
        return []

    orders = []
    for eid, edate, ename in rpm:
        for o in fetch_event_orders(eid):
            if o.get("status") != "placed":
                continue                  # skips refunded / abandoned
            costs = o.get("costs") or {}
            mv = lambda k: float((costs.get(k) or {}).get("major_value") or 0)
            gross = mv("gross")
            net = round(gross - mv("eventbrite_fee") - mv("payment_fee") - mv("tax"), 2)
            att = [a for a in (o.get("attendees") or []) if not a.get("cancelled")]
            qty = len(att) or 1
            city, zoom, code = "", 0, ""
            for a in att:
                tc = (a.get("ticket_class_name") or "").lower()
                if any(w in tc for w in ZOOM_WORDS):
                    zoom += 1
                if not code:
                    pc = a.get("promotional_code")
                    code = (pc.get("code", "") if isinstance(pc, dict) else "") or a.get("affiliate") or ""
                if not city:
                    home = ((a.get("profile") or {}).get("addresses") or {}).get("home") or {}
                    city = home.get("city") or ""
            email = (o.get("email") or "").strip().lower()
            fullname = f"{(o.get('first_name') or '').strip()} {(o.get('last_name') or '').strip()}".strip().lower()
            sponsor = (email in SPONSOR_EMAILS or fullname in SPONSOR_NAMES
                       or code.lower().startswith("sponsor"))
            orders.append({
                "oid": o["id"],
                "dt": o["created"][:19].replace("T", " "),
                "first": (o.get("first_name") or "").strip(),
                "last": (o.get("last_name") or "").strip(),
                "email": email,
                "city": city,
                "eid": eid, "edate": edate, "ename": ename,
                "qty": qty, "zoom": zoom, "code": code, "sp": 1 if sponsor else 0,
                "status": "Free Order" if gross == 0 else "Eventbrite Completed",
                "gross": round(gross, 2), "net": net,
            })
    orders.sort(key=lambda o: o["dt"])
    print(f"{len(orders)} orders, {sum(o['qty'] for o in orders)} tickets")

    # mask emails, keeping one stable masked value per real address
    mapping, used = {}, collections.Counter()
    for em in dict.fromkeys(o["email"] for o in orders if o["email"]):
        m = mask(em)
        used[m] += 1
        mapping[em] = m if used[m] == 1 else m.replace("***", f"***{used[m]}")
    for o in orders:
        if o["email"]:
            o["email"] = mapping[o["email"]]

    out = {"snapshot": dt.datetime.utcnow().isoformat(timespec="minutes"),
           "sponsors": SPONSOR_DIRECTORY,
           "orders": orders}
    with open("data.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("data.json written")

if __name__ == "__main__":
    sys.exit(main())
