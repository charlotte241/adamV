#!/usr/bin/env python3
"""Fill in what we actually know about each person on the RPM Outreach board.

Every column below is a hard fact taken from the person's own orders or from
what they typed at checkout. Nothing here is a guess, a band or a "usually".

    Full Name            the name they gave Eventbrite when they paid
    Town/City            the town they gave at checkout
    Travel               Local to Reading / Travels in, from that town
    Website              their own email domain, where it is not a free mailbox
    Source               "Bought a ticket", only where an order matches
    Lifecycle            from how many events they attended and how long ago
    Guests brought       places paid for beyond their own seat
    Power Team sponsor   ticked where a ticket was a Power Team or sponsor ticket

Lifecycle counts a ticket for an event that has not happened yet. Someone who
drifted away and has just booked again is "Booked again", not "Lost regular" -
they are in the room next time, and describing them as gone is the opposite of
the truth. Regulars who are still turning up keep their normal label.

Their ticket history is written as an update on the item rather than a column,
because a monday text cell stops at 2,000 characters and silently cut the
oldest tickets off the longest-standing members. The update separates events
they have attended from tickets held for events still to come, so "2 tickets"
is never mistaken for "came twice".

A person is matched on every address on their row - "Email" and anything in
"Other emails" - so someone who booked under a work address one year and a
personal one the next gets one continuous history.

Deliberately NOT filled, because nothing available is reliable enough:

    Company Name   a squashed domain gives "Morsewebb" and "Ramsayandwhite".
    Category       only a handful of domains carry an unambiguous trade word.
    Phone          the Eventbrite export carries none.

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


def lifecycle(past, future, today):
    """Matches the descriptions written on the labels in monday.

    A ticket for an event that has not happened yet outranks a long silence:
    someone who lapsed and rebooked is coming back, not gone."""
    if not past:
        return "Booked, not been yet" if future else "Never been"
    n = len(past)
    rec = months_since(dt.date.fromisoformat(past[-1]), today)
    if n >= 5:
        if rec <= 3:
            return "Regular"
        if future:
            return "Booked again"
        if rec <= 6:
            return "Regular - but quiet"
        if rec <= 12:
            return "Slipping"
        return "Lost regular"
    if n == 1:
        if rec <= 2:
            return "Just arrived"
        if future:
            return "Booked again"
        if rec <= 12:
            return "Came once, drifted"
        return "Cold - came once"
    if rec <= 2:
        return "In and out"
    if future:
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


def history_body(orders, used, today):
    """Every ticket they have paid for, newest first, with tickets for events
    still to come kept separate from events they actually attended."""
    def line(o):
        tc = (o.get("tc") or "").strip()
        when = dt.date.fromisoformat(o["edate"]).strftime("%b %Y")
        what = tc if tc else ("RPM ticket" if o["qty"] == 1 else "RPM tickets")
        return f"{when}: {o['qty']} x {what}, GBP {o['gross']:.2f}"

    iso = today.isoformat()
    done, coming = [], []
    for o in sorted(orders, key=lambda x: x["dt"], reverse=True):
        ln = line(o)
        (coming if o["edate"] > iso else done).append(ln)

    evs = sorted({o["edate"] for o in orders})
    past_ev = sorted({o["edate"] for o in orders if o["edate"] <= iso})
    total = sum(o["gross"] for o in orders)
    span = dt.date.fromisoformat(evs[0]).strftime("%b %Y")
    if evs[-1] != evs[0]:
        span += " to " + dt.date.fromisoformat(evs[-1]).strftime("%b %Y")

    head = f"<b>{MARKER}</b><br>Attended {len(past_ev)} " + \
           ("event" if len(past_ev) == 1 else "events") + f", {span}, GBP {total:.2f} in total"
    if coming:
        nxt = dt.date.fromisoformat(min(o["edate"] for o in orders if o["edate"] > iso))
        head += f". Also holds {len(coming)} ticket" + ("" if len(coming) == 1 else "s") + \
                f" for an event still to come, next on {nxt.strftime('%d %b %Y')}"
    if used > 1:
        head += f". Combined across {used} email addresses"
    out = head
    if coming:
        out += "<br><b>Booked, not happened yet</b><ul>" + \
               "".join("<li>" + x + "</li>" for x in coming) + "</ul>"
    out += "<br><b>Attended</b><ul>" + "".join("<li>" + x + "</li>" for x in done) + "</ul>" \
           if done else ""
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

    today = dt.date.today()
    filled = collections.Counter()
    skipped = collections.Counter()
    updates, histories = [], []
    multi = comeback = 0

    for it in items:
        cv = {c["id"]: (c["text"] or "").strip() for c in it["column_values"]}
        addrs = addresses(cv, email_col, other_col)
        if not addrs:
            skipped["no email on the row"] += 1
            continue

        orders, used = [], 0
        for a in addrs:
            got = people.get(m.ident_hash(a))
            if got:
                orders.extend(got)
                used += 1
        if used > 1:
            multi += 1

        want = {}
        if orders:
            o0 = sorted(orders, key=lambda x: x["dt"])[0]
            nm = f"{(o0.get('first') or '').strip()} {(o0.get('last') or '').strip()}".strip()
            if nm:
                want["name"] = nm
            want["source"] = {"label": "Bought a ticket"}
            evs = sorted({o["edate"] for o in orders})
            past = [e for e in evs if e <= today.isoformat()]
            future = [e for e in evs if e > today.isoformat()]
            lab = lifecycle(past, future, today)
            if lab == "Booked again":
                comeback += 1
            want["life"] = {"label": lab}
            want["guests"] = str(sum(max(0, o["qty"] - 1) for o in orders))
            want["sponsor"] = "v" if is_sponsor(orders) else ""

            body = history_body(orders, used, today)
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
    print(f"{comeback} people are lapsed but hold a ticket for an event still to come")
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
