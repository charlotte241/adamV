#!/usr/bin/env python3
"""Fill the per-event grid on the RPM Outreach board.

Two columns per event:

    "Jan 2024"        Face to face | Zoom | Didn't attend
    "Jan 2024 days"   days between placing the order and the event, 0 if absent

Every cell is a fact taken straight from the Eventbrite order. Nothing is
averaged, banded or guessed.

Only cells that would actually change are written. The first run on a fresh
board writes everything and takes a while; every run after that writes almost
nothing, because a person's history only changes when they buy another ticket.
Without that check this step rewrote 108 cells for 600 people every time and
ran for the better part of an hour.

Columns are matched by title, so renaming one in monday does not break this and
adding a new event only needs the two columns creating. An event with no
columns yet is skipped and named in the log rather than aborting the run.
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

    events = sorted({o["edate"] for o in orders})
    label = {e: dt.date.fromisoformat(e).strftime("%b %Y") for e in events}
    print(f"{len(events)} events in the data")

    per = collections.defaultdict(dict)
    for o in orders:
        ev = dt.date.fromisoformat(o["edate"])
        bought = dt.date.fromisoformat(o["dt"][:10])
        days = max(0, (ev - bought).days)
        mode = "Zoom" if o.get("zoom") and o["zoom"] >= o["qty"] else "Face to face"
        prev = per[o["h"]].get(o["edate"])
        if prev is None or days > prev[1] or (prev[0] == "Zoom" and mode == "Face to face"):
            per[o["h"]][o["edate"]] = (mode, max(days, prev[1] if prev else 0))

    by_title = m.columns()
    email_col = by_title.get("Email")
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")

    usable, missing = [], []
    for e in events:
        if label[e] in by_title and f"{label[e]} days" in by_title:
            usable.append(e)
        else:
            missing.append(label[e])
    if missing:
        m.note(f"Event grid: no columns yet for {missing} — those events were skipped.")
    print(f"{len(usable)} events have both columns and will be written")
    if not usable:
        raise RuntimeError("not one event has a matching pair of columns")

    grid_cols = [by_title[label[e]] for e in usable] + [by_title[f"{label[e]} days"] for e in usable]
    items = m.all_items([email_col] + grid_cols)

    updates, matched, unchanged, cells = [], 0, 0, 0
    for it in items:
        cv = m.values(it)
        email = cv.get(email_col, "")
        if not email:
            continue
        went = per.get(m.ident_hash(email))
        if not went:
            continue
        matched += 1
        payload = {}
        for e in usable:
            mode, days = went.get(e, ("Didn't attend", 0))
            sc = by_title[label[e]]
            dc = by_title[f"{label[e]} days"]
            if cv.get(sc, "") != mode:
                payload[sc] = {"label": mode}
            if cv.get(dc, "").replace(",", "") != str(days):
                payload[dc] = str(days)
        if payload:
            updates.append((it["id"], payload))
            cells += len(payload)
        else:
            unchanged += 1

    print(f"{matched} rows matched to a ticket buyer, {unchanged} already correct")
    print(f"{len(updates)} rows need writing, {cells} cells in total")

    if not updates:
        m.note(f"Event grid: all {matched} ticket buyers already correct across {len(usable)} events.")
        return 0

    per_request = 3 if cells > 2000 else 8
    m.write(updates, per_request=per_request)
    m.note(f"Event grid: {len(updates)} people updated across {len(usable)} events "
           f"({cells} cells changed, {unchanged} already correct).")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "Event grid"))
