#!/usr/bin/env python3
"""Shared monday helpers for the RPM CRM scripts.

The one rule here: never name a column by its id. Columns get deleted and
renamed as the board is used, and an id that no longer exists makes monday
reject the whole request - which is how the board quietly stopped updating.
Everything is looked up by title, and a missing title is a printed warning,
not a dead pipeline.

attendance_index below is the other single source of truth: what a ticket
actually means. Every script that reports attendance uses it, so the event
grid, the attendance counts and the lifecycle can never disagree.
"""
import json, os, sys, time, hashlib, collections
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
BOARD = "18428135685"

# The four things a cell in the event grid can say.
ATTENDED    = "Attended Face to face"   # scanned in on the night
BOOKED_F2F  = "Booked Face to face"     # in-person ticket, no scan against it
BOOKED_ZOOM = "Booked Zoom"             # online seat - never scanned, so never confirmed
NO_TICKET   = "Didn't book"

# When someone holds more than one ticket for the same night, the strongest
# thing we can say about them wins.
RANK = {BOOKED_ZOOM: 1, BOOKED_F2F: 2, ATTENDED: 3}


def attendance_index(orders, today):
    """(per_person, scanned) from the raw orders in data.json.

    per_person: {email hash: {event date: one of the four states above}}
                a night the person holds a ticket for but which was never
                scanned is deliberately absent, so the cell is left empty
                rather than guessing either way.
    scanned:    {event date: True/False} - did anyone work the door that night.

    Only "Attended Face to face" is an attendance. An online seat stays
    "Booked Zoom" however the night went, because nobody is scanned through a
    door they never walk through - a ticket is the only evidence there will
    ever be, and a ticket is not proof someone turned up.
    """
    scanned, in_person = {}, collections.Counter()
    for o in orders:
        e = o.get("edate")
        if not e:
            continue
        scanned[e] = scanned.get(e, False) or bool(o.get("ci"))
        in_person[e] += max(0, int(o.get("qty") or 1) - int(o.get("zoom") or 0))

    per = collections.defaultdict(dict)
    for o in orders:
        e, h = o.get("edate"), o.get("h")
        if not e or not h:
            continue
        qty = int(o.get("qty") or 1)
        zoom = int(o.get("zoom") or 0)
        if o.get("ci"):
            state = ATTENDED
        elif zoom >= qty and zoom > 0:
            state = BOOKED_ZOOM
        elif e > today or scanned.get(e) or in_person[e] == 0:
            state = BOOKED_F2F
        else:
            continue                      # past night, nobody scanned - unknown
        if RANK[state] > RANK.get(per[h].get(e), 0):
            per[h][e] = state
    return per, scanned


def api(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    last = None
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60))
            if "errors" in r:
                raise RuntimeError(json.dumps(r["errors"])[:400])
            return r["data"]
        except Exception as e:
            last = e
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday call failed ({e}) - retry {attempt+1}/4 in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"monday unreachable: {last}")


def columns():
    """title -> id, for every column on the board."""
    cols = api("query{ boards(ids:[%s]){ columns { id title } } }" % BOARD)
    return {c["title"]: c["id"] for c in cols["boards"][0]["columns"]}


def resolve(by_title, wanted):
    """wanted: {key: column title}. Returns {key: id} for the ones that exist,
    and prints the ones that do not so a deleted column is obvious in the log."""
    out, gone = {}, []
    for key, title in wanted.items():
        cid = by_title.get(title)
        if cid:
            out[key] = cid
        else:
            gone.append(title)
    if gone:
        print(f"  NOTE: these columns are no longer on the board, skipping them: {gone}",
              flush=True)
    return out


def all_items(col_ids):
    """Every row on the board with the given column ids. Pages until exhausted."""
    ids = ",".join('"%s"' % c for c in col_ids)
    items, cursor, pages = [], None, 0
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:200, cursor:$c){ '
             'cursor items { id name column_values(ids:[%s]) { id text } } } } }'
             ) % (BOARD, ids)
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        pages += 1
        cursor = page.get("cursor")
        if not cursor:
            break
        if pages > 40:
            raise RuntimeError("pagination did not terminate")
    print(f"  read {len(items)} rows over {pages} pages", flush=True)
    return items


def values(item):
    """column id -> text, with empties present rather than missing."""
    return {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}


def write(updates, per_request=10):
    """updates: [(item_id, {column_id: value})]"""
    done = 0
    for i in range(0, len(updates), per_request):
        chunk = updates[i:i + per_request]
        parts, variables = [], {}
        for n, (iid, payload) in enumerate(chunk):
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'm{n}: change_multiple_column_values(board_id: {BOARD}, '
                         f'item_id: {iid}, column_values: $v{n}, create_labels_if_missing: true) {{ id }}')
        sig = ", ".join(f"$v{n}: JSON!" for n in range(len(chunk)))
        api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        if done % 100 == 0 or done == len(updates):
            print(f"  written {done}/{len(updates)}", flush=True)
        time.sleep(0.35)


def ident_hash(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]


def note(line):
    """Put a line on the run's summary page as well as in the log, so a silent
    failure is visible without opening the step."""
    print(line, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


def guard(main, label):
    """Run main(). Never fail the workflow - that only produces failure emails -
    but make the failure impossible to miss on the run page."""
    if not TOKEN:
        print("MONDAY_TOKEN not set - skipping " + label)
        return 0
    try:
        return main() or 0
    except Exception as e:
        note(f"**{label} DID NOT RUN — the board was not updated.** `{e}`")
        print("=" * 70)
        print(f"  {label} FAILED: {e}")
        print("=" * 70, flush=True)
        return 0
