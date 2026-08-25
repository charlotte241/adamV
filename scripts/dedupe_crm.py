#!/usr/bin/env python3
"""Find duplicate people on the RPM Outreach board and tag them in place.

Four checks, in order of confidence. A row is tagged with the strongest match
found, and the row it clashes with is written into "Duplicate of" so the pair
can be judged without hunting.

  1 Same email                 - certain duplicate
  2 Same name                  - almost certainly one person, two records
  3 Near-identical name        - Ady/Adrian, Kate/Katie, transposed, typo'd
  4 Name does not match email  - not a duplicate, but the row is mislabelled,
                                 which is how duplicates get created later

Nothing is deleted and nothing is merged. Merging is a judgement call and
belongs to a person. This only makes the pairs visible.

Set DEDUPE_WRITE=0 to report without touching the board.
"""
import json, os, sys, time, re, unicodedata, difflib, collections
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping dedupe")
    sys.exit(0)
WRITE = os.environ.get("DEDUPE_WRITE", "1") != "0"

BOARD = "18428135685"
EMAIL = "email_mm5adkxb"
FULLNAME = "text_mm5b3my9"
FLAG = "color_mm6hf499"     # Duplicate check
OF   = "text_mm6hn0tj"      # Duplicate of

def api(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "errors" in r:
                raise RuntimeError(r["errors"])
            return r["data"]
        except Exception as e:
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday call failed ({e}) - retry {attempt+1}/4 in {wait}s")
            time.sleep(wait)
    raise RuntimeError("monday unreachable")

def norm(s):
    """Lowercase, strip accents and punctuation, collapse spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return " ".join(s.split())

# short forms that routinely produce two records for one person
NICK = {
    "ady": "adrian", "adie": "adrian", "kate": "katherine", "katie": "katherine",
    "katy": "katherine", "cathy": "katherine", "dave": "david", "danny": "daniel",
    "dan": "daniel", "mike": "michael", "mick": "michael", "chris": "christopher",
    "steve": "stephen", "stevie": "stephen", "jim": "james", "jimmy": "james",
    "jamie": "james", "bob": "robert", "rob": "robert", "bobby": "robert",
    "bill": "william", "will": "william", "billy": "william", "liz": "elizabeth",
    "beth": "elizabeth", "betsy": "elizabeth", "tom": "thomas", "tommy": "thomas",
    "nick": "nicholas", "tony": "anthony", "andy": "andrew", "drew": "andrew",
    "sam": "samuel", "ben": "benjamin", "matt": "matthew", "greg": "gregory",
    "jo": "joanne", "joe": "joseph", "sue": "susan", "pete": "peter",
    "ric": "richard", "rick": "richard", "dick": "richard", "ed": "edward",
    "ted": "edward", "alex": "alexander", "sandy": "alexander", "gerry": "gerald",
    "jerry": "gerald", "les": "leslie", "ken": "kenneth", "vik": "vikram",
    "raj": "rajesh", "manny": "emmanuel", "abi": "abimbola",
}
def canon(name):
    parts = norm(name).split()
    if not parts:
        return ""
    parts = [NICK.get(p, p) for p in parts]
    return " ".join(parts)

def surname_key(name):
    p = canon(name).split()
    return (p[-1], p[0][:1]) if len(p) >= 2 else (canon(name), "")

def local_part(e):
    return re.sub(r"[^a-z]", "", (e or "").split("@")[0].lower())

GENERIC = {"info", "office", "admin", "hello", "enquiries", "contact", "accounts",
           "sales", "mail", "team", "support", "payments", "property"}

def main():
    items, cursor = [], None
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor '
             'items { id name column_values(ids:["%s","%s","%s","%s"]) { id text } } } } }'
             ) % (BOARD, EMAIL, FULLNAME, FLAG, OF)
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(items)} rows on the board\n")

    rows = []
    for it in items:
        cv = {c["id"]: (c["text"] or "") for c in it["column_values"]}
        display = (cv.get(FULLNAME) or it["name"]).strip()
        rows.append({"id": it["id"], "name": it["name"].strip(), "disp": display,
                     "email": cv.get(EMAIL, "").strip().lower(),
                     "flag": cv.get(FLAG, ""), "of": cv.get(OF, "")})

    findings = collections.defaultdict(list)   # item id -> list of (rank, label, partner)

    # 1 same email
    by_email = collections.defaultdict(list)
    for r in rows:
        if r["email"]:
            by_email[r["email"]].append(r)
    for em, grp in by_email.items():
        if len(grp) > 1:
            for r in grp:
                others = [g["name"] for g in grp if g["id"] != r["id"]]
                findings[r["id"]].append((1, "Same email", f"{', '.join(others)} ({em})"))

    # 2 same name
    by_name = collections.defaultdict(list)
    for r in rows:
        k = canon(r["name"])
        if k:
            by_name[k].append(r)
    for k, grp in by_name.items():
        if len(grp) > 1:
            for r in grp:
                others = [f"{g['name']} <{g['email'] or 'no email'}>" for g in grp if g["id"] != r["id"]]
                findings[r["id"]].append((2, "Same name", "; ".join(others)))

    # 3 near-identical name
    idx = collections.defaultdict(list)
    for r in rows:
        idx[surname_key(r["name"])].append(r)
    for k, grp in idx.items():
        if len(grp) < 2 or not k[0]:
            continue
        for i, a in enumerate(grp):
            for b in grp[i+1:]:
                if canon(a["name"]) == canon(b["name"]):
                    continue                      # already caught as same name
                if difflib.SequenceMatcher(None, canon(a["name"]), canon(b["name"])).ratio() >= 0.82:
                    findings[a["id"]].append((3, "Near-identical name", f"{b['name']} <{b['email'] or 'no email'}>"))
                    findings[b["id"]].append((3, "Near-identical name", f"{a['name']} <{a['email'] or 'no email'}>"))

    # 4 name does not match email  (skips shared/company mailboxes)
    for r in rows:
        if not r["email"] or not r["name"]:
            continue
        lp = local_part(r["email"])
        if not lp or lp in GENERIC:
            continue
        parts = [p for p in norm(r["name"]).split() if len(p) > 2]
        if not parts:
            continue
        if not any(p in lp or lp.startswith(p[:4]) or p[:4] in lp for p in parts):
            findings[r["id"]].append((4, "Name does not match email", r["email"]))

    # strongest finding wins
    LABELS = {1: "Same email", 2: "Same name", 3: "Near-identical name",
              4: "Name does not match email"}
    tally = collections.Counter()
    updates = []
    for r in rows:
        f = sorted(findings.get(r["id"], []))
        if f:
            rank, label, partner = f[0]
            partner = "; ".join(dict.fromkeys(x[2] for x in f if x[0] == rank))[:250]
        else:
            rank, label, partner = 0, "Clean", ""
        tally[label] += 1
        if r["flag"] == "Checked - not a duplicate":
            continue                              # never overwrite a human decision
        if r["flag"] != label or r["of"] != partner:
            updates.append((r["id"], {FLAG: {"label": label}, OF: partner}))

    print("RESULT")
    for k in ["Same email", "Same name", "Near-identical name",
              "Name does not match email", "Clean"]:
        print(f"  {k:<28} {tally[k]}")
    print()

    for rank, header in [(1, "SAME EMAIL"), (2, "SAME NAME"), (3, "NEAR-IDENTICAL NAME")]:
        listed = [(r, sorted(findings[r["id"]])[0]) for r in rows
                  if findings.get(r["id"]) and sorted(findings[r["id"]])[0][0] == rank]
        if listed:
            print(f"{header} ({len(listed)} rows)")
            for r, (_, _, partner) in listed:
                print(f"   {r['name']:<28} <{r['email'] or 'no email':<38}>  <->  {partner}")
            print()

    if not WRITE:
        print("DEDUPE_WRITE=0 - board not modified")
        return 0

    done = 0
    for i in range(0, len(updates), 12):
        chunk = updates[i:i+12]
        parts, variables = [], {}
        for n, (iid, payload) in enumerate(chunk):
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'm{n}: change_multiple_column_values(board_id: {BOARD}, '
                         f'item_id: {iid}, column_values: $v{n}) {{ id }}')
        sig = ", ".join(f"$v{n}: JSON!" for n in range(len(chunk)))
        api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        print(f"  tagged {done}/{len(updates)}")
        time.sleep(0.4)
    print("dedupe tagging complete")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"dedupe failed, board untouched: {e}")
        sys.exit(0)
#!/usr/bin/env python3
"""Find duplicate people on the RPM Outreach board and tag them in place.

Four checks, in order of confidence. A row is tagged with the strongest match
found, and the row it clashes with is written into "Duplicate of" so the pair
can be judged without hunting.

  1 Same email                 - certain duplicate
  2 Same name                  - almost certainly one person, two records
  3 Near-identical name        - Ady/Adrian, Kate/Katie, transposed, typo'd
  4 Name does not match email  - not a duplicate, but the row is mislabelled,
                                 which is how duplicates get created later

Nothing is deleted and nothing is merged. Merging is a judgement call and
belongs to a person. This only makes the pairs visible.

Set DEDUPE_WRITE=0 to report without touching the board.
"""
import json, os, sys, time, re, unicodedata, difflib, collections
import urllib.request

TOKEN = os.environ.get("MONDAY_TOKEN", "").strip()
if not TOKEN:
    print("MONDAY_TOKEN not set - skipping dedupe")
    sys.exit(0)
WRITE = os.environ.get("DEDUPE_WRITE", "1") != "0"

BOARD = "18422366230"
EMAIL = "email_mm5adkxb"
FULLNAME = "text_mm5b3my9"
FLAG = "color_mm6hf499"     # Duplicate check
OF   = "text_mm6hn0tj"      # Duplicate of

def api(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.monday.com/v2", data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json",
                 "API-Version": "2024-10"})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "errors" in r:
                raise RuntimeError(r["errors"])
            return r["data"]
        except Exception as e:
            wait = min(2 ** attempt * 5, 40)
            print(f"  monday call failed ({e}) - retry {attempt+1}/4 in {wait}s")
            time.sleep(wait)
    raise RuntimeError("monday unreachable")

def norm(s):
    """Lowercase, strip accents and punctuation, collapse spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return " ".join(s.split())

# short forms that routinely produce two records for one person
NICK = {
    "ady": "adrian", "adie": "adrian", "kate": "katherine", "katie": "katherine",
    "katy": "katherine", "cathy": "katherine", "dave": "david", "danny": "daniel",
    "dan": "daniel", "mike": "michael", "mick": "michael", "chris": "christopher",
    "steve": "stephen", "stevie": "stephen", "jim": "james", "jimmy": "james",
    "jamie": "james", "bob": "robert", "rob": "robert", "bobby": "robert",
    "bill": "william", "will": "william", "billy": "william", "liz": "elizabeth",
    "beth": "elizabeth", "betsy": "elizabeth", "tom": "thomas", "tommy": "thomas",
    "nick": "nicholas", "tony": "anthony", "andy": "andrew", "drew": "andrew",
    "sam": "samuel", "ben": "benjamin", "matt": "matthew", "greg": "gregory",
    "jo": "joanne", "joe": "joseph", "sue": "susan", "pete": "peter",
    "ric": "richard", "rick": "richard", "dick": "richard", "ed": "edward",
    "ted": "edward", "alex": "alexander", "sandy": "alexander", "gerry": "gerald",
    "jerry": "gerald", "les": "leslie", "ken": "kenneth", "vik": "vikram",
    "raj": "rajesh", "manny": "emmanuel", "abi": "abimbola",
}
def canon(name):
    parts = norm(name).split()
    if not parts:
        return ""
    parts = [NICK.get(p, p) for p in parts]
    return " ".join(parts)

def surname_key(name):
    p = canon(name).split()
    return (p[-1], p[0][:1]) if len(p) >= 2 else (canon(name), "")

def local_part(e):
    return re.sub(r"[^a-z]", "", (e or "").split("@")[0].lower())

GENERIC = {"info", "office", "admin", "hello", "enquiries", "contact", "accounts",
           "sales", "mail", "team", "support", "payments", "property"}

def main():
    items, cursor = [], None
    while True:
        q = ('query($c:String){ boards(ids:[%s]){ items_page(limit:250, cursor:$c){ cursor '
             'items { id name column_values(ids:["%s","%s","%s","%s"]) { id text } } } } }'
             ) % (BOARD, EMAIL, FULLNAME, FLAG, OF)
        page = api(q, {"c": cursor})["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"{len(items)} rows on the board\n")

    rows = []
    for it in items:
        cv = {c["id"]: (c["text"] or "") for c in it["column_values"]}
        display = (cv.get(FULLNAME) or it["name"]).strip()
        rows.append({"id": it["id"], "name": it["name"].strip(), "disp": display,
                     "email": cv.get(EMAIL, "").strip().lower(),
                     "flag": cv.get(FLAG, ""), "of": cv.get(OF, "")})

    findings = collections.defaultdict(list)   # item id -> list of (rank, label, partner)

    # 1 same email
    by_email = collections.defaultdict(list)
    for r in rows:
        if r["email"]:
            by_email[r["email"]].append(r)
    for em, grp in by_email.items():
        if len(grp) > 1:
            for r in grp:
                others = [g["name"] for g in grp if g["id"] != r["id"]]
                findings[r["id"]].append((1, "Same email", f"{', '.join(others)} ({em})"))

    # 2 same name
    by_name = collections.defaultdict(list)
    for r in rows:
        k = canon(r["name"])
        if k:
            by_name[k].append(r)
    for k, grp in by_name.items():
        if len(grp) > 1:
            for r in grp:
                others = [f"{g['name']} <{g['email'] or 'no email'}>" for g in grp if g["id"] != r["id"]]
                findings[r["id"]].append((2, "Same name", "; ".join(others)))

    # 3 near-identical name
    idx = collections.defaultdict(list)
    for r in rows:
        idx[surname_key(r["name"])].append(r)
    for k, grp in idx.items():
        if len(grp) < 2 or not k[0]:
            continue
        for i, a in enumerate(grp):
            for b in grp[i+1:]:
                if canon(a["name"]) == canon(b["name"]):
                    continue                      # already caught as same name
                if difflib.SequenceMatcher(None, canon(a["name"]), canon(b["name"])).ratio() >= 0.82:
                    findings[a["id"]].append((3, "Near-identical name", f"{b['name']} <{b['email'] or 'no email'}>"))
                    findings[b["id"]].append((3, "Near-identical name", f"{a['name']} <{a['email'] or 'no email'}>"))

    # 4 name does not match email  (skips shared/company mailboxes)
    for r in rows:
        if not r["email"] or not r["name"]:
            continue
        lp = local_part(r["email"])
        if not lp or lp in GENERIC:
            continue
        parts = [p for p in norm(r["name"]).split() if len(p) > 2]
        if not parts:
            continue
        if not any(p in lp or lp.startswith(p[:4]) or p[:4] in lp for p in parts):
            findings[r["id"]].append((4, "Name does not match email", r["email"]))

    # strongest finding wins
    LABELS = {1: "Same email", 2: "Same name", 3: "Near-identical name",
              4: "Name does not match email"}
    tally = collections.Counter()
    updates = []
    for r in rows:
        f = sorted(findings.get(r["id"], []))
        if f:
            rank, label, partner = f[0]
            partner = "; ".join(dict.fromkeys(x[2] for x in f if x[0] == rank))[:250]
        else:
            rank, label, partner = 0, "Clean", ""
        tally[label] += 1
        if r["flag"] == "Checked - not a duplicate":
            continue                              # never overwrite a human decision
        if r["flag"] != label or r["of"] != partner:
            updates.append((r["id"], {FLAG: {"label": label}, OF: partner}))

    print("RESULT")
    for k in ["Same email", "Same name", "Near-identical name",
              "Name does not match email", "Clean"]:
        print(f"  {k:<28} {tally[k]}")
    print()

    for rank, header in [(1, "SAME EMAIL"), (2, "SAME NAME"), (3, "NEAR-IDENTICAL NAME")]:
        listed = [(r, sorted(findings[r["id"]])[0]) for r in rows
                  if findings.get(r["id"]) and sorted(findings[r["id"]])[0][0] == rank]
        if listed:
            print(f"{header} ({len(listed)} rows)")
            for r, (_, _, partner) in listed:
                print(f"   {r['name']:<28} <{r['email'] or 'no email':<38}>  <->  {partner}")
            print()

    if not WRITE:
        print("DEDUPE_WRITE=0 - board not modified")
        return 0

    done = 0
    for i in range(0, len(updates), 12):
        chunk = updates[i:i+12]
        parts, variables = [], {}
        for n, (iid, payload) in enumerate(chunk):
            variables[f"v{n}"] = json.dumps(payload)
            parts.append(f'm{n}: change_multiple_column_values(board_id: {BOARD}, '
                         f'item_id: {iid}, column_values: $v{n}) {{ id }}')
        sig = ", ".join(f"$v{n}: JSON!" for n in range(len(chunk)))
        api("mutation(" + sig + "){ " + " ".join(parts) + " }", variables)
        done += len(chunk)
        print(f"  tagged {done}/{len(updates)}")
        time.sleep(0.4)
    print("dedupe tagging complete")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"dedupe failed, board untouched: {e}")
        sys.exit(0)
