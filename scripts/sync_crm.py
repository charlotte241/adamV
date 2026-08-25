#!/usr/bin/env python3
"""Push Eventbrite attendance onto the RPM Outreach CRM board.

Five columns, all of them facts read straight off the orders. Nobody edits
these by hand, so they are always overwritten to match Eventbrite:

    First visit      date of their earliest ticket
    Times attended   events that have already happened
    Last attended    the most recent one that has happened
    Total spent      gross across every order
    Brings guests    ticked if any order was for more than one place

A row is matched to a person by hashing every address on the row - "Email" and
anything in "Other emails" - the same way fetch_data.py hashed the order email.
People routinely book under a work address one year and a personal one the
next, so counting only the primary address would quietly discard half their
history the moment two records were merged. No name matching, no fuzzy guessing.

Everything a human types - Best fit, Owner, Next action, Relationship stage,
notes, replies - is never touched.
"""
import json, sys
import datetime as dt
import mdlib as m

WANT = {
    "first": "First visit",
    "times": "Times attended",
    "last":  "Last attended",
    "spent": "Total spent",
    "guest": "Brings guests",
}


def addresses(cv, email_col, other_col):
    """Every address this row represents, primary first."""
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

    people = {}
    for o in data["orders"]:
        people.setdefault(o["h"], []).append(o)
    print(f"{len(people)} distinct ticket buyers in the Eventbrite data")

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

    today = dt.date.today()
    updates, matched, no_email, not_a_buyer, multi = [], 0, 0, 0, 0

    for it in items:
        cv = m.values(it)
        addrs = addresses(cv, email_col, other_col)
        if not addrs:
            no_email += 1
            continue

        orders = []
        used = 0
        for a in addrs:
            got = people.get(m.ident_hash(a))
            if got:
                orders.extend(got)
                used += 1
        if not orders:
            not_a_buyer += 1
            continue
        matched += 1
        if used > 1:
            multi += 1

        evs = sorted({o["edate"] for o in orders})
        past = [e for e in evs if e <= today.isoformat()]
        want = {
            "first": evs[0],
            "times": str(len(past)),
            "last":  past[-1] if past else "",
            "spent": str(round(sum(o["gross"] for o in orders), 2)),
            "guest": "v" if any(o["qty"] > 1 for o in orders) else "",
        }

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

    print(f"matched {matched} · of those, {multi} counted across more than one address "
          f"· on the board but never bought a ticket {not_a_buyer} · no email on the row {no_email}")
    print(f"{len(updates)} rows need changing")

    m.write(updates)
    m.note(f"CRM sync: {matched} people matched to Eventbrite ({multi} across several "
           f"addresses), {len(updates)} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "CRM sync"))
