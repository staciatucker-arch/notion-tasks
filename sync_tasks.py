#!/usr/bin/env python3
"""
Task Tracker sync.

Runs nightly. Reads the recurrence rules out of the Notion database, makes
sure a dated task exists for every upcoming occurrence, then clears the
finished day away.

Three things are safe by construction:

  * A Unique task is never archived while it's unfinished, so a one-off you
    still owe yourself never disappears.
  * A Weekly, Monthly or Quarterly task carries over until its next
    occurrence spawns - an unfinished Thursday chore is still there on
    Friday.
  * Anything flagged Accomplishment is kept forever.

Daily tasks refresh instead of carrying over: yesterday's copy retires and
today's stands alone, because the rule brings it back every morning anyway.
See GRACE_DAYS to change that per type.

Archived pages go to Notion's trash and are recoverable there for 30 days.

Set DRY_RUN=1 to print what would be archived without touching anything.
"""

import os
import sys
import json
import calendar
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- settings

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get(
    "NOTION_DATABASE_ID", "4f8906246e014a85966f00beae08ad95"
)

# Print what would be archived, archive nothing. Creation is skipped too, so
# a dry run never writes anything at all.
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")

# GitHub runners keep their clock in UTC, and a small-hours Eastern run lands
# on a different UTC date depending on the season. Resolving "today" against a
# named zone means daylight saving can shift when the job fires without ever
# shifting which day it thinks it is.
LOCAL_TZ = ZoneInfo("America/New_York")

# How far ahead to create tasks. 60 days keeps the Calendar and This Week
# tabs looking populated without flooding the database.
HORIZON_DAYS = 60

# Checkbox properties.
DONE_PROP = "Done"           # finished it
SKIP_PROP = "Won't Do"       # deliberately skipping this occurrence
KEEP_PROP = "Accomplishment" # worth remembering; never archived

# How long an unfinished instance keeps showing in Today after its own date
# has passed. Keyed by the Type property.
#
#   0    -> refreshes overnight. Yesterday's copy retires, today's stands
#           alone. The rule brings it back tomorrow, so nothing is lost.
#   N    -> N days of grace, then it retires.
#   None -> carries over until its next occurrence spawns, at which point the
#           older copy is superseded. Right for anything that recurs less
#           often than daily: an unfinished Thursday task should still be
#           there on Friday, and should keep surfacing until next Thursday
#           replaces it.
#
# Unique tasks have no Rule Key and are never retired by this logic at all -
# nothing would ever bring them back.
GRACE_DAYS = {
    "Daily": 0,
    "Weekly": None,
    "Monthly": None,
    "Quarterly": None,
}

# Used when a task's Type is missing or unrecognised. None is the cautious
# choice: carry it over rather than retire something we don't understand.
DEFAULT_GRACE = None

# Circuit breaker. A correct run archives a handful of rows. Dozens means
# something is wrong - a clock skew, a duplicated Rule Key, a bad edit - and
# it's better to stop than to empty the database into the trash.
MAX_ARCHIVES = 60

# Weekday codes used in the Recurrence field.
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
DAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"


# ---------------------------------------------------------------- api layer

def call(method, path, payload=None):
    """Make one Notion API request and return the parsed JSON."""
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ! Notion API error {e.code} on {method} {path}")
        print(f"    {body[:400]}")
        raise


def query_all(filter_obj=None):
    """Fetch every row matching a filter, following pagination."""
    results = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if filter_obj:
            payload["filter"] = filter_obj
        if cursor:
            payload["start_cursor"] = cursor
        data = call("POST", f"/databases/{DATABASE_ID}/query", payload)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


# ------------------------------------------------------------ property help

def text_of(page, prop):
    """Pull a plain string out of a title or rich_text property."""
    p = page["properties"].get(prop, {})
    parts = p.get("title") or p.get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in parts).strip()


def select_of(page, prop):
    p = page["properties"].get(prop, {})
    sel = p.get("select")
    return sel.get("name") if sel else None


def checkbox_of(page, prop):
    """False for an unchecked box and for a property that doesn't exist yet."""
    return page["properties"].get(prop, {}).get("checkbox", False)


def date_of(page, prop="Date"):
    p = page["properties"].get(prop, {})
    d = p.get("date")
    if not d or not d.get("start"):
        return None
    return date.fromisoformat(d["start"][:10])


# ------------------------------------------------------------ date math

def nth_weekday(year, month, n, weekday):
    """
    The date of the nth given weekday in a month.
    n=1 means first, n=2 second, and so on. n=-1 means last.
    Returns None if that occurrence doesn't exist (e.g. a 5th Saturday).
    """
    if n == -1:
        last = calendar.monthrange(year, month)[1]
        d = date(year, month, last)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    d += timedelta(weeks=n - 1)
    return d if d.month == month else None


def occurrences(spec, start, end):
    """
    Every date this recurrence spec lands on between start and end inclusive.

    Note that `start` is always today. Nothing is ever generated for a past
    date. That is what makes retiring yesterday safe - a cleared task can
    never be recreated by the next morning's run - but it also means a rule
    added today will not backfill the occurrence it just missed.

    Supported specs:
      daily            every day
      weekly:TH        every Thursday
      monthly:2SA      2nd Saturday of every month
      monthly:-1SU     last Sunday of every month
      quarterly:1SA    1st Saturday of Jan / Apr / Jul / Oct
    """
    spec = (spec or "").strip().lower()
    if not spec:
        return []

    if spec == "daily":
        out, d = [], start
        while d <= end:
            out.append(d)
            d += timedelta(days=1)
        return out

    if ":" not in spec:
        print(f"  ? Skipping unrecognised recurrence: {spec!r}")
        return []

    kind, arg = spec.split(":", 1)
    arg = arg.upper()

    if kind == "weekly":
        target = WEEKDAYS.get(arg)
        if target is None:
            print(f"  ? Unknown weekday in: {spec!r}")
            return []
        out, d = [], start
        while d <= end:
            if d.weekday() == target:
                out.append(d)
            d += timedelta(days=1)
        return out

    if kind in ("monthly", "quarterly"):
        # arg looks like "2SA" or "-1SU"
        code = arg[-2:]
        target = WEEKDAYS.get(code)
        if target is None:
            print(f"  ? Unknown weekday in: {spec!r}")
            return []
        try:
            n = int(arg[:-2])
        except ValueError:
            print(f"  ? Unreadable position in: {spec!r}")
            return []

        out = []
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            eligible = (m in (1, 4, 7, 10)) if kind == "quarterly" else True
            if eligible:
                d = nth_weekday(y, m, n, target)
                if d and start <= d <= end:
                    out.append(d)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    print(f"  ? Unrecognised recurrence kind: {spec!r}")
    return []


# ------------------------------------------------------------ the work

def spawn_missing(rules, existing, today):
    """Create any recurring task that should exist in the window but doesn't."""
    horizon = today + timedelta(days=HORIZON_DAYS)

    # What already exists, keyed by (rule key, date).
    have = set()
    for page in existing:
        key = text_of(page, "Rule Key")
        d = date_of(page)
        if key and d:
            have.add((key, d))

    created = 0
    for rule in rules:
        key = text_of(rule, "Rule Key")
        name = text_of(rule, "Name")
        spec = text_of(rule, "Recurrence")

        if not key:
            label = name or "(unnamed row)"
            print(f"  ! '{label}' has no Rule Key - skipping")
            continue
        if not name:
            print(f"  ! rule {key!r} has no Name - skipping")
            continue
        if not spec:
            print(f"  ! rule {key!r} has no Recurrence - skipping")
            continue

        for when in occurrences(spec, today, horizon):
            if (key, when) in have:
                continue

            props = {
                "Name": {"title": [{"text": {"content": name}}]},
                "Date": {"date": {"start": when.isoformat()}},
                "Rule Key": {"rich_text": [{"text": {"content": key}}]},
                DONE_PROP: {"checkbox": False},
                SKIP_PROP: {"checkbox": False},
                "Is Rule": {"checkbox": False},
                "Day of Week": {"select": {"name": DAY_NAMES[when.weekday()]}},
            }
            t = select_of(rule, "Type")
            if t:
                props["Type"] = {"select": {"name": t}}
            tb = select_of(rule, "Time Block")
            if tb:
                props["Time Block"] = {"select": {"name": tb}}

            if not DRY_RUN:
                call("POST", "/pages", {
                    "parent": {"database_id": DATABASE_ID},
                    "properties": props,
                })
            have.add((key, when))
            created += 1
            print(f"  + {name} -> {when.isoformat()}")

    return created


def archive(page, reason):
    """Send one page to Notion's trash."""
    if not DRY_RUN:
        call("PATCH", f"/pages/{page['id']}", {"archived": True})
    when = date_of(page)
    stamp = when.isoformat() if when else "no date"
    print(f"  - {reason}: {text_of(page, 'Name')} ({stamp})")


def cleanup(existing, today):
    """
    Clear the finished day away.

    Applied in order:

      1. Flagged Accomplishment  -> kept forever, that's the whole point.

      2. Settled and dated       -> archived. "Settled" means Done or Won't
         before today               Do. Today's own checkmarks are left alone
                                    so the day still reads as a day you got
                                    through.

      3. Unfinished, no          -> kept. A Unique task is the only copy
         Rule Key                   there will ever be, so it keeps surfacing
                                    until it's dealt with.

      4. Unfinished, grace = N   -> retired once it is more than N days past.
         (Daily, N=0)               Refreshing, not carrying over: the rule
                                    puts a clean copy on today's list every
                                    morning, so yesterday's is redundant the
                                    moment the day turns. This does not
                                    depend on the new copy already existing,
                                    so a missed nightly run leaves no residue
                                    to untangle.

      5. Unfinished, grace       -> carried over, then superseded once a newer
         = None (Weekly and         past-or-today copy exists. An unfinished
         longer)                    Thursday task is still there on Friday and
                                    keeps surfacing until next Thursday
                                    replaces it.

    Skipping an occurrence never touches the next one. Each copy carries its
    own date and is created independently from the rule, so a Weekly marked
    Won't Do this Thursday still appears next Thursday.
    """
    cleared = 0
    retired = 0
    doomed = []
    carry = {}

    for page in existing:
        d = date_of(page)
        if not d:
            continue

        # 1. Accomplishments are kept forever.
        if checkbox_of(page, KEEP_PROP):
            continue

        # 2. Settled and in the past.
        if checkbox_of(page, DONE_PROP) or checkbox_of(page, SKIP_PROP):
            if d < today:
                doomed.append((page, "cleared"))
                cleared += 1
            continue

        # 3. Unfinished one-off: always carries over.
        key = text_of(page, "Rule Key")
        if not key:
            continue

        grace = GRACE_DAYS.get(select_of(page, "Type"), DEFAULT_GRACE)

        # 5. Carry over until a newer copy exists, then supersede.
        if grace is None:
            if d <= today:
                carry.setdefault(key, []).append((d, page))
            continue

        # 4. Refresh: the day is over and the rule will bring it back.
        if (today - d).days > grace:
            doomed.append((page, "expired"))
            retired += 1

    for key, entries in carry.items():
        entries.sort(key=lambda x: x[0])
        # entries[-1] is the newest past-or-today copy; everything before it
        # is a duplicate of the same unfinished chore.
        for d, page in entries[:-1]:
            doomed.append((page, "superseded"))
            retired += 1

    if len(doomed) > MAX_ARCHIVES:
        print(f"\n  !! {len(doomed)} rows are eligible for archiving.")
        for page, reason in doomed[:10]:
            when = date_of(page)
            stamp = when.isoformat() if when else "no date"
            print(f"     {reason}: {text_of(page, 'Name')} ({stamp})")
        print(f"     ...and {len(doomed) - 10} more.")
        raise SystemExit(
            f"Refusing to archive more than {MAX_ARCHIVES} rows in one run. "
            f"Check the system clock and look for duplicated Rule Keys. "
            f"Re-run with DRY_RUN=1 to inspect, or raise MAX_ARCHIVES if this "
            f"really is a one-off backlog."
        )

    for page, reason in doomed:
        archive(page, reason)

    return cleared, retired


def main():
    if not NOTION_TOKEN:
        print("NOTION_TOKEN is not set. Nothing to do.")
        return 1

    today = datetime.now(LOCAL_TZ).date()
    mode = "  [DRY RUN - nothing will be written]" if DRY_RUN else ""
    print(f"Task Tracker sync - {today.isoformat()} (America/New_York){mode}")

    print("\nReading rules...")
    rules = query_all({"property": "Is Rule", "checkbox": {"equals": True}})
    print(f"  found {len(rules)} recurrence rules")

    print("\nReading existing tasks...")
    existing = query_all({"property": "Is Rule", "checkbox": {"equals": False}})
    print(f"  found {len(existing)} tasks")

    print("\nCreating anything missing...")
    created = spawn_missing(rules, existing, today)
    print(f"  created {created}")

    print("\nClearing yesterday...")
    if DRY_RUN:
        # Nothing was actually created, so re-reading would miss the copies
        # that a real run would have made. Work from what we already have.
        pass
    else:
        # Re-read so we account for what we just made.
        existing = query_all({"property": "Is Rule", "checkbox": {"equals": False}})
    cleared, retired = cleanup(existing, today)
    print(f"  cleared {cleared} settled, retired {retired}")

    print(f"\nDone. {created} created, {cleared} cleared, {retired} retired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
