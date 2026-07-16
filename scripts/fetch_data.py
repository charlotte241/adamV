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
    rpm = []
    for e in events:
        d = dt.date.fromisoformat(e["start"]["local"][:10])
        if d == last_thursday(d):        # monthly RPM only - skips one-off workshops
            rpm.append((e["id"], d.isoformat(), e["name"]["text"] or ""))
    print(f"{len(events)} events found, {len(rpm)} are last-Thursday RPMs")

    orders = []
    for eid, edate, ename in rpm:
        for o in paged(f"/events/{eid}/orders/", "orders", expand="attendees"):
            if o.get("status") != "placed":
                continue                  # skips refunded / abandoned
            costs = o.get("costs") or {}
            mv = lambda k: float((costs.get(k) or {}).get("major_value") or 0)
            gross = mv("gross")
            net = round(gross - mv("eventbrite_fee") - mv("payment_fee") - mv("tax"), 2)
            att = [a for a in (o.get("attendees") or []) if not a.get("cancelled")]
            qty = len(att) or 1
            city = ""
            for a in att:
                home = ((a.get("profile") or {}).get("addresses") or {}).get("home") or {}
                if home.get("city"):
                    city = home["city"]; break
            orders.append({
                "oid": o["id"],
                "dt": o["created"][:19].replace("T", " "),
                "first": (o.get("first_name") or "").strip(),
                "last": (o.get("last_name") or "").strip(),
                "email": (o.get("email") or "").strip().lower(),
                "city": city,
                "eid": eid, "edate": edate, "ename": ename,
                "qty": qty,
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
           "orders": orders}
    with open("data.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("data.json written")

if __name__ == "__main__":
    sys.exit(main())
