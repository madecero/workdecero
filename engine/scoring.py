"""Scoring: how well a posting suits one person, from their profile.

Jobs get a Fit score and contracts get Win odds, both 0 to 100. The points
come from the profile (levels, pay floor, home base, work modes, preferred
industries, exclusions), so the same posting scores differently for
different people.

The one judgement a script cannot make is how well the role matches the
person's background. The research step reads the posting against the resume
and passes that in as `fit_assessment` (0 to 40). Everything else is worked
out here.

Jobs, 100 points:
    fit to background   0 to 40   from the research step
    level               0 to 15   the posting's level against jobs.levels
    industry            0 to 5    a preferred industry
    location            0 to 15   work mode and distance against the profile
    warm path           0 to 15   someone the person knows there
    freshness           0 to 10   how recently it was posted

Contracts, 100 points:
    fit to background   0 to 40
    rate                0 to 15   against the hourly floor and target
    engagement          0 to 10   a way of working the person accepts
    location            0 to 10
    warm path           0 to 15
    freshness           0 to 10

Points come off for a starting pay under the floor, a link that could not be
verified, an exclusion marked "flag", and onsite work outside the person's
area. A posting is dropped, not scored down, when the profile rules it out:
an exclusion marked "skip", the current employer, a title keyword to avoid,
pay or rate that tops out under the floor, an engagement type the person does
not take, or a posting older than the profile allows. A posting with no pay
listed is kept, marked "Pay not listed", and loses nothing.

The weights are engine defaults. They are not profile settings.
Standard library only, Python 3.9+.
"""

import datetime
import re

import wdprofile

JOB_WEIGHTS = {"fit": 40, "level": 15, "industry": 5, "location": 15, "warm": 15, "freshness": 10}
CONTRACT_WEIGHTS = {"fit": 40, "rate": 15, "engagement": 10, "location": 10, "warm": 15, "freshness": 10}
PART_LABELS = {"fit": "background", "level": "level", "industry": "industry", "location": "location",
               "warm": "warm path", "freshness": "freshness", "rate": "rate", "engagement": "engagement"}

PENALTY_PAY_START_BELOW_FLOOR = 6
PENALTY_UNVERIFIED_LINK = 3
PENALTY_FLAGGED = 10
PENALTY_ONSITE_OUTSIDE_AREA = 4      # kept for older notes: such a role is now left out
PENALTY_MISSING_CREDENTIAL = 10
NEAR_MISS_BELOW = 20          # a background fit under this, out of 40, is a near miss
PENALTY_HOURS_OUTSIDE_RANGE = 4

LEVELS = ["entry", "mid", "senior", "lead", "manager", "director", "vp", "executive"]
# Checked in order: the first pattern that matches the title decides the level.
_LEVEL_PATTERNS = [
    (r"\b(chief|c[a-z]o|president|partner|founding)\b", "executive"),
    (r"\b(svp|evp|vice president|vp)\b", "vp"),
    (r"\bhead of\b", "director"),
    (r"\b(senior director|sr director|director)\b", "director"),
    (r"\b(manager|supervisor)\b", "manager"),
    (r"\b(principal|staff|lead)\b", "lead"),
    (r"\b(senior|sr)\b", "senior"),
    (r"\b(junior|jr|entry|associate|intern|trainee)\b", "entry"),
]

# How strong the connection is. The research step names the strongest one it found.
WARM_POINTS = {
    "1st-hiring-team": 15,   # the person knows someone on the hiring team
    "1st": 12,               # knows someone at the company
    "2nd-hiring-team": 10,   # one introduction away from the hiring team
    "2nd": 8,                # one introduction away from someone at the company
    "shared-background": 5,  # same former employer or school as someone there
    "named-contact": 2,      # a recruiter or manager is named, with no connection
}

_JOB_FRESHNESS = ((7, 10), (14, 8), (30, 5), (60, 2))
_CONTRACT_FRESHNESS = ((3, 10), (7, 8), (14, 5), (21, 2))
_UNDATED_POINTS = 3

ENGAGEMENT_WORDS = {
    "independent-1099": ("1099", "independent", "independent contractor", "self employed"),
    "corp-to-corp": ("c2c", "corp to corp", "corp-to-corp", "b2b contract"),
    "w2-contract": ("w2", "w-2"),
    "fixed-price": ("fixed", "sow", "project", "project based", "statement of work", "milestone", "deliverable"),
    "retainer": ("retainer", "monthly", "ongoing", "advisory", "advisor", "advisory board"),
    "fractional": ("fractional", "part time", "part-time", "interim", "advisory", "advisor"),
    "expert-call": ("expert call", "expert network", "consultation", "hourly consult", "paid call"),
    "public-bid": ("rfp", "rfq", "bid", "solicitation", "tender"),
}
# Wording that says "this is contract work" without saying which kind. It fits any kind the person takes
# except a W2 contract, and the note asks them to confirm the terms.
GENERIC_CONTRACT_WORDS = ("contract", "contractor", "freelance", "freelancer", "consulting", "consultant",
                          "consulting engagement", "gig", "temporary", "contract to hire")


class ScoreResult(object):
    def __init__(self, weights):
        self.weights = weights
        self.parts = {}        # points earned per part
        self.penalties = []    # (points, reason)
        self.caveats = []      # sentences for the note
        self.dropped = None    # a reason, when the posting is ruled out
        self.near = None       # a reason, when it is written as a near miss and kept out of the top list

    @property
    def total(self):
        raw = sum(self.parts.values()) - sum(points for points, _ in self.penalties)
        return int(max(0, min(100, round(raw))))

    def breakdown(self):
        """A short readable account, such as 'background 30/40, level 15/15, ... minus 3 (link not verified)'."""
        bits = ["%s %d/%d" % (PART_LABELS[name], self.parts.get(name, 0), self.weights[name]) for name in self.weights]
        text = ", ".join(bits)
        for points, reason in self.penalties:
            text += ", minus %d (%s)" % (points, reason)
        return text


def _text(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _date(value):
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value or "") if isinstance(value, str) else None
    if not match:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _contains_phrase(haystack, needle):
    needle = _text(needle)
    return bool(needle) and re.search(r"(?:^| )%s(?: |$)" % re.escape(needle), _text(haystack)) is not None


# --------------------------------------------------------------------------
# Confidential names and exclusions
# --------------------------------------------------------------------------

def confidential_names(profile):
    """Every name that must never be written into a note, digest or draft."""
    return wdprofile.confidential_names(profile)


def scrub_confidential(value, names, replacement="[confidential]"):
    """Remove confidential names from text a sweep is about to write. Lists are scrubbed item by item."""
    if isinstance(value, list):
        return [scrub_confidential(item, names, replacement) for item in value]
    if not isinstance(value, str):
        return value
    for name in sorted(names, key=len, reverse=True):
        if name.strip():
            value = re.sub(re.escape(name.strip()), replacement, value, flags=re.IGNORECASE)
    return value


def active_exclusions(profile, today):
    """The profile's exclusions that still apply, plus the current employer, which is always skipped."""
    items = []
    for item in wdprofile.effective_exclusions(profile):
        until = _date(item.get("until"))
        if until is not None and until < today:
            continue   # the exclusion has run out
        items.append(item)
    return items


def _same_company(a, b):
    import merge  # local import: merge imports this module
    left, right = merge.normalize_company(a), merge.normalize_company(b)
    return bool(left) and left == right


def match_exclusion(item, owner, exclusions):
    """The first exclusion a posting hits, or None. `owner` is the employer or client name."""
    title = item.get("title") or ""
    for exclusion in exclusions:
        name, kind = exclusion.get("name", ""), exclusion.get("kind")
        if kind == "employer" and _same_company(owner, name):
            return exclusion
        if kind == "industry" and (_contains_phrase(item.get("industry"), name) or
                                   _text(item.get("industry")) and _text(item.get("industry")) in _text(name)):
            return exclusion
        if kind == "keyword" and (_contains_phrase(title, name) or _contains_phrase(item.get("description"), name)):
            return exclusion
    return None


# --------------------------------------------------------------------------
# Parts shared by jobs and contracts
# --------------------------------------------------------------------------

def level_of(item):
    """The posting's level: the one given, or one read from the title."""
    given = _text(item.get("level"))
    if given in LEVELS:
        return given
    title = " " + _text(item.get("title")) + " "
    for pattern, level in _LEVEL_PATTERNS:
        if re.search(pattern, title):
            return level
    return None


def _level_points(item, wanted, full):
    if not wanted:
        return int(round(full * 2 / 3.0))     # the profile names no levels, so no level is wrong
    level = level_of(item)
    if level is None:
        return int(round(full / 2.0))
    if level in wanted:
        return full
    distance = min(abs(LEVELS.index(level) - LEVELS.index(other)) for other in wanted if other in LEVELS)
    return int(round(full / 2.0)) if distance == 1 else 0


def _in_area(item, location):
    """True, False or None (cannot tell) for whether the posting's place is within reach."""
    place = _text(item.get("location"))
    # A place the person named as also in reach is in reach, however many miles it is.
    also = [_text(area.split(",")[0]) for area in location.get("other_areas") or []]
    if place and any(name and _contains_phrase(place, name) for name in also):
        return True
    miles = _number(item.get("distance_miles"))
    radius = _number(location.get("radius_miles"))
    if miles is not None and radius is not None:
        return miles <= radius
    if not place:
        return None
    town = _text((location.get("home_base") or "").split(",")[0])
    if any(name and _contains_phrase(place, name) for name in (town, _text(location.get("metro")))):
        return True
    return False


def _location_points(item, profile, full, result):
    location = profile.get("location") or {}
    modes = location.get("work_modes") or []
    mode = _text(item.get("work_mode"))
    if mode == "remote":
        if "remote" in modes:
            return full
        result.caveats.append("Remote role, and your profile lists only %s" % " and ".join(modes))
        return int(round(full / 3.0))
    in_area = _in_area(item, location)
    if mode in ("hybrid", "onsite", "on site"):
        mode = "onsite" if mode != "hybrid" else "hybrid"
        accepted = mode in modes
        if in_area and accepted:
            return full - 1 if mode == "hybrid" else int(round(full * 0.8))
        if in_area:
            result.caveats.append("%s role near you, and your profile does not list %s work"
                                  % (mode.capitalize(), mode))
            return int(round(full / 3.0))
        if in_area is None:
            result.caveats.append("Location not clear from the posting")
            return int(round(full / 3.0))
        targets = [_text(place.split(",")[0]) for place in location.get("relocation_targets") or []]
        if location.get("open_to_relocation") and any(name and _contains_phrase(item.get("location"), name)
                                                      for name in targets):
            result.caveats.append("Would mean relocating")
            return int(round(full / 2.0))
        # A job that needs them in a place they cannot reach is not a fit, whatever else it offers.
        result.dropped = "it is %s work in a place outside your area" % mode
        return 0
    result.caveats.append("Work mode not stated")
    return int(round(full / 3.0)) if in_area is not False else 0


def _warm_points(item, full, result):
    warm = item.get("warm")
    if not warm:
        return 0
    points = WARM_POINTS.get(_text(warm).replace(" ", "-"), 0)
    return int(round(points * full / 15.0))


def _freshness_points(item, today, bands, full, max_age, result):
    posted = _date(item.get("posted"))
    if posted is None:
        return int(round(_UNDATED_POINTS * full / 10.0))
    age = (today - posted).days
    if max_age and age > max_age:
        if item.get("live_list") is True:
            # Read a moment ago from the employer's own job list, so it is open whatever its age.
            result.caveats.append("Posted %d days ago, still open on the employer's site" % age)
            return 0
        result.dropped = "posted %d days ago, older than the %d days your profile allows" % (age, max_age)
        return 0
    for limit, points in bands:
        if age <= limit:
            return int(round(points * full / 10.0))
    return 0


def _fit_points(item, full, result):
    assessed = _number(item.get("fit_assessment"))
    if assessed is None:
        result.caveats.append("Fit to your background was not assessed")
        return int(round(full / 2.0))
    return int(max(0, min(full, round(assessed))))


def missing_requirements(item):
    """Must-haves a posting states that the person lacks: a language, a licence, a clearance, a degree, a domain."""
    given = item.get("missing_requirements")
    found = list(given) if isinstance(given, (list, tuple)) else ([given] if given else [])
    if item.get("missing_credential"):
        found.append(item["missing_credential"])
    clean = []
    for entry in found:
        text = str(entry).strip().rstrip(".")
        if text and text.lower() not in [other.lower() for other in clean]:
            clean.append(text)
    return clean


def _common_checks(item, owner, profile, today, result):
    """Exclusions, confidential names, link check and deal breakers. Sets result.dropped when ruled out."""
    if any(_same_company(owner, name) for name in confidential_names(profile)):
        # The reason must not repeat the name.
        result.dropped = "it is at an employer or client your profile keeps confidential"
        return
    # A note about a posting that names a confidential employer in its title would have to carry the name.
    if any(_contains_phrase(_text(item.get(key)), _text(name))
           for name in confidential_names(profile) for key in ("title", "via") if item.get(key)):
        result.dropped = "its title names an employer or client your profile keeps confidential"
        return
    exclusion = match_exclusion(item, owner, active_exclusions(profile, today))
    if exclusion is not None:
        reason = exclusion.get("reason") or "listed in your exclusions"
        if exclusion.get("action", "skip") == "skip":
            result.dropped = "excluded by your profile (%s: %s)" % (exclusion.get("kind"), reason)
            return
        result.penalties.append((PENALTY_FLAGGED, "flagged in your profile"))
        result.caveats.append("Check first: %s" % reason)
    if item.get("deal_breaker"):
        result.dropped = "hits a deal breaker in your profile (%s)" % item["deal_breaker"]
        return
    missing = missing_requirements(item)
    if missing:
        # A must-have the person lacks: the role is kept, marked down, and never ranked as a fit.
        result.penalties.append((PENALTY_MISSING_CREDENTIAL, "requires something you do not have"))
        for needed in missing:
            result.caveats.append("Requires %s, which you do not have" % needed)
        result.near = "requires %s" % " and ".join(missing)
    if item.get("link_verified") is False:
        result.penalties.append((PENALTY_UNVERIFIED_LINK, "link not verified"))
        result.caveats.append("The link could not be verified. Confirm the posting is real before applying")


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

def score_job(item, profile, today=None):
    today = today or datetime.date.today()
    result = ScoreResult(JOB_WEIGHTS)
    jobs = profile.get("jobs") or {}

    for keyword in jobs.get("exclude_title_keywords") or []:
        if _contains_phrase(item.get("title"), keyword):
            result.dropped = "the title contains '%s', which your profile leaves out" % keyword
            return result
    _common_checks(item, item.get("company"), profile, today, result)
    if result.dropped:
        return result

    floor = _number(jobs.get("salary_floor"))
    pay_min, pay_max = _number(item.get("pay_min")), _number(item.get("pay_max"))
    if pay_min is None and pay_max is None:
        result.caveats.append("Pay not listed")
    elif floor:
        if pay_max is not None and pay_max < floor:
            result.dropped = "pay tops out at %d, under your floor of %d" % (pay_max, floor)
            return result
        if pay_max is None and pay_min is not None and pay_min < floor:
            result.penalties.append((PENALTY_PAY_START_BELOW_FLOOR, "starting pay under your floor"))
            result.caveats.append("Only a starting pay of %d is listed, under your floor of %d" % (pay_min, floor))

    result.parts["fit"] = _fit_points(item, JOB_WEIGHTS["fit"], result)
    assessed = _number(item.get("fit_assessment"))
    if result.near is None and assessed is not None and assessed < NEAR_MISS_BELOW:
        result.near = "the background fit is %d of %d, under the %d that counts as a fit" % (
            round(assessed), JOB_WEIGHTS["fit"], NEAR_MISS_BELOW)
    result.parts["level"] = _level_points(item, jobs.get("levels") or [], JOB_WEIGHTS["level"])
    preferred = jobs.get("preferred_industries") or []
    industry = item.get("industry")
    hit = any(_contains_phrase(industry, name) or (_text(industry) and _contains_phrase(name, industry))
              for name in preferred)
    result.parts["industry"] = JOB_WEIGHTS["industry"] if hit else 0
    result.parts["location"] = _location_points(item, profile, JOB_WEIGHTS["location"], result)
    result.parts["warm"] = _warm_points(item, JOB_WEIGHTS["warm"], result)
    result.parts["freshness"] = _freshness_points(item, today, _JOB_FRESHNESS, JOB_WEIGHTS["freshness"],
                                                  _number(jobs.get("max_posting_age_days")), result)
    return result


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------

def engagement_type(item):
    """The profile's name for how a gig is engaged, read from the posting's wording. None when unclear."""
    wording = " " + _text(item.get("engagement")) + " "
    given = (item.get("engagement") or "").strip().lower()
    if given in ENGAGEMENT_WORDS:
        return [given]
    found = [name for name, words in ENGAGEMENT_WORDS.items()
             if any(" %s " % _text(word) in wording for word in words)]
    if not found and any(" %s " % _text(word) in wording for word in GENERIC_CONTRACT_WORDS):
        return ["generic"]
    return found or None


def score_contract(item, profile, today=None):
    today = today or datetime.date.today()
    result = ScoreResult(CONTRACT_WEIGHTS)
    contracts = profile.get("contracts") or {}

    _common_checks(item, item.get("client"), profile, today, result)
    if result.dropped:
        return result
    # Research reports every gig it read. Whether one is too late is decided here, so the person sees why.
    if item.get("closed") is True:
        result.dropped = "the listing says it is closed or filled"
        return result
    deadline = _date(item.get("closes"))
    if deadline is not None and deadline < today:
        result.dropped = "its deadline passed on %s" % deadline.isoformat()
        return result

    accepted = contracts.get("engagement_types") or []
    kinds = engagement_type(item)
    if kinds == ["generic"] and [kind for kind in accepted if kind != "w2-contract"]:
        # "Contract" or "consulting" with no more said: it fits what the person takes. They confirm the kind.
        result.parts["engagement"] = int(round(CONTRACT_WEIGHTS["engagement"] * 0.7))
        result.caveats.append("The listing says contract work without saying which kind. Confirm the terms")
    elif kinds is None or kinds == ["generic"]:
        result.parts["engagement"] = CONTRACT_WEIGHTS["engagement"] // 2
        result.caveats.append("How the work is engaged is not stated. Confirm the terms before you spend time on it")
    else:
        usable = [kind for kind in kinds if kind in accepted]
        if not usable:
            result.dropped = "it is offered as %s, which your profile does not take" % " or ".join(kinds)
            return result
        if usable == ["corp-to-corp"] and not contracts.get("has_business_entity"):
            result.dropped = "it is corp-to-corp only, and your profile has no business entity"
            return result
        result.parts["engagement"] = CONTRACT_WEIGHTS["engagement"]

    floor, target = _number(contracts.get("rate_floor_hourly")), _number(contracts.get("rate_target_hourly"))
    rate_min, rate_max = _number(item.get("rate_min")), _number(item.get("rate_max"))
    top = rate_max if rate_max is not None else rate_min
    if top is None:
        fixed, fixed_min = _number(item.get("fixed_price")), _number(contracts.get("fixed_price_min"))
        if fixed is not None and fixed_min and fixed < fixed_min:
            result.dropped = "the fixed price of %d is under your minimum of %d" % (fixed, fixed_min)
            return result
        monthly, monthly_min = _number(item.get("retainer_monthly")), _number(contracts.get("retainer_monthly_min"))
        if monthly is not None and monthly_min and monthly < monthly_min:
            result.dropped = "the retainer of %d a month is under your minimum of %d" % (monthly, monthly_min)
            return result
        if fixed is not None or monthly is not None:
            # A stated price that clears the person's minimum counts as a rate that clears the floor.
            result.parts["rate"] = int(round(CONTRACT_WEIGHTS["rate"] * 2 / 3.0))
        else:
            result.parts["rate"] = int(round(CONTRACT_WEIGHTS["rate"] * 0.4))
            result.caveats.append("Rate not listed")
    elif floor and top < floor:
        result.dropped = "the rate tops out at %s an hour, under your floor of %s" % (top, floor)
        return result
    elif target and top >= target:
        result.parts["rate"] = CONTRACT_WEIGHTS["rate"]
    else:
        result.parts["rate"] = int(round(CONTRACT_WEIGHTS["rate"] * 2 / 3.0))

    hours = _number(item.get("hours_per_week"))
    low, high = _number(contracts.get("hours_per_week_min")), _number(contracts.get("hours_per_week_max"))
    if hours is not None and ((high is not None and hours > high) or (low is not None and hours < low)):
        result.penalties.append((PENALTY_HOURS_OUTSIDE_RANGE, "hours outside your range"))
        result.caveats.append("Asks for %s hours a week, outside the range in your profile" % hours)
    weeks, shortest = _number(item.get("length_weeks")), _number(contracts.get("min_duration_weeks"))
    if weeks is not None and shortest and weeks < shortest:
        result.caveats.append("Runs %s weeks, shorter than the %s you asked for" % (weeks, shortest))

    result.parts["fit"] = _fit_points(item, CONTRACT_WEIGHTS["fit"], result)
    assessed = _number(item.get("fit_assessment"))
    if result.near is None and assessed is not None and assessed < NEAR_MISS_BELOW:
        result.near = "the background fit is %d of %d, under the %d that counts as a fit" % (
            round(assessed), CONTRACT_WEIGHTS["fit"], NEAR_MISS_BELOW)
    located = dict(item)
    if item.get("remote") is True and not item.get("work_mode"):
        located["work_mode"] = "remote"
    result.parts["location"] = _location_points(located, profile, CONTRACT_WEIGHTS["location"], result)
    result.parts["warm"] = _warm_points(item, CONTRACT_WEIGHTS["warm"], result)
    result.parts["freshness"] = _freshness_points(item, today, _CONTRACT_FRESHNESS, CONTRACT_WEIGHTS["freshness"],
                                                  _number(contracts.get("max_posting_age_days")), result)
    return result


def score(kind, item, profile, today=None):
    if kind == "job":
        return score_job(item, profile, today)
    if kind == "contract":
        return score_contract(item, profile, today)
    raise ValueError("scoring covers jobs and contracts, not %r" % kind)
