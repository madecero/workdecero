#!/usr/bin/env python3
"""Build a demo Obsidian vault filled with invented tracker notes.

Used to look at the views in Obsidian and as the fixture for the vault tests.
Dates are set relative to today, so every view has rows whenever it is built.
Every person, company, group and event here is made up.

Usage: make_demo_vault.py TARGET_DIR [--folder NAME]
Standard library only, Python 3.9+.
"""

import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "trackers"))

import vault  # noqa: E402

SCRATCH_MARKER = ".workdecero-scratch"

USER_NOTES = {
    "Reading list.md": "---\nstatus: reading\ntype: book\ntags:\n  - personal\ncompany: Penguin\n---\n"
                       "# Reading list\n\nMy own note. Nothing here belongs to workdecero.\n",
    "Projects/Kitchen remodel.md": "---\nstatus: planning\ntype: project\n---\nQuotes due Friday.\n",
}


def build(target, folder="workdecero", today=None):
    """Create the vault at `target` and return the VaultStore used."""
    today = today or datetime.date.today()

    def day(offset):
        return (today + datetime.timedelta(days=offset)).isoformat()

    os.makedirs(target, exist_ok=True)
    # Marks the folder as a throwaway vault. dev/obsidian_check.mjs refuses any vault without it.
    with open(os.path.join(target, SCRATCH_MARKER), "w", encoding="utf-8") as handle:
        handle.write("A demo vault made by dev/make_demo_vault.py. Safe to delete.\n")
    for name, text in USER_NOTES.items():
        path = os.path.join(target, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)

    store = vault.VaultStore(target, folder, backup_dir=os.path.join(target + "-backups"), today=today)
    store.ensure_layout()
    link = store.link

    companies = [
        ("O-1", "Front Range Robotics", "Hybrid, 3 days", "Greenhouse", "https://frontrange.example.com"),
        ("O-2", "Mile High Analytics", "Remote first", "Lever", "https://milehigh.example.com"),
        ("O-3", "Alpenglow Freight Systems", "Onsite", "Workday", "https://alpenglow.example.com"),
        ("O-4", "Cascadia Cold Brew Co", "Remote", "None", "https://cascadia.example.com"),
        ("O-5", "Bluebird Health", "Remote first", "Ashby", "https://bluebird.example.com"),
    ]
    for note_id, name, policy, system, site in companies:
        store.upsert("company", note_id, name, {"work_policy": policy, "hiring_system": system, "site": site})

    people = [
        ("P-1", "Priya Raman", "Front Range Robotics", "VP Engineering", "1st", 80, "Worked together in 2019",
         ["VP of Product, Front Range Robotics"], day(3)),
        ("P-2", "Jonas Albrecht", "Mile High Analytics", "Chief of Staff", "2nd", 60, "Shared connection",
         ["Head of Product, Mile High Analytics"], None),
        ("P-3", "Lena Fischer", "Bluebird Health", "Recruiter", "None", 40, "Named on the posting", [], day(10)),
    ]
    for note_id, name, company, role, rel, confidence, why, roles, follow_up in people:
        values = {"company": link("company", company), "role": role, "relationship": rel, "confidence": confidence,
                  "why": why, "linked_roles": [link("job", title) for title in roles],
                  "source_url": "https://example.com/team"}
        store.upsert("person", note_id, name, values)
        if follow_up:
            store.upsert("person", note_id, name, {"follow_up_by": follow_up}, force=["follow_up_by"])

    jobs = [
        # id, title, company, status, fit, pay_min, pay_max, mode, miles, posted, first_seen, closes
        ("J-1", "VP of Product, Front Range Robotics", "Front Range Robotics", "New", 88, 230000, 270000, "Hybrid", 12, -3, -2, 20),
        ("J-2", "Head of Product, Mile High Analytics", "Mile High Analytics", "New", 92, 210000, 240000, "Remote", None, -5, -4, None),
        ("J-3", "Senior Director of Product, Bluebird Health", "Bluebird Health", "New", 9, None, 95000, "Remote", None, -6, -1, None),
        ("J-4", "Director of Product, Alpenglow Freight Systems", "Alpenglow Freight Systems", "New", 71, 180000, 205000, "Onsite", 28, -30, -21, 5),
        ("J-5", "Chief Product Officer, Bluebird Health", "Bluebird Health", "Applied", 85, 260000, 320000, "Remote", None, -30, -25, None),
        ("J-6", "VP Product Strategy, Mile High Analytics", "Mile High Analytics", "Interviewing", 90, 240000, 280000, "Remote", None, -40, -35, None),
        ("J-7", "Group Product Manager, Front Range Robotics", "Front Range Robotics", "Interested", 77, 190000, 220000, "Hybrid", 12, -9, -8, 6),
        ("J-8", "Head of AI Products, Alpenglow Freight Systems", "Alpenglow Freight Systems", "Passed", 64, 200000, 230000, "Onsite", 28, -50, -45, None),
        ("J-9", "VP of Product, Cascadia Cold Brew Co", "Cascadia Cold Brew Co", "Closed", 55, None, None, "Remote", None, -60, -55, None),
    ]
    for (note_id, title, company, status, fit, pay_min, pay_max, mode, miles, posted, first_seen, closes) in jobs:
        values = {"status": status, "role": title.split(", ")[0], "company": link("company", company), "fit": fit,
                  "work_mode": mode,
                  "posted": day(posted), "first_seen": day(first_seen), "last_seen": day(0),
                  "apply_url": "https://jobs.example.com/" + note_id.lower(), "source": "Company site",
                  "updated": day(first_seen)}
        if pay_min is not None:
            values["pay_min"] = pay_min
        if pay_max is not None:
            values["pay_max"] = pay_max
        if miles is not None:
            values["distance_miles"] = miles
        if closes is not None:
            values["closes"] = day(closes)
        store.upsert("job", note_id, title, values,
                     section="**Why it fits:** matches your target level and AI product focus.\n\n"
                             "**Caveats:** none found.")
    store.upsert("job", "J-1", "", {"warm_path": [link("person", "Priya Raman")]})
    store.upsert("job", "J-2", "", {"warm_path": [link("person", "Jonas Albrecht")]})
    user = [
        ("J-5", {"applied_on": day(-14), "next_step": "Nudge the recruiter", "last_touch": day(-14),
                 "contact": link("person", "Lena Fischer")}),
        ("J-6", {"applied_on": day(-30), "next_step": "Panel interview", "next_step_date": day(2),
                 "last_touch": day(-4), "contact": link("person", "Jonas Albrecht")}),
        ("J-7", {"next_step": "Ask Priya about the team", "next_step_date": day(6)}),
        ("J-8", {"reason": "Fully onsite"}),
        ("J-9", {"reason": "Posting closed"}),
    ]
    for note_id, values in user:
        store.upsert("job", note_id, "", values, force=list(values))

    contracts = [
        ("C-1", "Fractional Head of Product, seed-stage fintech", "Fractional marketplace", "New", 86, 180, 220, 12, 16, True, -2),
        ("C-2", "AI product strategy sprint", "Mile High Analytics", "New", 74, 150, 175, 20, 6, True, -5),
        ("C-3", "Product coaching retainer", "Bluebird Health", "Negotiating", 81, 200, 250, 6, 12, True, -20),
        ("C-4", "Roadmap audit, fixed price", "Cascadia Cold Brew Co", "Applied", 60, 120, 140, 10, 4, False, -12),
        ("C-5", "Interim CPO", "Alpenglow Freight Systems", "Lost", 70, 190, 230, 30, 12, False, -40),
    ]
    for note_id, title, client, status, fit, rate_min, rate_max, hours, weeks, remote, seen in contracts:
        store.upsert("contract", note_id, title,
                     {"status": status, "client": client, "via": "direct", "fit": fit, "rate_min": rate_min,
                      "rate_max": rate_max, "hours_per_week": hours, "length_weeks": weeks, "remote": remote,
                      "engagement": "fractional", "link": "https://gigs.example.com/" + note_id.lower(),
                      "first_seen": day(seen), "posted": day(seen - 1), "last_seen": day(0), "updated": day(seen)})
    store.upsert("contract", "C-3", "", {"next_step": "Send the revised scope", "next_step_date": day(1)},
                 force=["next_step", "next_step_date"])

    channels = [
        ("H-1", "Fractional executive marketplace", "Live", 9, "Most fractional product roles are posted here"),
        ("H-2", "Expert network", "In progress", 6, "Paid calls on AI product strategy"),
        ("H-3", "Freelance marketplace", "Not started", 4, "High volume, lower rates"),
        ("H-4", "Advisor directory", "Needs refresh", 5, "Profile is two years old"),
    ]
    for note_id, title, status, value, why in channels:
        store.upsert("channel", note_id, title, {"status": status, "value": value, "why": why,
                                                 "profile_url": "https://profiles.example.com/" + note_id.lower(),
                                                 "last_checked": day(-7)})

    groups = [
        ("G-1", "Denver Product Leaders", "New", "Product executives", "Monthly", 9, False, 0, 90),
        ("G-2", "Front Range AI Builders", "Trying", "Applied AI", "Every two weeks", 4, False, 0, 82),
        ("G-3", "Rocky Mountain Founders Circle", "Joined", "Founders and investors", "Monthly", 15, False, 300, 75),
        ("G-4", "Remote Product Roundtable", "New", "Product leaders, online", "Weekly", 2, True, 0, 68),
    ]
    for note_id, title, status, focus, cadence, next_in, virtual, cost, fit in groups:
        store.upsert("group", note_id, title, {"status": status, "focus": focus, "cadence": cadence,
                                               "next_meeting": day(next_in), "virtual": virtual,
                                               "cost_per_year": cost, "fit": fit, "first_seen": day(-10),
                                               "link": "https://groups.example.com/" + note_id.lower()})

    events = [
        ("E-1", "AI Builders demo night", 4, "18:00", True, False, "Union Station, Denver", 0, "Front Range AI Builders", 84),
        ("E-2", "Product Leaders breakfast", 9, "07:30", False, False, "LoDo, Denver", 25, "Denver Product Leaders", 88),
        ("E-3", "Remote roundtable: pricing AI features", 2, "12:00", False, True, "", 0, "Remote Product Roundtable", 70),
        ("E-4", "Founders Circle fall mixer", 23, "17:30", True, False, "RiNo, Denver", 40, "Rocky Mountain Founders Circle", 77),
        ("E-5", "Product Leaders happy hour", -6, "17:00", True, False, "Cherry Creek, Denver", 0, "Denver Product Leaders", 80),
        ("E-6", "Regional product conference", 55, "09:00", False, False, "Boulder", 350, "Denver Product Leaders", 65),
    ]
    for note_id, title, offset, time, evening, virtual, where, cost, group, fit in events:
        store.upsert("event", note_id, title, {"date": day(offset), "time": time, "evening": evening,
                                               "virtual": virtual, "where": where, "cost": cost,
                                               "group": link("group", group), "fit": fit, "first_seen": day(-8),
                                               "link": "https://events.example.com/" + note_id.lower()})
    store.upsert("event", "E-1", "", {"going": True}, force=["going"])
    store.upsert("event", "E-5", "", {"going": True, "attended": True,
                                      "people_met": [link("person", "Priya Raman")]},
                 force=["going", "attended", "people_met"])

    store.upsert("digest", "D-1", "%s Job sweep" % day(-1),
                 {"date": day(-1), "track": "Jobs", "new": 3, "closed": 1,
                  "touched": [link("job", jobs[0][1]), link("job", jobs[1][1]), link("job", jobs[2][1])]},
                 section="3 new roles, 1 posting closed. Best new fit: Head of Product, Mile High Analytics.")
    store.upsert("digest", "D-2", "%s Networking sweep" % day(-8),
                 {"date": day(-8), "track": "Networking", "new": 4, "closed": 0, "touched": []},
                 section="4 new events in the next 30 days.")
    store.refresh_rollups()
    return store


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    folder = "workdecero"
    if "--folder" in argv:
        index = argv.index("--folder")
        folder = argv[index + 1]
        del argv[index:index + 2]
    store = build(argv[0], folder)
    print("Demo vault ready at %s (%d files written). Open it in Obsidian and start with '%s/Start here'."
          % (argv[0], len(store.changed), folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
