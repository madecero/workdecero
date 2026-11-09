# engine

Shared logic for the sweeps. Standard library Python only (3.9 or newer), so it runs with no installs.

| File | What it does |
| --- | --- |
| `miniyaml.py` | Reads and writes the small part of YAML that profiles and Obsidian note properties use. Refuses everything else with a clear error. |
| `schema.py` | Checks a value against a JSON Schema and words every problem for a non-technical reader. |
| `model.py` | The tracker note types, their properties and their status lists, defined once. |
| `profile.schema.json` | The profile format: every setting, with a description, an example and a default. |
| `wdprofile.py` | Loads a profile, fills in defaults, runs the checks. `python3 engine/wdprofile.py validate [PATH]`. |

Still to come: liveness checks, merge and dedupe, scoring, warm paths.
