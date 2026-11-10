"""The contract sweep, engine side.

Contract work is found in different places for different people: a designer
on freelance marketplaces, a nurse through travel and per-diem agencies, a
product leader through fractional networks and expert calls. So this sweep
takes its places to look from the person's local map, never from a fixed
list.

    start    re-check open gigs, bring the Channels list up to date, and tell
             research where to look and what kinds of engagement to keep
    intake   take what research found, drop what the profile rules out, merge
             the same gig listed by several agencies, write the notes, and for
             the best few say who to contact first (from the listing and the
             person's own People notes, with no further searching)
    finish   verify, write the digest note (deadlines first, channels to set
             up last) and report
    update   record what the person did: "I applied to C-12", "Upwork is live"

What research is never told: the hourly floor and target, the fixed-price
and retainer minimums, the hours wanted, exclusions, confidential names and
the current employer. It reports what a posting says. Whether that clears
the person's floor is decided here, and a gig that does not is left out
without a reason being printed.

Research only. Nothing here bids, applies, signs up or sends anything.

Standard library only, Python 3.9+.
"""

import datetime
import json
import os

import liveness
import merge
import model
import scoring
import sources
import warmpath

BUDGET = {
    "lite": {"searches": 10, "read": 12, "contact": 3},
    "full": {"searches": 30, "read": 30, "contact": 8},
}
STATE_NAME = "contract-state.json"
DEADLINE_DAYS = 7              # proposal and bid deadlines this close lead the digest
NEXT_STEP_DAYS = 7

# How each accepted way of working is described to research, and the words that mean it in a posting.
ENGAGEMENT_PLAIN = {
    "independent-1099": "independent contractor (1099)",
    "corp-to-corp": "corp-to-corp (C2C), paid to their own company",
    "w2-contract": "W2 contract through an agency",
    "fixed-price": "fixed-price project or statement of work",
    "retainer": "monthly retainer",
    "fractional": "fractional or part-time leadership",
    "expert-call": "paid expert calls and consultations",
    "public-bid": "public bids and requests for proposals",
}
# Map kinds that are places to look, in the order research should work through them.
PLACE_KINDS = ("marketplace", "talent-network", "agency", "expert-network", "bid-portal")
PLACE_WORDS = {"marketplace": "Marketplaces", "talent-network": "Talent networks", "agency": "Agencies",
               "expert-network": "Expert networks", "bid-portal": "Public-bid portals"}
# Places that only pay off once the person has a profile there.
CHANNEL_KINDS = ("marketplace", "talent-network", "expert-network")
CHANNEL_STATUS = {"not-started": "Not started", "in-progress": "In progress", "submitted": "In progress",
                  "vetting": "In progress", "active": "Live", "skip": "Skip"}
CHANNEL_DONE = ("Live", "Skip")

FIND_KEYS = ("title", "client", "via", "url", "source", "job_id", "engagement", "rate_min", "rate_max",
             "fixed_price", "retainer_monthly", "hours_per_week", "length_weeks", "remote", "work_mode",
             "location", "distance_miles", "posted", "closes", "fit_assessment", "why", "deal_breaker",
             "link_verified", "industry", "poster")


def budget(profile):
    return BUDGET.get(profile.get("mode") or "lite", BUDGET["lite"])


def _safe(text, profile):
    """Text for the conversation, with every confidential name taken out."""
    return scoring.scrub_confidential(text, scoring.confidential_names(profile), "[withheld]")


def brief(profile):
    """What research may know about the person. Their field and what they offer, never a number."""
    person, skills = profile.get("person") or {}, profile.get("skills") or {}
    contracts, location = profile.get("contracts") or {}, profile.get("location") or {}
    lines = [
        "Field: %s" % person.get("headline", ""),
        "Background: %s" % (person.get("summary") or "(no summary in the profile)"),
        "Years of experience: %s" % person.get("years_experience", "not given"),
        "Strongest skills: %s" % ", ".join(skills.get("core") or []),
    ]
    if skills.get("secondary"):
        lines.append("Other skills: %s" % ", ".join(skills["secondary"]))
    if skills.get("certifications"):
        lines.append("Certifications held: %s" % ", ".join(skills["certifications"]))
    if skills.get("credentials_missing"):
        lines.append("Credentials NOT held (a gig that requires one is a deal breaker): %s"
                     % ", ".join(skills["credentials_missing"]))
    lines.append("Services they sell: %s" % ", ".join(contracts.get("services") or []))
    if contracts.get("titles"):
        lines.append("Titles to search for: %s" % ", ".join(contracts["titles"]))
    if contracts.get("search_keywords"):
        lines.append("Other search words: %s" % ", ".join(contracts["search_keywords"]))
    place = location.get("home_base", "")
    if location.get("metro"):
        place += " (%s area)" % location["metro"]
    lines.append("Lives in: %s. Work modes accepted: %s. Will travel %s miles for hybrid or onsite." % (
        place, ", ".join(location.get("work_modes") or []), location.get("radius_miles")))
    return _safe("\n".join(lines), profile)


def engagement_rules(profile):
    """Which kinds of engagement to record, in words research can apply to a posting."""
    accepted = (profile.get("contracts") or {}).get("engagement_types") or []
    wanted = [ENGAGEMENT_PLAIN[name] for name in accepted if name in ENGAGEMENT_PLAIN]
    lines = ["Record a gig when it is offered as: %s." % ("; ".join(wanted) or "any kind of contract work")]
    if "w2-contract" not in accepted:
        lines.append("Leave out a gig that is W2 only. Keep one that offers a choice, such as \"W2 or C2C\".")
    lines.append("When a posting does not say how the work is engaged, record it and leave `engagement` out. "
                 "The note will tell the person to confirm the terms.")
    lines.append("Write `engagement` in the posting's own words, such as \"1099\", \"C2C\", \"W2 or C2C\", "
                 "\"retainer\", \"fixed price\", \"expert call\" or \"RFP\".")
    return lines


# --------------------------------------------------------------------------
# State kept between the steps of one sweep. Private to the helper.
# --------------------------------------------------------------------------

def state_path(folder):
    return os.path.join(folder, STATE_NAME)


def save_state(folder, state):
    handle = os.open(state_path(folder), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(state, out, ensure_ascii=False)
    os.chmod(state_path(folder), 0o600)


def load_state(folder):
    path = state_path(folder)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def clear_state(folder):
    path = state_path(folder)
    if os.path.isfile(path) or os.path.islink(path):
        os.remove(path)
    if os.path.isdir(folder) and not os.path.islink(folder) and not os.listdir(folder):
        os.rmdir(folder)


# --------------------------------------------------------------------------
# Channels: platforms that need a profile before they pay off
# --------------------------------------------------------------------------

def _name_key(name):
    return merge.normalize_company(name)


def sync_channels(profile, store, source_map, today=None):
    """Make sure every channel has a note. Returns how many notes were added.

    Channels come from two places: the ones the person named at setup, and the
    marketplaces and networks in their local map. A note that already exists
    is never changed here, because its status belongs to the person.
    """
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    rows = store.rows("channel")
    known = set(_name_key(row.title) for row in rows)
    wanted = []
    for entry in (profile.get("contracts") or {}).get("channels") or []:
        if isinstance(entry, dict) and entry.get("name"):
            wanted.append((entry["name"], CHANNEL_STATUS.get(entry.get("status"), "Not started"), entry.get("url"),
                           "You named it at setup."))
    for entry in source_map.get("contracts") or []:
        if entry.get("kind") in CHANNEL_KINDS and entry.get("name"):
            wanted.append((entry["name"], "Not started", None,
                           entry.get("notes") or "In your local map as a %s." % entry["kind"].replace("-", " ")))
    added, taken = 0, []
    for name, status, url, why in wanted:
        key = _name_key(name)
        if not key or key in known:
            continue
        known.add(key)
        note_id = merge.next_id(model.CHANNEL, rows, taken)
        taken.append(note_id)
        values = {"status": status, "why": scoring.scrub_confidential(why, names)}
        if url:
            values["profile_url"] = url
        store.upsert("channel", note_id, name, values)
        added += 1
    return added


def pending_channels(store):
    """Channel notes the person has not finished setting up: (title, status, why)."""
    pending = []
    for row in store.rows("channel"):
        status = model.CHANNEL.canonical_status(row.values.get("status")) or (row.values.get("status") or "Not started")
        if status not in CHANNEL_DONE:
            pending.append((row.title, status, row.values.get("why") or ""))
    return sorted(pending, key=lambda entry: (entry[1] != "In progress", entry[0].lower()))


# --------------------------------------------------------------------------
# Step 1: start
# --------------------------------------------------------------------------

def places(profile, source_map):
    """Where research should look, from the local map: [(kind, name, url)]."""
    bids = ((profile.get("contracts") or {}).get("public_bids") or {}).get("enabled")
    accepted = (profile.get("contracts") or {}).get("engagement_types") or []
    found = []
    for kind in PLACE_KINDS:
        if kind == "bid-portal" and not (bids or "public-bid" in accepted):
            continue
        if kind == "expert-network" and accepted and "expert-call" not in accepted:
            continue
        for entry in source_map.get("contracts") or []:
            if entry.get("kind") == kind and entry.get("how") != "browser":
                found.append((kind, entry["name"], entry.get("url") or ""))
    return found


def start(profile, store, source_map, fetcher, today=None):
    """Re-check open gigs, bring the Channels list up to date, and describe where to look. Returns the state."""
    today = today or datetime.date.today()
    rows = store.rows("contract")
    operations = liveness.plan_updates(rows, model.CONTRACT, fetcher, today)
    store.apply(operations)
    titles = dict((row.id, row.title) for row in rows)
    closed = [titles[op["id"]] for op in operations if op["state"] == liveness.CLOSED and op.get("append")]
    suspect = [titles[op["id"]] for op in operations if op["values"].get("liveness") == liveness.SUSPECT]
    channels_added = sync_channels(profile, store, source_map, today)
    browser_only = [entry["name"] for entry in source_map.get("contracts") or [] if entry.get("how") == "browser"]
    return {
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "day": today.isoformat(),
        "mode": profile.get("mode") or "lite",
        "checked": len(operations), "closed": closed, "suspect": suspect,
        "channels_added": channels_added,
        "places": [list(place) for place in places(profile, source_map)],
        "not_covered": browser_only,
        "new": [], "seen_again": [], "left_out": 0, "merged": 0, "reposted": [],
        "searches": 0, "finds_read": 0,
    }


def start_report(state, profile):
    """What research is told after start. Checked to carry nothing private."""
    limits = budget(profile)
    lines = ["CONTRACT SWEEP STARTED (%s mode)" % state["mode"], ""]
    lines.append("ALREADY DONE BY THE ENGINE")
    lines.append("  Open gigs re-checked: %d. Found closed: %d. Possibly closed: %d." % (
        state["checked"], len(state["closed"]), len(state["suspect"])))
    if state["channels_added"]:
        lines.append("  Platforms added to the Channels list: %d." % state["channels_added"])
    lines += ["", "ABOUT THE PERSON (all you need to judge fit)"]
    lines += ["  " + line for line in brief(profile).splitlines()]
    lines += ["", "WHICH GIGS TO RECORD"]
    lines += ["  " + line for line in engagement_rules(profile)]
    lines += ["", "YOUR BUDGET THIS RUN: use at most %d web searches, and read up to %d postings." % (
        limits["searches"], limits["read"])]
    lines += ["", "PLACES TO LOOK, from their local map"]
    shown = None
    for kind, name, url in state["places"]:
        if kind != shown:
            lines.append("  %s" % PLACE_WORDS[kind])
            shown = kind
        lines.append("    %s: %s" % (name, url))
    if not state["places"]:
        lines.append("  The local map has no contract sources yet. Search the open web for the services and titles "
                     "above with words such as freelance, contract or fractional.")
    if state["not_covered"]:
        lines += ["", "NOT COVERED WITHOUT A BROWSER (name these in your final message): %s"
                  % ", ".join(state["not_covered"])]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Step 2: intake
# --------------------------------------------------------------------------

def _site(url):
    """The site a link is on, without a leading www."""
    host = merge._host(url or "") or ""
    return host[4:] if host.startswith("www.") else host


def _clean(item):
    return dict((key, item[key]) for key in FIND_KEYS if key in item and item[key] not in (None, ""))


def _undisclosed(item):
    return merge.normalize_company(item.get("client")) in merge._UNDISCLOSED


def _top_rate(item):
    for key in ("rate_max", "rate_min"):
        if isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool):
            return item[key]
    return None


def _same_gig_through_agencies(a, b):
    """The same gig passed around by two agencies: same title and place, and nothing that says otherwise."""
    title_a, title_b = merge.normalize_title(a.get("title")), merge.normalize_title(b.get("title"))
    if not title_a or title_a != title_b:
        return False
    via_a, via_b = merge.normalize_company(a.get("via")), merge.normalize_company(b.get("via"))
    if not via_a or not via_b or via_a == via_b:
        return False
    # One agency posting the title twice is two gigs, also when an earlier copy was already folded in.
    for one, other in ((a, via_b), (b, via_a)):
        if other in [merge.normalize_company(entry.get("source")) for entry in one.get("also_listed") or []]:
            return False
    if not (_undisclosed(a) or _undisclosed(b)):
        return False                 # two named clients: merge.same already decides those
    if merge._plain(a.get("location")) != merge._plain(b.get("location")):
        return False
    for key in ("hours_per_week", "length_weeks"):
        if a.get(key) is not None and b.get(key) is not None and a[key] != b[key]:
            return False
    return True


def _better(a, b):
    """Which listing to keep: a named client first, then the higher rate, then the fuller record."""
    def rank(item):
        return (0 if _undisclosed(item) else 1, _top_rate(item) or 0, len(item))
    return (a, b) if rank(a) >= rank(b) else (b, a)


def merge_agency_copies(items):
    """Fold the same gig listed by several agencies into one. Returns (items, how many were folded in)."""
    kept, folded = [], 0
    for item in items:
        for index, other in enumerate(kept):
            if _same_gig_through_agencies(item, other):
                best, rest = _better(other, item)
                merged = dict(best)
                for key, value in rest.items():
                    if merged.get(key) in (None, "", []):
                        merged[key] = value
                fits = [x.get("fit_assessment") for x in (best, rest)
                        if isinstance(x.get("fit_assessment"), (int, float))]
                if fits:
                    merged["fit_assessment"] = max(fits)
                also = list(best.get("also_listed") or [])
                for entry in [{"source": rest.get("via") or rest.get("source"), "url": rest.get("url")}] + \
                        list(rest.get("also_listed") or []):
                    if entry.get("url") and merge.link_key(entry["url"]) != merge.link_key(merged.get("url")) and \
                            merge.link_key(entry["url"]) not in [merge.link_key(e.get("url")) for e in also]:
                        also.append(entry)
                if also:
                    merged["also_listed"] = also
                kept[index] = merged
                folded += 1
                break
        else:
            kept.append(item)
    return kept, folded


# --------------------------------------------------------------------------
# Who to contact, from what is already known. No web searches are spent on this.
# --------------------------------------------------------------------------

def known_people(item, people_rows, profile):
    """People notes at the client or the agency: [(row, "1st" or "2nd", "the client" or the agency name)]."""
    employer = (profile.get("person") or {}).get("current_employer")
    quiet = warmpath._is_quiet(profile)
    found = []
    for where, label in ((item.get("client"), "the client"), (item.get("via"), item.get("via"))):
        if not where or merge.normalize_company(where) in merge._UNDISCLOSED:
            continue
        for row in people_rows:
            company = merge.link_label(row.values.get("company"))
            if not warmpath._same_company(company, where):
                continue
            if quiet and warmpath._same_company(company, employer):
                continue                         # never route a quiet search through a coworker
            degree = (row.values.get("relationship") or "").strip().lower()
            if degree in ("1st", "2nd") and not any(row is entry[0] for entry in found):
                found.append((row, degree, label))
    return sorted(found, key=lambda entry: (entry[1] != "1st", entry[2] != "the client", entry[0].title.lower()))


def contact_part(item, known, platforms, flagged, store, names):
    """The contact lines for one gig. Returns (text for the note, the "contact first" sentence)."""
    via = (item.get("via") or "").strip()
    on_platform = bool(via) and merge.normalize_company(via) in platforms
    poster = item.get("poster") if isinstance(item.get("poster"), dict) else {}
    poster_name = (poster.get("name") or "").strip()
    likely = poster.get("inferred") is True
    role = (poster.get("title") or "").strip()

    if flagged:
        first = "No one for now. See the caution above"
    elif known:
        row, degree, where = known[0]
        at = "the client" if where == "the client" else where
        if degree == "1st":
            first = "%s (1st), who works at %s, for a warm introduction" % (row.title, at)
        else:
            first = "%s (2nd), through a shared connection. Works at %s" % (row.title, at)
    elif on_platform:
        # The platform is the channel. No address is guessed for someone who posts there.
        first = "Reply on %s" % via
    elif poster_name:
        what = "likely the contact for this gig (not named as such on the listing)" if likely else \
            "named on the listing%s" % (" as %s" % role if role else "")
        first = "%s, %s%s" % (poster_name, what, ", at %s" % via if via else "")
    elif via:
        first = "Apply through %s" % via
    else:
        first = "Apply through the listing"

    lines = ["- **Contact first:** %s." % first]
    if poster_name:
        shown = "%s%s" % ("Likely " if likely else "", poster_name)
        lines.append("- **Named on the listing:** %s%s.%s" % (
            shown, ", %s" % role if role else "",
            " Not named in that role on the listing." if likely else ""))
    else:
        lines.append("- **Named on the listing:** no one.")
    if known and not flagged:
        lines.append("- **People you know there:** " + "; ".join(
            "%s (%s, %s%s)" % (store.link("person", row.title), degree, "at the client" if where == "the client"
                               else "at %s" % where, ", %s" % row.values["role"] if row.values.get("role") else "")
            for row, degree, where in known) + ".")
    else:
        lines.append("- **People you know there:** none in your network at the client or the agency.")
    if on_platform:
        lines.append("- **How to reach them:** through %s. No email address is guessed for a platform gig." % via)
    text = scoring.scrub_confidential("\n".join(lines), names)
    return text, scoring.scrub_confidential(first + ".", names)


def intake(state, handoff, profile, store, today=None):
    """Filter and score what research found, write the notes, and record what happened."""
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    items = []
    for entry in handoff.get("finds") or []:
        if isinstance(entry, dict) and entry.get("title") and entry.get("url"):
            items.append(_clean(entry))
    state["finds_read"] = state.get("finds_read", 0) + len(items)
    # A gig whose link sits on one of the person's platforms came through that platform, said or not.
    hosts = dict((_site(url), name) for kind, name, url in state.get("places") or [] if kind in CHANNEL_KINDS and _site(url))
    for item in items:
        if not item.get("via") and _site(item.get("url")) in hosts:
            item["via"] = hosts[_site(item["url"])]
    items, folded = merge_agency_copies(items)
    state["merged"] = state.get("merged", 0) + folded

    # Knowing someone at the client or the agency raises the odds, so it is worked out before scoring.
    people_rows = store.rows("person")
    known_by_link = {}
    for item in items:
        known = known_people(item, people_rows, profile)
        known_by_link[merge.link_key(item.get("url"))] = (item, known)
        if known:
            item["warm"] = known[0][1]

    result = merge.plan_intake("contract", items, store, profile, today)
    store.apply(result.operations)

    rows = dict((row.id, row) for row in store.rows("contract"))
    for operation, score in result.new:
        row = rows.get(operation["id"])
        if row is None:
            continue
        state["new"].append({"id": row.id, "title": row.title, "fit": score.total,
                             "client": row.values.get("client") or "", "via": row.values.get("via") or "",
                             "url": row.values.get("link")})
    state["new"].sort(key=lambda entry: -entry["fit"])

    # A light contact step for the best few, from the listing and the People notes only.
    platforms = set(merge.normalize_company(row.title) for row in store.rows("channel"))
    exclusions = scoring.active_exclusions(profile, today)
    for entry in [e for e in state["new"] if "contact_first" not in e][:budget(profile)["contact"]]:
        item, known = known_by_link.get(merge.link_key(entry["url"]), ({}, []))
        if not item:
            continue
        exclusion = scoring.match_exclusion(item, item.get("client"), exclusions)
        flagged = exclusion is not None and exclusion.get("action", "skip") != "skip"
        text, first = contact_part(item, known, platforms, flagged, store, names)
        store.apply([{"type": "contract", "id": entry["id"], "values": {}, "force": [],
                      "part": (model.PART_WARM_PATH, text)}])
        entry["contact_first"] = first
        entry["warm"] = known[0][1] if known and not flagged else None
    state["seen_again"] = sorted(set(state["seen_again"]) | set(op["id"] for op in result.seen_again))
    for label, reason in result.dropped:
        if "already in your tracker as" in reason and "Closed" in reason:
            state["reposted"].append(label)
        else:
            state["left_out"] += 1        # the reason stays here: it can hold a floor or an excluded name
    if isinstance(handoff.get("searches"), int) and not isinstance(handoff.get("searches"), bool):
        state["searches"] = max(0, handoff["searches"])
    for name in handoff.get("not_covered") or []:
        if isinstance(name, str) and name.strip() and name.strip() not in state["not_covered"]:
            state["not_covered"].append(name.strip())
    return state


def intake_report(state, profile):
    limits = budget(profile)
    lines = ["INTAKE DONE",
             "  New notes written: %d. Already in the tracker and refreshed: %d. Left out by the profile: %d."
             % (len(state["new"]), len(state["seen_again"]), state["left_out"])]
    if state.get("merged"):
        lines.append("  Copies of the same gig from different agencies folded together: %d." % state["merged"])
    left = limits["searches"] - state.get("searches", 0)
    if not state.get("finds_read") and left >= limits["searches"] // 2 and not state.get("nudged"):
        state["nudged"] = True                  # said once, so a sweep can never loop on it
        lines += ["", "BEFORE YOU FINISH: %d of %d searches are unused and no gig was read." % (left, limits["searches"]),
                  "  Go back to PLACES TO LOOK, open and read the most promising gigs, and run intake again with",
                  "  them under finds. If you searched and nothing real came up, carry on and say so in not_covered."]
    else:
        lines += ["", "Run the finish command next. Do not ask why something was left out."]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Step 3: finish
# --------------------------------------------------------------------------

def verify(state, store):
    """Checks on what this sweep wrote. Returns a list of problems in plain words."""
    problems = []
    rows = dict((row.id, row) for row in store.rows("contract"))
    for entry in state["new"]:
        row = rows.get(entry["id"])
        if row is None:
            problems.append("The note for %s is missing." % entry["title"])
            continue
        if not row.values.get("link"):
            problems.append("%s has no link." % row.title)
        if not isinstance(row.values.get("fit"), int):
            problems.append("%s has no score." % row.title)
        if not model.CONTRACT.canonical_status(row.values.get("status")):
            problems.append("%s has a status the tracker does not know." % row.title)
    open_rows = [row for row in rows.values() if not model.CONTRACT.is_done(row.values.get("status"))]
    for index, row in enumerate(open_rows):
        for other in open_rows[index + 1:]:
            if merge.same("contract", merge.row_as_item("contract", row), merge.row_as_item("contract", other)):
                problems.append("%s and %s look like the same gig." % (row.title, other.title))
    for path, reason in store.skipped:
        problems.append("%s: %s" % (path, reason))
    return problems


def _rate_text(values):
    low, high = values.get("rate_min"), values.get("rate_max")
    if low and high and low != high:
        return "$%d to $%d an hour" % (low, high)
    if high or low:
        return "$%d an hour" % (high or low)
    return "rate not listed"


def deadlines(store, today):
    """Open gigs with something due within a week: proposal deadlines first, then the person's own next steps."""
    due = []
    for row in store.rows("contract"):
        if model.CONTRACT.is_done(row.values.get("status")):
            continue
        link = store.link("contract", row.title)
        closes = scoring._date(row.values.get("closes"))
        if closes and 0 <= (closes - today).days <= DEADLINE_DAYS:
            days = (closes - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else "in %d days" % days
            due.append((closes, "%s closes %s (%s)." % (link, closes.isoformat(), when)))
        step = scoring._date(row.values.get("next_step_date"))
        if step and (step - today).days <= NEXT_STEP_DAYS:
            what = row.values.get("next_step") or "Next step"
            due.append((step, "%s: %s, due %s." % (link, str(what).rstrip("."), step.isoformat())))
    return [text for _, text in sorted(due, key=lambda pair: pair[0])]


def write_digest(state, profile, store, problems, today=None):
    """Write the digest note. Returns (note title, featured entries, how many more, channels still to set up)."""
    today = today or datetime.date.today()
    limit = ((profile.get("digest") or {}).get("max_new_items")) or 10
    featured, more = state["new"][:limit], max(0, len(state["new"]) - limit)
    rows = dict((row.id, row) for row in store.rows("contract"))
    lines = []

    due = deadlines(store, today)
    lines.append("## Due within 7 days")
    lines += ["- " + text for text in due] or ["No proposal or bid deadline in the next week."]

    lines += ["", "## New this sweep"]
    for entry in featured:
        row = rows.get(entry["id"])
        values = row.values if row else {}
        rate = _rate_text(values)
        bits = [rate[:1].upper() + rate[1:]]
        if values.get("engagement"):
            bits.append(str(values["engagement"]))
        else:
            bits.append("terms not stated, confirm them first")
        if values.get("via"):
            bits.append("via %s" % values["via"])
        lines.append("- %s **Win odds %d.** %s.%s" % (
            store.link("contract", entry["title"]), entry["fit"], ", ".join(bits),
            " Contact first: %s" % entry["contact_first"] if entry.get("contact_first") else ""))
    if not featured:
        lines.append("Nothing new that fits this time.")
    if more:
        lines += ["", "%d more %s in your tracker, in the New this week view." % (more, "is" if more == 1 else "are")]

    lines += ["", "## Closed since the last sweep"]
    lines += ["- %s" % store.link("contract", title) for title in state["closed"]] or ["None."]
    if state["suspect"]:
        lines += ["", "Possibly closed, worth a look: "
                  + ", ".join(store.link("contract", title) for title in state["suspect"]) + "."]
    if state["reposted"]:
        lines += ["", "## Reposted",
                  "These were closed in your tracker and are open again. They were not reopened for you."]
        lines += ["- %s" % label for label in state["reposted"]]

    pending = pending_channels(store)
    lines += ["", "## Channels to set up"]
    if pending:
        lines.append("These platforms only pay off once your profile there is live. This list stays in every digest "
                     "until each one is set to Live or Skip.")
        for title, status, why in pending:
            lines.append("- %s: %s.%s" % (store.link("channel", title), status, " " + why.rstrip(".") + "." if why else ""))
    else:
        lines.append("Every channel is live or skipped.")

    lines += ["", "## What this sweep covered"]
    lines.append("- Open gigs re-checked: %d." % state["checked"])
    lines.append("- Places in your local map searched: %d." % len(state["places"]))
    lines.append("- Web searches used: %d of %d (%s mode)." % (state["searches"], budget(profile)["searches"],
                                                              "Lite" if state["mode"] == "lite" else "Full"))
    if state["left_out"]:
        lines.append("- Left out by your profile (how it is engaged, rate or price, exclusions, age): %d." % state["left_out"])
    if state.get("merged"):
        lines.append("- Copies of the same gig from different agencies folded together: %d." % state["merged"])
    lines += ["", "## Not covered"]
    lines += ["- %s" % name for name in state["not_covered"]] or ["Every source in your local map was covered."]
    lines.append("")
    lines.append("Signed-in browsing was not used. Marketplaces that show gigs only to members were searched only "
                 "through what is public.")
    if problems:
        lines += ["", "## Check these"]
        lines += ["- " + problem for problem in problems]
    lines += ["", "Nothing was bid on, applied for, signed up for or sent."]

    # A second sweep on the same day gets its own note. The first one is never overwritten.
    title = "%s Contract sweep" % today.isoformat()
    taken = [row.title for row in store.rows("digest")]
    number = 2
    while title in taken:
        title = "%s Contract sweep %d" % (today.isoformat(), number)
        number += 1
    note_id = merge.next_id(model.DIGEST, store.rows("digest"))
    touched = [store.link("contract", entry["title"]) for entry in state["new"]]
    store.upsert("digest", note_id, title,
                 {"date": today.isoformat(), "track": "Contracts", "new": len(state["new"]),
                  "closed": len(state["closed"]), "touched": touched},
                 section=scoring.scrub_confidential("\n".join(lines), scoring.confidential_names(profile)))
    return title, featured, more, pending


def finish_report(state, profile, title, featured, more, pending, due, problems):
    """The short version for the conversation. No rates and no prices."""
    lines = ["CONTRACT SWEEP DONE", ""]
    lines.append("New gigs that fit: %d. Closed since last time: %d. Reposted: %d." % (
        len(state["new"]), len(state["closed"]), len(state["reposted"])))
    if due:
        lines += ["", "DUE WITHIN 7 DAYS: %d. They lead the digest note." % len(due)]
    if featured:
        lines += ["", "TOP %d" % len(featured)]
        for entry in featured:
            who = entry["client"] or "client not named"
            lines.append("  Win odds %d  %s (%s%s).%s" % (
                entry["fit"], entry["title"], who, ", via %s" % entry["via"] if entry["via"] else "",
                " Someone you know can help." if entry.get("warm") else ""))
    if more:
        lines.append("  %d more %s in the tracker." % (more, "is" if more == 1 else "are"))
    if pending:
        lines += ["", "CHANNELS TO SET UP (%d): %s." % (len(pending), ", ".join(
            "%s (%s)" % (name, status.lower()) for name, status, _ in pending))]
    if state["not_covered"]:
        lines += ["", "Not covered: %s." % ", ".join(state["not_covered"])]
    if problems:
        lines += ["", "TO CHECK (%d): see the end of the digest note." % len(problems)]
    lines += ["", "The full digest is the note \"%s\" in the Digests folder of the tracker, with deadlines first." % title,
              "Nothing was bid on, applied for, signed up for or sent."]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Updates the person asks for, at any time
# --------------------------------------------------------------------------

def listing(store, profile):
    """Open gigs and channels by id, so a request such as "I applied to the brand refresh one" can be matched."""
    lines = ["OPEN GIGS"]
    rows = [row for row in store.rows("contract") if not model.CONTRACT.is_done(row.values.get("status"))]
    for row in sorted(rows, key=lambda r: r.id):
        lines.append("  %s | %s | %s" % (row.id, row.title, row.values.get("status") or "no status"))
    if not rows:
        lines.append("  none")
    lines.append("CHANNELS")
    channels = store.rows("channel")
    for row in sorted(channels, key=lambda r: r.id):
        lines.append("  %s | %s | %s" % (row.id, row.title, row.values.get("status") or "no status"))
    if not channels:
        lines.append("  none")
    lines.append("GIG STATUSES: %s" % ", ".join(model.CONTRACT.status_names()))
    lines.append("CHANNEL STATUSES: %s" % ", ".join(model.CHANNEL.status_names()))
    return _safe("\n".join(lines), profile)


def apply_updates(updates, store, profile, today=None):
    """Record status changes the person asked for. Returns (lines describing what was done, problems)."""
    today = today or datetime.date.today()
    day = today.isoformat()
    names = scoring.confidential_names(profile)
    done, problems = [], []
    for entry in updates if isinstance(updates, list) else []:
        if not isinstance(entry, dict):
            continue
        note_id = str(entry.get("id") or "").strip().upper()
        kind = "channel" if note_id.startswith(model.CHANNEL.id_prefix) else "contract"
        note_type = model.BY_NAME[kind]
        row = store.get(kind, note_id) or store.get(kind, _with_dash(note_id, note_type.id_prefix))
        if row is None:
            problems.append("There is no gig or channel with the id %s." % (entry.get("id") or "(none)"))
            continue
        values, force = {}, []
        if entry.get("status") is not None:
            status = note_type.canonical_status(str(entry["status"]))
            if status is None:
                problems.append("%s is not a status for %s. Use one of: %s." % (
                    entry["status"], row.title, ", ".join(note_type.status_names())))
                continue
            values["status"] = status
            force.append("status")
            if kind == "contract" and status == "Applied" and not row.values.get("applied_on"):
                values["applied_on"] = day
                force.append("applied_on")
        if kind == "contract":
            for key in ("next_step", "next_step_date", "reason"):
                if entry.get(key):
                    values[key] = scoring.scrub_confidential(str(entry[key]), names)
                    force.append(key)
            values["last_touch"] = day
            force.append("last_touch")
            values["updated"] = day
        else:
            if entry.get("profile_url"):
                values["profile_url"] = str(entry["profile_url"])
                force.append("profile_url")
            values["last_checked"] = day
        if not force or force == ["last_touch"]:
            problems.append("Nothing to change was given for %s." % row.title)
            continue
        operation = {"type": kind, "id": row.id, "values": values, "force": force}
        if kind == "contract":
            what = "status set to %s" % values["status"] if "status" in values else "updated"
            note = scoring.scrub_confidential(str(entry.get("note") or "").strip(), names)
            operation["append"] = "%s: %s%s" % (day, what, ". " + note.rstrip(".") + "." if note else ".")
        store.apply([operation])
        done.append("%s: %s." % (row.title, "now %s" % values["status"] if "status" in values else "updated"))
    return done, problems


def _with_dash(note_id, prefix):
    """C12 and C012 mean C-12."""
    rest = note_id[len(prefix):].lstrip("-") if note_id.startswith(prefix) else note_id
    return "%s-%d" % (prefix, int(rest)) if rest.isdigit() else note_id
