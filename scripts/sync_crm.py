#!/usr/bin/env python3
"""Push Eventbrite attendance onto the RPM Outreach CRM board.

The four counts, which reconcile against each other:

    Total events booked      every ticket they have ever bought, in person or
                             online, whether or not they used it
    Total events attended    the ones they actually took part in, in the room
                             or on Zoom
    Total events in person   scanned through the door
    Total events on zoom     online seats

    booked - attended  =  tickets that went unused

Plus:

    First visit      the first event they turned up to in person
    Last attended    the most recent one they turned up to in person
    Total spent      gross across every order, used or not
    Brings guests    ticked if any order was for more than one place

All four counts cover events up to and including today. A ticket for a night
that has not happened yet is not counted anywhere - nobody has booked-and-not-
shown for an event that is still to come.

An online seat counts as attended. Nobody is scanned through a door they never
walk through, so a Zoom ticket is the only evidence there will ever be for
them, and treating it as a no-show would be wrong.

A row is matched to a person by hashing every address on the row - "Email" and
anything in "Other emails" - because people book under a work address one year
and a personal one the next.

Everything a human types - Best fit, Owner, Next action, Relationship stage,
notes, replies - is never touched.
"""
import json, sys
import datetime as dt
import mdlib as m

WANT = {
    "booked":   "Total events booked",
    "attended": "Total events attended",
    "person":   "Total events in person",
    "zoom":     "Total events on zoom",
    "first":    "First visit",
    "last":     "Last attended",
    "spent":    "Total spent",
    "guest":    "Brings guests",
}
COUNTS = ("booked", "attended", "person", "zoom")


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


def main():
    try:
        data = json.load(open("data.json"))
    except FileNotFoundError:
        m.note("**CRM sync skipped — data.json is missing.**")
        return 0

    today = dt.date.today().isoformat()
    per, scanned = m.attendance_index(data["orders"], today)
    money, guests = {}, {}
    for o in data["orders"]:
        h = o.get("h")
        if not h:
            continue
        money[h] = round(money.get(h, 0.0) + float(o.get("gross") or 0), 2)
        if int(o.get("qty") or 1) > 1:
            guests[h] = True
    print(f"{len(per)} distinct ticket buyers in the Eventbrite data")

    by_title = m.columns()
    col = m.resolve(by_title, WANT)
    email_col = by_title.get("Email")
    other_col = by_title.get("Other emails")
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")
    if not col:
        raise RuntimeError("none of the attendance columns exist any more")

    read = [email_col] + ([other_col] if other_col else []) + list(col.values())
    items = m.all_items(read)

    updates, matched, no_email, not_a_buyer, multi = [], 0, 0, 0, 0
    totals = {k: 0 for k in COUNTS}

    for it in items:
        cv = m.values(it)
        addrs = addresses(cv, email_col, other_col)
        if not addrs:
            no_email += 1
            continue

        states, spent, guest, used = {}, 0.0, False, 0
        for a in addrs:
            h = m.ident_hash(a)
            if h in money:
                spent += money[h]
                guest = guest or bool(guests.get(h))
            got = per.get(h)
            if not got:
                continue
            used += 1
            for e, s in got.items():
                if m.RANK[s] > m.RANK.get(states.get(e), 0):
                    states[e] = s
        if not states:
            not_a_buyer += 1
            continue
        matched += 1
        if used > 1:
            multi += 1

        done = {e: s for e, s in states.items() if e <= today}
        in_person = sorted(e for e, s in done.items() if s == m.ATTENDED)
        on_zoom = [e for e, s in done.items() if s == m.BOOKED_ZOOM]
        n = {
            "booked":   len(done),
            "attended": len(in_person) + len(on_zoom),
            "person":   len(in_person),
            "zoom":     len(on_zoom),
        }
        for k in COUNTS:
            totals[k] += n[k]

        want = {k: str(v) for k, v in n.items()}
        want["first"] = in_person[0] if in_person else ""
        want["last"] = in_person[-1] if in_person else ""
        want["spent"] = str(round(spent, 2))
        want["guest"] = "v" if guest else ""

        payload = {}
        for key, val in want.items():
            cid = col.get(key)
            if not cid:
                continue
            cur = cv.get(cid, "")
            if key == "guest":
                if bool(cur) != bool(val):
                    payload[cid] = {"checked": "true" if val else "false"}
            elif key in ("first", "last"):
                if cur[:10] != val:
                    payload[cid] = {"date": val} if val else {}
            else:
                if cur.replace(",", "") != val:
                    payload[cid] = val
        if payload:
            updates.append((it["id"], payload))

    unused = totals["booked"] - totals["attended"]
    print(f"matched {matched} · of those {multi} across more than one address "
          f"· never bought a ticket {not_a_buyer} · no email on the row {no_email}")
    print(f"booked {totals['booked']} · attended {totals['attended']} "
          f"(in person {totals['person']}, zoom {totals['zoom']}) · unused {unused}")
    print(f"{len(updates)} rows need changing")

    m.write(updates)
    m.note(f"CRM sync: {matched} people matched ({multi} across several addresses). "
           f"Booked {totals['booked']:,} · attended {totals['attended']:,} "
           f"(in person {totals['person']:,}, zoom {totals['zoom']:,}) · "
           f"{unused:,} tickets unused. {len(updates)} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "CRM sync"))
