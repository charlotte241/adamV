#!/usr/bin/env python3
"""Push Eventbrite attendance onto the RPM Outreach CRM board.

Five columns, all of them facts read straight off the orders and the door:

    First visit      the first event they actually turned up to
    Times attended   events they turned up to, not tickets they bought
    Last attended    the most recent one they turned up to
    Total spent      gross across every order, whether they came or not
    Brings guests    ticked if any order was for more than one place

Times attended counts people in the room. A ticket is not an attendance: about
one in six goes unused, and counting those made every regular look identical to
someone who books and never comes. Money is different - a no-show still paid,
so Total spent counts every order.

A row is matched to a person by hashing every address on the row - "Email" and
anything in "Other emails" - the same way fetch_data.py hashed the order email.
People routinely book under a work address one year and a personal one the next.

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
    money = {}
    guests = {}
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
    total_attended = total_booked = 0

    for it in items:
        cv = m.values(it)
        addrs = addresses(cv, email_col, other_col)
        if not addrs:
            no_email += 1
            continue

        states, spent, guest, used = {}, 0.0, False, 0
        rank = {m.BOOKED: 1, m.ATTENDED: 2}
        for a in addrs:
            h = m.ident_hash(a)
            got = per.get(h)
            if h in money:
                spent += money[h]
                guest = guest or bool(guests.get(h))
            if not got:
                continue
            used += 1
            for e, s in got.items():
                if rank[s] > rank.get(states.get(e), 0):
                    states[e] = s
        if not states:
            not_a_buyer += 1
            continue
        matched += 1
        if used > 1:
            multi += 1

        came = sorted(e for e, s in states.items() if s == m.ATTENDED)
        past_came = [e for e in came if e <= today]
        total_attended += len(past_came)
        total_booked += sum(1 for e, s in states.items() if s == m.BOOKED and e <= today)

        want = {
            "first": past_came[0] if past_came else "",
            "times": str(len(past_came)),
            "last":  past_came[-1] if past_came else "",
            "spent": str(round(spent, 2)),
            "guest": "v" if guest else "",
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

    print(f"matched {matched} · of those {multi} across more than one address "
          f"· on the board but never bought a ticket {not_a_buyer} · no email on the row {no_email}")
    print(f"{total_attended} attendances, {total_booked} tickets that went unused")
    print(f"{len(updates)} rows need changing")

    m.write(updates)
    m.note(f"CRM sync: {matched} people matched ({multi} across several addresses). "
           f"{total_attended:,} attendances, {total_booked:,} unused tickets. "
           f"{len(updates)} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "CRM sync"))
