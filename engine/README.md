# engine

Shared logic for the sweeps. Standard library Python only (3.9 or newer), so it runs with no installs.

| File | What it does |
| --- | --- |
| `miniyaml.py` | Reads and writes the small part of YAML that profiles and Obsidian note properties use. Refuses everything else with a clear error. |
| `schema.py` | Checks a value against a JSON Schema and words every problem for a non-technical reader. |
| `model.py` | The tracker note types, their properties and their status lists, defined once. |
| `profile.schema.json` | The profile format: every setting, with a description, an example and a default. |
| `wdprofile.py` | Loads a profile, fills in defaults, runs the checks. `python3 engine/wdprofile.py validate [PATH]`. |

| `liveness.py` | Checks whether a posting is still open. `python3 engine/liveness.py check URL`. |

| `merge.py` | Decides which finds are the same posting, merges them, matches them against the vault and plans the writes. |
| `scoring.py` | Fit for jobs and Win odds for contracts, 0 to 100, from the profile. Applies exclusions. |

| `sources.py` | The local source map: checks each proposed source with a live call, removes duplicates, re-checks old entries, and keeps `sources.yaml`. |
| `handoff.py` | The private temporary folder the helpers share with the assistant, and whether a skill has a run open. Commands that change saved data only work during a run. |
| `harvest.py` | Reads the job lists of employers in the map and keeps the postings whose titles match the profile. |
| `warmpath.py` | Writes the Warm path part of a job note, rates email confidence from published evidence, links People notes, and imports LinkedIn's connections export. |

## Liveness rules

Every answer is `live`, `closed` or `unknown`, with the evidence it rests on.

| Source | How it is checked |
| --- | --- |
| Workday | The site's own job API. Closed when applications are off, the posting is not found, or it was unpublished (HTTP 403 with code S22). |
| Greenhouse, Lever | The public job board API. A 404 means removed. |
| Ashby | The public job board list. Closed when the posting is no longer on it. |
| LinkedIn | The public guest view of the posting. Search links are never checked. |
| SAM.gov | The opportunity record. Closed once the response deadline has passed. |
| Phenom career sites | These print "has been filled" on every page. Only the embedded Workday apply link or the posting's own status field is trusted. |
| TEKsystems | The page template always carries closed wording, so only the status code counts: 410 is gone, 200 is live. |
| Sites that need a real browser | Reported as unknown, for the browser pass. |
| Any other page | 404 or 410 closes. Closed wording or a redirect to a list page is only a weak sign. |

**A posting is never closed on one weak sign.** Two weak signs together close it, or the same weak sign at two checks on different days. Until then the note is marked `suspect` and a line asks you to look.

When a posting closes, a note you have not applied to moves to Closed with the evidence. A note at Applied or later keeps your status and gets a `POSTING CLOSED` line instead.

Requests are spaced one second apart per site, retried with a growing wait on 429 and 503, time out after 20 seconds, and carry a plain User-Agent with no personal details. The checker plans updates and the vault store applies them. It never writes to the vault itself.
