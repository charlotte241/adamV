#!/usr/bin/env python3
"""Merge duplicate people on the RPM Outreach CRM board.

Two rows are the same person if they share a name or share an email. Those
links are transitive, so Frank Mason's five rows collapse into one even though
no two of them share an address.

Which row survives
------------------
The one holding the email they used for their most recent booking, read from
the Eventbrite order data in data.json. Someone who bought under a work address
last month and a personal one in 2022 keeps the work address, because that is
where the next confirmation lands. If nobody in the cluster has ever bought a
ticket, the fullest row wins.

Nothing is lost
---------------
Every other email, and every value that differed between the merged rows, is
written into an update on the surviving item before the losing rows are
archived. Archived, not deleted - they stay recoverable in monday for 30 days
and the update is a permanent record either way.

Date, number and status columns are deliberately left alone. They cannot be
written on this board at the moment, so touching them would silently drop data.
They are rebuilt from Eventbrite by sync_crm.py anyway.

MERGE_WRITE is unset or 0  ->  report only, board untouched.
MERGE_WRITE=1              ->  apply.
MERGE_ONLY=id,id           ->  restrict to these cluster leader item ids.
MERGE_SKIP=id,id           ->  leave these clusters alone.
"""
import json, os, sys, re, time, unicodedata, collections
import mdlib as m

WRITE = os.environ.get("MERGE_WRITE", "0") == "1"
ONLY = {s.strip() for s in os.environ.get("MERGE_ONLY", "").split(",") if s.strip()}
SKIP = {s.strip() for s in os.environ.get("MERGE_SKIP", "").split(",") if s.strip()}

MERGEABLE = {"text", "long_text", "checkbox", "link", "phone", "email"}
NEVER = {"Duplicate check", "Duplicate of"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z ]", " ", s.lower()).split())


def is_grid(t):
    return bool(re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}( days)?$", t))


def latest_booking(data):
    """email hash -> most recent event date they hold a ticket for."""
    out = {}
    for o in data.get("orders", []):
        h, d = o.get("h"), o.get("edate")
        if h and d and d > out.get(h, ""):
            out[h] = d
    return out


def main():
    try:
        book = latest_booking(json.load(open("data.json")))
    except FileNotFoundError:
        m.note("**Merge skipped - data.json is missing, so the most-recent-booking rule cannot be applied.**")
        return 0
    print(f"{len(book)} email addresses have Eventbrite bookings")

    cols = m.api("query{ boards(ids:[%s]){ columns { id title type } } }" % m.BOARD)["boards"][0]["columns"]
    title = {c["id"]: c["title"] for c in cols}
    ctype = {c["id"]: c["type"] for c in cols}
    keep_cols = [c["id"] for c in cols
                 if ctype[c["id"]] in MERGEABLE
                 and not is_grid(c["title"]) and c["title"] not in NEVER]
    email_col = next((c["id"] for c in cols if c["title"] == "Email"), None)
    if not email_col:
        raise RuntimeError("no column titled 'Email' on the board")

    items, cursor, pages = [], None, 0
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:200, cursor:$c){ cursor '
             'items { id name created_at group{title} column_values { id text } } } } }') % m.BOARD
        page = m.api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        pages += 1
        cursor = page.get("cursor")
        if not cursor:
            break
        if pages > 40:
            raise RuntimeError("pagination did not terminate")
    print(f"read {len(items)} rows")

    rows = []
    for it in items:
        cv = {c["id"]: (c["text"] or "").strip() for c in it["column_values"]}
        rows.append({
            "id": it["id"], "name": it["name"].strip(), "group": it["group"]["title"],
            "created": (it.get("created_at") or "")[:10],
            "email": cv.get(email_col, "").strip().lower(),
            "nk": norm(it["name"]), "cv": cv,
            "filled": sum(1 for k in keep_cols if cv.get(k)),
        })

    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for key in ("nk", "email"):
        bucket = collections.defaultdict(list)
        for r in rows:
            if r[key]:
                bucket[r[key]].append(r["id"])
        for ids in bucket.values():
            for other in ids[1:]:
                union(ids[0], other)

    clusters = collections.defaultdict(list)
    for r in rows:
        clusters[find(r["id"])].append(r)
    dupes = {k: v for k, v in clusters.items() if len(v) > 1}
    print(f"{len(rows)} rows -> {len(clusters)} people, {len(dupes)} clusters to merge")

    planned, archive, updates, notes, skipped = [], [], [], [], 0

    for leader, group in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
        ids = {r["id"] for r in group}
        if SKIP & ids:
            skipped += 1
            continue
        if ONLY and not (ONLY & ids):
            continue

        def rank(r):
            return (book.get(m.ident_hash(r["email"]), "") if r["email"] else "",
                    r["filled"], r["created"] or "9999")
        ordered = sorted(group, key=rank, reverse=True)
        keep = ordered[0]
        losers = ordered[1:]
        kh = m.ident_hash(keep["email"]) if keep["email"] else ""
        why = ("most recent booking " + book[kh]) if kh in book else "no bookings in this cluster - fullest row kept"

        payload, audit = {}, []
        for cid in keep_cols:
            if cid == email_col:
                continue
            vals = [r["cv"].get(cid, "") for r in ordered]
            present = [v for v in vals if v]
            if not present:
                continue
            if ctype[cid] == "checkbox":
                if any(v == "v" for v in vals) and keep["cv"].get(cid) != "v":
                    payload[cid] = {"checked": "true"}
                continue
            distinct = list(dict.fromkeys(present))
            if len(distinct) == 1:
                if not keep["cv"].get(cid):
                    payload[cid] = distinct[0]
            else:
                if ctype[cid] in ("text", "long_text"):
                    payload[cid] = "  |  ".join(distinct)[:1990]
                audit.append((title[cid], distinct))

        body = ["<b>Merged " + str(len(group)) + " duplicate rows into this one.</b>",
                "Kept because: " + why, "", "<b>Email addresses on the merged rows</b><ul>"]
        for r in ordered:
            if not r["email"]:
                continue
            when = book.get(m.ident_hash(r["email"]), "")
            mark = " &larr; kept as the Email on this record" if r is keep else ""
            body.append("<li>" + r["email"] + " - " +
                        ("last booked " + when if when else "no Eventbrite bookings") + mark + "</li>")
        body.append("</ul>")
        if audit:
            body.append("<b>Values that differed between the rows</b><ul>")
            for t, distinct in audit:
                body.append("<li><b>" + t + "</b>: " + " &nbsp;|&nbsp; ".join(distinct) + "</li>")
            body.append("</ul>")
        body.append("<b>Rows archived into this one</b><ul>")
        for r in losers:
            body.append("<li>" + r["name"] + " (item " + r["id"] + ", added " + r["created"] +
                        ", " + (r["email"] or "no email") + ")</li>")
        body.append("</ul>")
        body.append("<i>Archived rows are recoverable in monday. Attendance, spend and lifecycle "
                    "are rebuilt from Eventbrite on the next run.</i>")

        planned.append((keep, ordered, why, audit))
        if payload:
            updates.append((keep["id"], payload))
        notes.append((keep["id"], "\n".join(body)))
        archive.extend(r["id"] for r in losers)

    print("=" * 74)
    for keep, ordered, why, audit in planned:
        print("")
        print(keep["name"] + "  (" + str(len(ordered)) + " rows)  keep " + keep["id"] +
              " <" + (keep["email"] or "-") + ">")
        print("   reason: " + why)
        for r in ordered[1:]:
            print("   archive " + r["id"] + " <" + (r["email"] or "-") + ">")
        for t, distinct in audit:
            print("   keeps both: " + t + " = " + " | ".join(x[:40] for x in distinct))
    print("=" * 74)
    print("clusters merged      " + str(len(planned)))
    print("rows archived        " + str(len(archive)))
    print("rows updated         " + str(len(updates)))
    if skipped:
        print("clusters skipped     " + str(skipped))

    if not WRITE:
        m.note("Merge dry run: " + str(len(planned)) + " clusters would collapse " +
               str(len(archive)) + " rows. Nothing was written. Set MERGE_WRITE=1 to apply.")
        return 0

    m.write(updates)

    posted = 0
    for iid, body in notes:
        m.api("mutation($b:String!){ create_update(item_id: " + iid + ", body: $b) { id } }", {"b": body})
        posted += 1
        if posted % 20 == 0:
            print("  posted " + str(posted) + " updates", flush=True)
        time.sleep(0.3)

    done = 0
    for i in range(0, len(archive), 10):
        chunk = archive[i:i + 10]
        parts = " ".join("a" + str(n) + ": archive_item(item_id: " + iid + ") { id }"
                         for n, iid in enumerate(chunk))
        m.api("mutation{ " + parts + " }")
        done += len(chunk)
        print("  archived " + str(done) + "/" + str(len(archive)), flush=True)
        time.sleep(0.35)

    m.note("Merge: " + str(len(planned)) + " people consolidated, " + str(len(archive)) +
           " duplicate rows archived, " + str(posted) + " audit updates posted.")
    return 0


if __name__ == "__main__":
    sys.exit(m.guard(main, "Merge duplicates"))
