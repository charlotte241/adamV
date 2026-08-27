#!/usr/bin/env python3
"""Fill the per-event grid on the RPM Outreach board.

Two columns per event:

    "Jan 2024"        Attended | Booked | Didn't book
    "Jan 2024 days"   days between placing the order and the event, 0 if absent

    Attended     they were scanned in on the night, or they held an online
                 seat. Online seats are never scanned because there is no door
                 to walk through, so the ticket is the only evidence there will
                 ever be for them.
    Booked       they hold a ticket and it has not become an attendance - they
                 did not turn up, or the night has not happened yet.
    Didn't book  no ticket.
    (empty)      the night was never scanned at all, so who came is genuinely
                 unknown and the cell says nothing rather than guessing. One
                 night in the history is like this: 31 March 2022.

A ticket sold is not a person in the room. Before check-in data was read, a
no-show and a regular looked identical on this board and roughly one ticket in
six is a no-show, so the attendance figures were about 290 too high.

Only cells that would actually change are written, so the first run is slow and
every run after it writes almost nothing.

Columns are matched by title, so renaming one in monday does not break this and
adding a new event only needs the two columns creating.
"""
import json, sys, collections
import datetime as dt
import mdlib as m


def main():
    try:
        data = json.load(open("data.json"))
    except FileNotFoundError:
        m.note("**Event grid skipped — data.json is missing.**")
        return 0
    orders = data["orders"]
    today = dt.date.today().isoformat()

    per, scanned = m.attendance_index(orders, today)
    events = sorted({o["edate"] for o in orders if o.get("edate")})
    label = {e: dt.date.fromisoformat(e).strftime("%b %Y") for e in events}
    unscanned = [e for e in events if e <= today and not scanned.get(e)]
    print(f"{len(events)} events in the data, {len(unscanned)} never scanned: {unscanned}")

    # days between booking and the event, best (earliest) booking per person
    days = collections.defaultdict(dict)
    for o in orders:
        e = o.get("edate")
        if not e:
            continue
        d = max(0, (dt.date.fromisoformat(e) - dt.date.fromisoformat(o["dt"][:10])).days)
        days[o["h"]][e] = max(days[o["h"]].get(e, 0), d)

    by_title = m.columns()
    email_col = by_title.get("Email")
    other_col = by_title.get("Other emails")
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")

    usable, missing = [], []
    for e in events:
        if label[e] in by_title and f"{label[e]} days" in by_title:
            usable.append(e)
        else:
            missing.append(label[e])
    if missing:
        m.note(f"**Event grid: no columns yet for {missing}** — those events were skipped. "
               f"Create a status column and a numbers column named '<Mon YYYY>' and "
               f"'<Mon YYYY> days' to include them.")
    print(f"{len(usable)} events have both columns")
    if not usable:
        raise RuntimeError("not one event has a matching pair of columns")

    grid = [by_title[label[e]] for e in usable] + [by_title[f"{label[e]} days"] for e in usable]
    read = [email_col] + ([other_col] if other_col else []) + grid
    items = m.all_items(read)

    updates, matched, unchanged, cells, multi = [], 0, 0, 0, 0
    tally = collections.Counter()
    for it in items:
        cv = m.values(it)
        addrs = []
        p = cv.get(email_col, "").strip().lower()
        if p:
            addrs.append(p)
        if other_col:
            for alt in cv.get(other_col, "").split("|"):
                alt = alt.strip().lower()
                if alt and alt not in addrs:
                    addrs.append(alt)
        if not addrs:
            continue

        mine, mydays, used = {}, {}, 0
        rank = {m.BOOKED: 1, m.ATTENDED: 2}
        for a in addrs:
            h = m.ident_hash(a)
            got = per.get(h)
            if not got:
                continue
            used += 1
            for e, state in got.items():
                if rank[state] > rank.get(mine.get(e), 0):
                    mine[e] = state
            for e, d in days.get(h, {}).items():
                mydays[e] = max(mydays.get(e, 0), d)
        if not mine and not mydays:
            continue
        matched += 1
        if used > 1:
            multi += 1

        payload = {}
        for e in usable:
            sc = by_title[label[e]]
            dc = by_title[f"{label[e]} days"]
            state = mine.get(e)
            if state is None and e in mydays and e in unscanned:
                want = ""                       # never scanned - say nothing
            elif state is None:
                want = m.NO_TICKET
            else:
                want = state
            tally[want or "(left empty)"] += 1
            cur = cv.get(sc, "")
            if cur != want:
                payload[sc] = {"label": want} if want else {}
            d = str(mydays.get(e, 0))
            if cv.get(dc, "").replace(",", "") != d:
                payload[dc] = d
        if payload:
            updates.append((it["id"], payload))
            cells += len(payload)
        else:
            unchanged += 1

    print(f"{matched} rows matched ({multi} across several addresses), {unchanged} already correct")
    print(f"{len(updates)} rows to write, {cells} cells")
    print("  " + ", ".join(f"{k}: {v:,}" for k, v in tally.most_common()))

    if not updates:
        m.note(f"Event grid: all {matched} ticket buyers already correct across {len(usable)} events.")
        return 0

    m.write(updates, per_request=3 if cells > 2000 else 8)
    m.note(f"Event grid: {len(updates)} people updated across {len(usable)} events "
           f"({cells} cells changed, {unchanged} already correct). "
           f"Attended {tally[m.ATTENDED]:,} · Booked not attended {tally[m.BOOKED]:,}.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "Event grid"))
