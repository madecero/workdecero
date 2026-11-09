"""The tracker model: note types, their properties and their status lists.

This is the one place that says what a job, contract, channel, event, group,
person, company or digest note looks like. The vault store, the Bases views
and the sweep engine all read it, so a status or a property is never spelled
in two places.

Property keys in a note are the names here with PREFIX in front (wd_status,
wd_fit, ...). The prefix keeps them apart from properties the user already
has, such as their own status, type, tags or company.

Who may write a property:
    SWEEP  rewritten by every sweep that sees the item
    SEED   written once when the note is created, then the user's to change
    USER   never written by a sweep. Created empty when `seed_empty` is set,
           so the row is ready to fill in from Obsidian.

Standard library only, Python 3.9+.
"""

PREFIX = "wd_"

SWEEP = "sweep"
SEED = "seed"
USER = "user"

# One block per note that sweeps may rewrite. Everything outside it is the user's.
SECTION_START = "<!-- workdecero:start -->"
SECTION_END = "<!-- workdecero:end -->"

# Group label for a status nobody recognises (a typo made in a table cell). It sorts first, so it is seen.
CHECK_STATUS_STAGE = "0 Check status"

TEXT, NUMBER, DATE, CHECKBOX, LINK, LINKS, URL = "text", "number", "date", "checkbox", "link", "links", "url"


class Field(object):
    def __init__(self, name, kind, display, owner=SWEEP, seed_empty=False, default=None):
        self.name = name
        self.kind = kind
        self.display = display
        self.owner = owner
        self.seed_empty = seed_empty
        self.default = default

    @property
    def key(self):
        return PREFIX + self.name


class Status(object):
    """`stage` is the label a status is grouped under, numbered so groups sort in pipeline order."""

    def __init__(self, name, stage, kind, engaged=False):
        self.name = name
        self.stage = stage
        self.kind = kind        # "new", "active" or "done"
        self.engaged = engaged  # the user has applied or is further along


class NoteType(object):
    def __init__(self, name, folder, id_prefix, title_display, fields, statuses=()):
        self.name = name
        self.folder = folder
        self.id_prefix = id_prefix
        self.title_display = title_display
        self.fields = [Field("id", TEXT, "ID"), Field("type", TEXT, "Type")] + list(fields)
        self.statuses = list(statuses)
        self._by_name = dict((field.name, field) for field in self.fields)

    def field(self, name):
        return self._by_name[name]

    def has_field(self, name):
        return name in self._by_name

    def status_names(self, kind=None):
        return [status.name for status in self.statuses if kind is None or status.kind == kind]

    def stage_of(self, status_name):
        for status in self.statuses:
            if status.name == status_name:
                return status.stage
        return self.statuses[-1].stage if self.statuses else None

    def canonical_status(self, text):
        """The status name a hand-typed value means ("applied " is Applied), or None if it matches nothing."""
        wanted = text.strip().lower() if isinstance(text, str) else ""
        for status in self.statuses:
            if status.name.lower() == wanted:
                return status.name
        return None

    def is_done(self, text):
        """True only for a recognised finished status. A typo is never treated as finished."""
        name = self.canonical_status(text)
        return name is not None and name in self.status_names("done")

    def engaged_statuses(self):
        """Statuses where the user is already in the process. A sweep never changes these."""
        return [status.name for status in self.statuses if status.engaged]

    def is_engaged(self, text):
        return self.canonical_status(text) in self.engaged_statuses()

    def default_status(self):
        return self.statuses[0].name if self.statuses else None


def _pursuit_fields():
    """Properties the user fills in while chasing a job or a contract."""
    return [
        Field("applied_on", DATE, "Applied on", USER, seed_empty=True),
        Field("next_step", TEXT, "Next step", USER, seed_empty=True),
        Field("next_step_date", DATE, "Next step date", USER, seed_empty=True),
        Field("last_touch", DATE, "Last touch", USER),
        Field("contact", LINK, "Contact", USER),
        Field("reason", TEXT, "Reason", SEED),
        Field("liveness", TEXT, "Posting check"),
        Field("last_checked", DATE, "Last checked"),
    ]


JOB = NoteType(
    "job", "Jobs", "J", "Role",
    [
        Field("status", TEXT, "Status", SEED),
        Field("role", TEXT, "Role title"),
        Field("company", LINK, "Company"),
        Field("fit", NUMBER, "Fit"),
        Field("pay_min", NUMBER, "Pay from"),
        Field("pay_max", NUMBER, "Pay to"),
        Field("work_mode", TEXT, "Work mode"),
        Field("distance_miles", NUMBER, "Miles from home"),
        Field("location", TEXT, "Location"),
        Field("posted", DATE, "Posted"),
        Field("first_seen", DATE, "First seen", SEED),
        Field("last_seen", DATE, "Last seen"),
        Field("closes", DATE, "Closes"),
        Field("apply_url", URL, "Apply link"),
        Field("warm_path", LINKS, "Warm path"),
        Field("source", TEXT, "Found on"),
        Field("req_id", TEXT, "Posting ID"),
        Field("updated", DATE, "Last updated"),
    ] + _pursuit_fields(),
    [
        Status("New", "1 New", "new"),
        Status("Interested", "2 Interested", "active"),
        Status("Applied", "3 Applied", "active", engaged=True),
        Status("Interviewing", "4 Interviewing", "active", engaged=True),
        Status("Offer", "5 Offer", "active", engaged=True),
        Status("Passed", "6 Closed", "done"),
        Status("Rejected", "6 Closed", "done"),
        Status("Closed", "6 Closed", "done"),
    ],
)

CONTRACT = NoteType(
    "contract", "Contracts", "C", "Gig",
    [
        Field("status", TEXT, "Status", SEED),
        Field("client", TEXT, "Client or platform"),
        Field("via", TEXT, "Via"),
        Field("fit", NUMBER, "Fit"),
        Field("rate_min", NUMBER, "Rate from ($/hr)"),
        Field("rate_max", NUMBER, "Rate ($/hr)"),
        Field("hours_per_week", NUMBER, "Hours per week"),
        Field("length_weeks", NUMBER, "Length (weeks)"),
        Field("remote", CHECKBOX, "Remote"),
        Field("engagement", TEXT, "Engagement"),
        Field("link", URL, "Link"),
        Field("posted", DATE, "Posted"),
        Field("first_seen", DATE, "First seen", SEED),
        Field("last_seen", DATE, "Last seen"),
        Field("closes", DATE, "Closes"),
        Field("updated", DATE, "Last updated"),
    ] + _pursuit_fields(),
    [
        Status("New", "1 New", "new"),
        Status("Interested", "2 Interested", "active"),
        Status("Applied", "3 Applied", "active", engaged=True),
        Status("Interviewing", "4 Interviewing", "active", engaged=True),
        Status("Negotiating", "5 Negotiating", "active", engaged=True),
        Status("Won", "6 Won", "done"),
        Status("Lost", "7 Closed", "done"),
        Status("Passed", "7 Closed", "done"),
        Status("Closed", "7 Closed", "done"),
    ],
)

CHANNEL = NoteType(
    "channel", "Channels", "H", "Platform",
    [
        Field("status", TEXT, "Status", SEED),
        Field("value", NUMBER, "Value"),
        Field("why", TEXT, "Why it fits"),
        Field("profile_url", URL, "Profile link", SEED),
        Field("last_checked", DATE, "Last checked"),
    ],
    [
        Status("Not started", "1 Not started", "new"),
        Status("In progress", "2 In progress", "active"),
        Status("Live", "3 Live", "active"),
        Status("Needs refresh", "4 Needs refresh", "active"),
        Status("Skip", "5 Skip", "done"),
    ],
)

EVENT = NoteType(
    "event", "Events", "E", "Event",
    [
        Field("date", DATE, "Date"),
        Field("time", TEXT, "Time"),
        Field("evening", CHECKBOX, "Evening"),
        Field("virtual", CHECKBOX, "Virtual"),
        Field("where", TEXT, "Where"),
        Field("cost", NUMBER, "Cost"),
        Field("group", LINK, "Group"),
        Field("link", URL, "Link"),
        Field("fit", NUMBER, "Fit"),
        Field("first_seen", DATE, "First seen", SEED),
        Field("going", CHECKBOX, "Going?", USER, seed_empty=True, default=False),
        Field("attended", CHECKBOX, "Attended", USER, seed_empty=True, default=False),
        Field("people_met", LINKS, "People met", USER),
        Field("follow_up_by", DATE, "Follow up by", USER),
        Field("prep", LINK, "Prep note", USER),
    ],
)

GROUP = NoteType(
    "group", "Groups", "G", "Group",
    [
        Field("status", TEXT, "Status", SEED),
        Field("focus", TEXT, "Focus"),
        Field("cadence", TEXT, "Cadence"),
        Field("next_meeting", DATE, "Next meeting"),
        Field("virtual", CHECKBOX, "Virtual?"),
        Field("cost_per_year", NUMBER, "Cost per year"),
        Field("link", URL, "Link"),
        Field("fit", NUMBER, "Fit"),
        Field("first_seen", DATE, "First seen", SEED),
        Field("last_attended", DATE, "Last attended", USER),
    ],
    [
        Status("New", "1 New", "new"),
        Status("Trying", "2 Trying", "active"),
        Status("Joined", "3 Joined", "active"),
        Status("Passed", "4 Passed", "done"),
        Status("Inactive", "4 Passed", "done"),
    ],
)

PERSON = NoteType(
    "person", "People", "P", "Name",
    [
        Field("company", LINK, "Company"),
        Field("role", TEXT, "Role"),
        Field("relationship", TEXT, "Relationship"),
        Field("linked_roles", LINKS, "Linked roles"),
        Field("source_url", URL, "Source"),
        Field("confidence", NUMBER, "Confidence"),
        Field("why", TEXT, "Why"),
        Field("last_contact", DATE, "Last contact", USER),
        Field("follow_up_by", DATE, "Follow up by", USER),
    ],
)

COMPANY = NoteType(
    "company", "Companies", "O", "Company",
    [
        Field("work_policy", TEXT, "Work policy"),
        Field("hiring_system", TEXT, "Hiring system"),
        Field("site", URL, "Website"),
        Field("open_roles", NUMBER, "Open roles"),
        Field("people_known", NUMBER, "People known"),
    ],
)

DIGEST = NoteType(
    "digest", "Digests", "D", "Digest",
    [
        Field("date", DATE, "Date"),
        Field("track", TEXT, "Track"),
        Field("new", NUMBER, "New"),
        Field("closed", NUMBER, "Closed"),
        Field("touched", LINKS, "Notes touched"),
    ],
)

TYPES = [JOB, CONTRACT, CHANNEL, EVENT, GROUP, PERSON, COMPANY, DIGEST]
BY_NAME = dict((note_type.name, note_type) for note_type in TYPES)

RELATIONSHIPS = ["1st", "2nd", "None"]
WORK_MODES = ["Remote", "Hybrid", "Onsite"]
