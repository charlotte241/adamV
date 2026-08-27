#!/usr/bin/env python3
"""Fill in what we actually know about each person on the RPM Outreach board.

    Full Name            the name they gave Eventbrite when they paid
    Town/City            the town they gave at checkout
    Travel               Local to Reading / Travels in, from that town
    Website              their own email domain, where it is not a free mailbox
    Source               "Bought a ticket", only where an order matches
    Lifecycle            from events they turned up to and how long ago
    Guests brought       places paid for beyond their own seat
    Power Team sponsor   ticked where a ticket was a Power Team or sponsor ticket

Lifecycle is built from attendances, not ticket sales. Someone who books every
month and never comes is not a regular, and before check-in data was read this
board could not tell the difference. A ticket for an event that has not
happened yet still counts as coming back - "Booked again" - because they are in
the room next time.

The ticket history is written as an update on the item rather than a column,
because a monday text cell stops at 2,000 characters and silently cut the
oldest tickets off the longest-standing members. It separates what they
attended from tickets that went unused and tickets for nights still to come.

A person is matched on every address on their row - "Email" and anything in
"Other emails".

Full Name, Town/City and Website are only written when the cell is EMPTY, so
anything a human typed wins.
"""
import json, sys, re, time, collections
import datetime as dt
import mdlib as m

WANT = {
    "name":    "Full Name",
    "web":     "Website",
    "source":  "Source",
    "city":    "Town/City",
    "life":    "Lifecycle",
    "guests":  "Guests brought",
    "sponsor": "Power Team sponsor",
    "travel":  "Travel",
}
NEVER_OVERWRITE = {"name", "web", "city"}
MARKER = "Ticket history"

FREE = {"gmail", "googlemail", "hotmail", "outlook", "live", "yahoo", "icloud", "me",
        "aol", "msn", "btinternet", "sky", "virginmedia", "protonmail", "proton",
        "mail", "ymail", "rocketmail", "gmx", "talktalk", "blueyonder", "tiscali",
        "yopmail", "zoho", "fastmail", "aim", "email", "gmai", "googlemai"}
TWO_PART = {"co.uk", "org.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk",
            "gov.uk", "sch.uk", "com.au", "co.za", "co.nz", "co.in"}
LOCAL = {"reading", "wokingham", "bracknell", "earley", "caversham", "tilehurst",
         "woodley", "winnersh", "twyford", "sonning", "shinfield", "theale",
         "pangbourne", "burghfield", "calcot", "purley on thames", "emmer green",
         "lower earley", "arborfield", "spencers wood", "mortimer", "binfield"}


def domain(email):
    return email.split("@")[-1].lower().strip().strip(".")


def registrable(d):
    p = d.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in TWO_PART:
        return p[-3]
    return p[-2] if len(p) >= 2 else (p[0] if p else "")


def business_site(email):
    d = domain(email)
    if not d or "." not in d or registrable(d) in FREE:
        return None
    return "https://" + d


def months_since(day, today):
    return (today.year - day.year) * 12 + (today.month - day.month)


def lifecycle(came, coming, today):
    """came: events they turned up to, oldest first. coming: tickets held for
    nights that have not happened yet."""
    if not came:
        return "Booked, not been yet" if coming else "Never been"
    n = len(came)
    rec = months_since(dt.date.fromisoformat(came[-1]), today)
    if n >= 5:
        if rec <= 3:
            return "Regular"
        if coming:
            return "Booked again"
        if rec <= 6:
            return "Regular - but quiet"
        if rec <= 12:
            return "Slipping"
        return "Lost regular"
    if n == 1:
        if rec <= 2:
            return "Just arrived"
        if coming:
            return "Booked again"
        if rec <= 12:
            return "Came once, drifted"
        return "Cold - came once"
    if rec <= 2:
        return "In and out"
    if coming:
        return "Booked again"
    if rec <= 12:
        return "Gave up after a few"
    return "Cold - tried a few"


def is_sponsor(orders):
    for o in orders:
        blob = ((o.get("tc") or "") + " " + (o.get("code") or "") + " " +
                (o.get("promo") or "")).lower()
        if "power team" in blob or "powerteam" in blob or "sponsor" in blob:
            return True
        if o.get("sp"):
            return True
    return False


def history_body(orders, states, used, today):
    """Split every ticket into attended, unused, and still to come."""
    iso = today.isoformat()

    def line(o):
        tc = (o.get("tc") or "").strip()
        when = dt.date.fromisoformat(o["edate"]).strftime("%b %Y")
        what = tc if tc else ("RPM ticket" if o["qty"] == 1 else "RPM tickets")
        return f"{when}: {o['qty']} x {what}, GBP {o['gross']:.2f}"

    came, unused, coming = [], [], []
    for o in sorted(orders, key=lambda x: x["dt"], reverse=True):
        e = o["edate"]
        s = states.get(e)
        if e > iso:
            coming.append(line(o))
        elif s == m.ATTENDED:
            came.append(line(o))
        elif s == m.BOOKED:
            unused.append(line(o))
        else:
            came.append(line(o) + " (door not scanned)")

    attended = sorted(e for e, s in states.items() if s == m.ATTENDED and e <= iso)
    evs = sorted({o["edate"] for o in orders})
    total = sum(o["gross"] for o in orders)
    span = dt.date.fromisoformat(evs[0]).strftime("%b %Y")
    if evs[-1] != evs[0]:
        span += " to " + dt.date.fromisoformat(evs[-1]).strftime("%b %Y")

    head = (f"<b>{MARKER}</b><br>Attended {len(attended)} "
            + ("event" if len(attended) == 1 else "events")
            + f", {span}, GBP {total:.2f} spent in total")
    if unused:
        head += f". {len(unused)} ticket" + ("" if len(unused) == 1 else "s") + " went unused"
    if coming:
        nxt = min(o["edate"] for o in orders if o["edate"] > iso)
        head += f". Holds {len(coming)} ticket" + ("" if len(coming) == 1 else "s") + \
                " for a night still to come, next on " + dt.date.fromisoformat(nxt).strftime("%d %b %Y")
    if used > 1:
        head += f". Combined across {used} email addresses"

    out = head
    for title, group in (("Booked, not happened yet", coming),
                         ("Attended", came),
                         ("Booked but did not attend", unused)):
        if group:
            out += f"<br><b>{title}</b><ul>" + "".join("<li>" + x + "</li>" for x in group) + "</ul>"
    return out


def plain(html):
    return " ".join(re.sub("<[^>]+>", " ", html or "").split()).lower()


def addresses(cv, email_col, other_col):
    out = []
    p = cv.get(email_col, "").strip().lower()
    if p:
        out.append(p)
    if other_col:
        for alt in cv.get(other_col, "").split("|"):
            alt = alt.strip().lower()
            if alt and alt not in out:
                out.append(alt)
    return out


def items_with_updates(col_ids):
    ids = ",".join('"%s"' % c for c in col_ids)
    out, cursor, pages = [], None, 0
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:100, cursor:$c){ cursor '
             'items { id column_values(ids:[%s]) { id text } updates(limit:10) { id body } } '
             '} } }') % (m.BOARD, ids)
        page = m.api(q, {"c": cursor})["boards"][0]["items_page"]
        out.extend(page["items"])
        pages += 1
        cursor = page.get("cursor")
        if not cursor:
            break
        if pages > 60:
            raise RuntimeError("pagination did not terminate")
    print(f"  read {len(out)} rows over {pages} pages", flush=True)
    return out


def main():
    try:
        data = json.load(open("data.json"))
    except FileNotFoundError:
        m.note("**Enrichment skipped — data.json is missing.**")
        return 0
    try:
        cities = json.load(open("city_lookup.json"))
    except FileNotFoundError:
        cities = {}
        print("city_lookup.json missing - Town/City and Travel will be skipped")

    today = dt.date.today()
    iso = today.isoformat()
    att_index, _ = m.attendance_index(data["orders"], iso)
    people = collections.defaultdict(list)
    for o in data["orders"]:
        people[o["h"]].append(o)
    print(f"{len(people)} ticket buyers known · {len(cities)} towns on file\n")

    by_title = m.columns()
    col = m.resolve(by_title, WANT)
    email_col = by_title.get("Email")
    other_col = by_title.get("Other emails")
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")

    read = [email_col] + ([other_col] if other_col else []) + list(col.values())
    items = items_with_updates(read)

    filled = collections.Counter()
    skipped = collections.Counter()
    updates, histories = [], []
    multi = comeback = 0
    rank = {m.BOOKED: 1, m.ATTENDED: 2}

    for it in items:
        cv = {c["id"]: (c["text"] or "").strip() for c in it["column_values"]}
        addrs = addresses(cv, email_col, other_col)
        if not addrs:
            skipped["no email on the row"] += 1
            continue

        orders, states, used = [], {}, 0
        for a in addrs:
            h = m.ident_hash(a)
            got = people.get(h)
            if got:
                orders.extend(got)
                used += 1
            for e, s in (att_index.get(h) or {}).items():
                if rank[s] > rank.get(states.get(e), 0):
                    states[e] = s
        if used > 1:
            multi += 1

        want = {}
        if orders:
            o0 = sorted(orders, key=lambda x: x["dt"])[0]
            nm = f"{(o0.get('first') or '').strip()} {(o0.get('last') or '').strip()}".strip()
            if nm:
                want["name"] = nm
            want["source"] = {"label": "Bought a ticket"}
            came = sorted(e for e, s in states.items() if s == m.ATTENDED and e <= iso)
            coming = sorted(e for e in states if e > iso)
            lab = lifecycle(came, coming, today)
            if lab == "Booked again":
                comeback += 1
            want["life"] = {"label": lab}
            want["guests"] = str(sum(max(0, o["qty"] - 1) for o in orders))
            want["sponsor"] = "v" if is_sponsor(orders) else ""

            body = history_body(orders, states, used, today)
            existing = next((u for u in (it.get("updates") or [])
                             if plain(u["body"]).startswith(MARKER.lower())), None)
            if existing is None:
                histories.append(("new", it["id"], body))
            elif plain(existing["body"]) != plain(body):
                histories.append(("edit", existing["id"], body))
        else:
            skipped["on the board but never bought a ticket"] += 1

        town = next((cities[m.ident_hash(a)] for a in addrs if m.ident_hash(a) in cities), None)
        if town:
            want["city"] = town
            want["travel"] = {"label": "Local to Reading"
                              if town.strip().lower() in LOCAL else "Travels in"}
        else:
            skipped["no town given at checkout"] += 1

        site = next((business_site(a) for a in addrs if business_site(a)), None)
        if site:
            want["web"] = {"url": site, "text": site.replace("https://", "")}
        else:
            skipped["free mailbox - no company site"] += 1

        payload = {}
        for key, val in want.items():
            cid = col.get(key)
            if not cid:
                continue
            cur = cv.get(cid, "")
            if key in NEVER_OVERWRITE and cur:
                continue
            if key == "sponsor":
                if bool(cur) != bool(val):
                    payload[cid] = {"checked": "true" if val else "false"}
            elif isinstance(val, dict):
                target = val.get("label") or val.get("text") or ""
                if cur != target:
                    payload[cid] = val
            else:
                if cur != val:
                    payload[cid] = val
            if cid in payload:
                filled[WANT[key]] += 1
        if payload:
            updates.append((it["id"], payload))

    print("\nWILL FILL")
    for k, v in filled.most_common():
        print(f"  {k:<24} {v}")
    print("\nLEFT BLANK ON PURPOSE")
    for k, v in skipped.most_common():
        print(f"  {k:<36} {v}")
    new = sum(1 for h in histories if h[0] == "new")
    ed = len(histories) - new
    print(f"\n{len(updates)} rows to update, {multi} built from more than one address")
    print(f"{comeback} lapsed people hold a ticket for a night still to come")
    print(f"{new} ticket histories to write, {ed} to revise\n")

    m.write(updates)

    done = 0
    for kind, ident, body in histories:
        if kind == "new":
            m.api('mutation($b:String!){ create_update(item_id: %s, body: $b){ id } }' % ident,
                  {"b": body})
        else:
            m.api('mutation($b:String!){ edit_update(id: %s, body: $b){ id } }' % ident,
                  {"b": body})
        done += 1
        if done % 50 == 0:
            print(f"  ticket histories written {done}/{len(histories)}", flush=True)
        time.sleep(0.25)

    m.note(f"Enrichment: {len(updates)} rows updated ({multi} combined across several "
           f"addresses, {comeback} marked Booked again). Ticket history: {new} written, "
           f"{ed} revised.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "Enrichment"))
