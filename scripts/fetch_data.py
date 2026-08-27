#!/usr/bin/env python3
"""Pull all RPM (last-Thursday) orders from the Eventbrite API and write data.json.

Runs inside GitHub Actions. Requires env var EVENTBRITE_TOKEN (a private token
from eventbrite.com/platform/api-keys, stored as an encrypted repo secret).
Emails are masked before anything is written, so data.json never contains
personal email addresses.
"""
import json, os, sys, time, calendar, collections, hashlib
import datetime as dt
import urllib.request, urllib.parse, urllib.error

TOKEN = os.environ["EVENTBRITE_TOKEN"].strip()
API = "https://www.eventbriteapi.com/v3"

def get(path, **params):
    qs = urllib.parse.urlencode(params)
    url = f"{API}{path}?{qs}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt * 5, 60)
                print("  HTTP", e.code, "on", path, "- retry", attempt + 1, "of 5 in", wait, "s")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            wait = min(2 ** attempt * 5, 60)
            print("  network error on", path, e, "- retry", attempt + 1, "of 5 in", wait, "s")
            time.sleep(wait)
    raise last

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

def ident_hash(email, first, last):
    """Stable, non-reversible person key.

    Built from the REAL email before masking, so identity survives the masking
    step and never depends on the ***2 collision suffix. Falls back to the name
    when an order carries no email. Publishing the hash rather than the address
    keeps the public data file free of any extra personal data.
    """
    key = (email or "").strip().lower()
    if not key:
        key = "name:" + " ".join(f"{first} {last}".split()).lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]

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
    # ...but those older meets still tell us whether a 2026 booker is genuinely
    # new or an old face returning. We pull them for IDENTITY ONLY: no money, no
    # tickets, nothing that reaches a chart. Keeps the pace maths on 2022+ while
    # making "first ever visit" mean what it says.
    IDENTITY_SINCE = dt.date(2019, 1, 1)
    rpm, legacy, manifest = [], [], []
    for e in events:
        d = dt.date.fromisoformat(e["start"]["local"][:10])
        name = e["name"]["text"] or ""
        # RPM normally runs on the last Thursday, but it has occasionally moved
        # (Feb and Jul 2022 ran on the 3rd Thursday). Name is the safety net so a
        # rescheduled meet is never dropped; workshops and clinics stay out.
        nl = name.lower()
        looks_like_rpm = "reading property meet" in nl or nl.startswith("rpm")
        if d < SINCE:
            reason = "before 2022"
            if d >= IDENTITY_SINCE and (d == last_thursday(d) or looks_like_rpm):
                legacy.append((e["id"], d.isoformat()))
                reason = "before 2022 - identity only"
        elif not (d == last_thursday(d) or looks_like_rpm):
            reason = "not a monthly RPM"
        else:
            reason = None
            rpm.append((e["id"], d.isoformat(), name))
        # audit trail: every event the API returned, and why it was kept or dropped,
        # so a monthly RPM can never silently vanish from the dashboard
        manifest.append({"date": d.isoformat(), "name": name[:70],
                         "included": reason is None, "reason": reason})
    manifest.sort(key=lambda x: x["date"])
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
            # The Organizer app writes checked_in onto each attendee record, so
            # this is the difference between a ticket sold and a person who
            # actually walked in. Read-only over the public API, which is all we
            # need. Without it every "times attended" figure is really "times
            # bought", and a no-show is indistinguishable from an attendee.
            ci = sum(1 for a in att if a.get("checked_in"))
            # Does any seat on this order carry a name of its own, different from
            # the buyer? If so, guests are not anonymous after all and we can name
            # them. Only the COUNT is published - a guest's name never enters this
            # public repository. add_new_people.py reads the real names from the
            # API at run time and writes them straight to monday.
            _buyer = f"{(o.get('first_name') or '').strip()} {(o.get('last_name') or '').strip()}".strip().lower()
            ng = sum(1 for a in att
                     if ((a.get("profile") or {}).get("name") or "").strip().lower()
                     not in ("", _buyer))
            city, zoom, promo, aff = "", 0, "", ""
            classes = []
            for a in att:
                tcn = a.get("ticket_class_name") or ""
                if tcn:
                    classes.append(tcn)
                tc = tcn.lower()
                if any(w in tc for w in ZOOM_WORDS):
                    zoom += 1
                if not promo:
                    pc = a.get("promotional_code")
                    promo = pc.get("code", "") if isinstance(pc, dict) else ""
                if not aff:
                    aff = a.get("affiliate") or ""
                if not city:
                    home = ((a.get("profile") or {}).get("addresses") or {}).get("home") or {}
                    city = home.get("city") or ""
            email = (o.get("email") or "").strip().lower()
            fullname = f"{(o.get('first_name') or '').strip()} {(o.get('last_name') or '').strip()}".strip().lower()
            sponsor = (email in SPONSOR_EMAILS or fullname in SPONSOR_NAMES
                       or (promo or "").lower().startswith("sponsor"))
            orders.append({
                "oid": o["id"],
                "dt": o["created"][:19].replace("T", " "),
                "first": (o.get("first_name") or "").strip(),
                "last": (o.get("last_name") or "").strip(),
                "email": email,
                "city": city,
                "eid": eid, "edate": edate, "ename": ename,
                "h": ident_hash(email, o.get("first_name"), o.get("last_name")),
                "qty": qty, "ci": ci, "ng": ng,
                "zoom": zoom, "code": promo or aff,
                "promo": promo, "aff": aff, "sp": 1 if sponsor else 0,
                "tc": "; ".join(sorted(set(classes)))[:80],
                "status": "Free Order" if gross == 0 else "Eventbrite Completed",
                "gross": round(gross, 2), "net": net,
            })
    # ---- identity-only sweep of the 2019-2021 meets ----
    prior = set()
    for eid, edate in legacy:
        for o in fetch_event_orders(eid):
            if o.get("status") != "placed":
                continue
            prior.add(ident_hash((o.get("email") or "").strip().lower(),
                                 o.get("first_name"), o.get("last_name")))
    print(f"{len(legacy)} pre-2022 meets swept for identity: {len(prior)} people seen")

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
           "priorAudience": {"from": IDENTITY_SINCE.isoformat(), "to": "2021-12-31",
                             "events": len(legacy), "ids": sorted(prior)},
           "sponsors": SPONSOR_DIRECTORY,
           "eventManifest": manifest,
           "orders": orders}
    with open("data.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("data.json written")

if __name__ == "__main__":
    sys.exit(main())
