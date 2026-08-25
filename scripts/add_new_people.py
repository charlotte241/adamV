#!/usr/bin/env python3
"""Put every Eventbrite ticket buyer on the CRM board.

The board was originally built from a hand-made list, so it only ever held some
of the people who have actually bought a ticket. This closes that gap and keeps
it closed: anyone who buys a ticket appears on the board on the next refresh.

Two things stop this from undoing the merge. A person is considered already
present if their address appears in "Email" OR in "Other emails", which is where
merge_people.py records the addresses a person also books under. And the board
id, group and columns are all resolved by title through mdlib, so a rebuild or a
rename does not silently send new rows to the wrong place.

Real email addresses are read from the Eventbrite API at run time and written
straight to monday. They are never written to data.json or anywhere in this
repository, which is public.

Needs EVENTBRITE_TOKEN and MONDAY_TOKEN.
"""
import json, os, sys, time, calendar
import datetime as dt
import urllib.request, urllib.parse, urllib.error
import mdlib as m

EB = os.environ.get("EVENTBRITE_TOKEN", "").strip()
SINCE = dt.date(2022, 1, 1)
PEOPLE_GROUP_PREFIX = "People"


def eb(path, **params):
    url = f"https://www.eventbriteapi.com/v3{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {EB}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            wait = min(2 ** attempt * 5, 60)
            print(f"  eventbrite {path} failed ({e}) - retry in {wait}s", flush=True)
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


def last_thursday(d):
    last = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return last - dt.timedelta(days=(last.weekday() - 3) % 7)


def main():
    if not EB:
        m.note("**New-people sync skipped - EVENTBRITE_TOKEN is not set.**")
        return 0

    by_title = m.columns()
    email_col = by_title.get("Email")
    name_col = by_title.get("Full Name")
    other_col = by_title.get("Other emails")
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")

    groups = m.api("query{ boards(ids:[%s]){ groups { id title } } }" % m.BOARD)["boards"][0]["groups"]
    grp = next((g["id"] for g in groups if g["title"].startswith(PEOPLE_GROUP_PREFIX)), None)
    if not grp:
        raise RuntimeError("no group whose title starts with 'People'")

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

    cols = [c for c in (email_col, other_col) if c]
    on_board = set()
    for it in m.all_items(cols):
        cv = m.values(it)
        primary = cv.get(email_col, "").strip().lower()
        if primary:
            on_board.add(primary)
        if other_col:
            for alt in cv.get(other_col, "").split("|"):
                alt = alt.strip().lower()
                if alt:
                    on_board.add(alt)
    print(f"{len(on_board)} addresses already accounted for on the board")

    missing = {em: nm for em, nm in people.items() if em not in on_board}
    print(f"{len(missing)} buyers to add")
    if not missing:
        m.note("New people: none to add, every ticket buyer is already on the board.")
        return 0

    if len(missing) > 150:
        m.note(f"**New people: {len(missing)} rows would be added, which is more than expected. "
               f"Refusing to run in case the board or the Other emails column is misconfigured.**")
        return 0

    todo = sorted(missing.items(), key=lambda kv: kv[1].lower())
    done = 0
    for i in range(0, len(todo), 10):
        chunk = todo[i:i+10]
        parts, variables = [], {}
        for n, (em, nm) in enumerate(chunk):
            payload = {email_col: {"email": em, "text": em}}
            if name_col:
                payload[name_col] = nm[:250]
            variables[f"n{n}"] = nm[:250]
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'c{n}: create_item(board_id: {m.BOARD}, group_id: "{grp}", '
                         f'item_name: $n{n}, column_values: $v{n}) {{ id }}')
        sig = ", ".join(f"$n{n}: String!, $v{n}: JSON!" for n in range(len(chunk)))
        m.api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        print(f"  added {done}/{len(todo)}", flush=True)
        time.sleep(0.5)
    m.note(f"New people: {done} ticket buyers added to the board.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "New people"))
