# Optional connections

[Back to the README](../README.md)

All off until you turn them on with `/workdecero:setup`. Each one is skipped cleanly, with a line in the digest, when its tool is not available in a session.

| Connection | What it does | Rules it keeps |
| --- | --- | --- |
| Browser pass (Claude in Chrome) | Reads job boards and sites that a script cannot | Its own tab, closed at the end. Paced like a person reading. Read only: never a form, never apply, save or follow. A CAPTCHA is never attempted: the site is named under "Not covered" |
| Signed-in LinkedIn | Finds who you already know at an employer, and the hiring team on a posting | Needs two switches: the connection, and your acknowledgement of the risk. **Automated activity can get a LinkedIn account restricted or closed.** workdecero paces itself and keeps to a small daily number of pages, and cannot remove that risk. Read only. In a quiet search it never opens a profile, because a profile view can be shown to its owner |
| Calendar (Outlook or Google) | Adds a networking event when you say you are going | Checks for a clash first and tells you. One event, no attendees. The entry holds the link and why to go |
| Task tracker (Linear) | Posts a short comment after each sweep on an issue you name | Counts only. Never a title, an employer or a name |
| Spreadsheet snapshot | Rebuilds `workdecero snapshot.xlsx` in your tracker folder after each sweep, with one sheet per tracker | A picture to glance at, never a second copy to keep up. Anything typed into it is lost at the next sweep |

**No standing permission for these.** The approval described in [What workdecero runs without asking](permissions.md) covers only workdecero's own helper commands. Browser, calendar and tracker tools are asked for by Claude Code each time, like any other tool. If you want fewer prompts, allow them yourself in your own Claude Code settings.

**About LinkedIn, plainly.** You do not need it. Without it, workdecero uses only what is public. If you export your connections from LinkedIn yourself (Settings, Data privacy, Get a copy of your data, Connections), setup can read that file so sweeps know who you know, with no signed-in browsing at all. That is the safe way, and it is the one I recommend.

## Cost and limits

workdecero itself is free. What it uses is your Claude Code plan.

| | Lite | Full |
| --- | --- | --- |
| Meant for | A Pro plan | A Max plan |
| Building your local map | Up to 12 web searches | Up to 40 |
| Refreshing your local map | Up to 4 | Up to 12 |
| A job sweep | Up to 10 web searches, up to 12 postings judged, contact research for the best 3 | Up to 30, 30 and 8 |
| A contract or networking sweep | Up to 10 web searches | Up to 30 |

**Typical times in Lite**, from my own test runs: setup 5 to 10 minutes, the local map about 2 to 3 minutes, each sweep about 2 minutes. Full takes longer.

Reading a page that is already known (an employer's job list, a board in your map) does not count as a search, so much of a sweep costs no searches at all.

**Plan limits.** Every Claude plan has usage limits, and a sweep counts against yours like any other work in Claude Code. If you hit a limit in the middle of a sweep, nothing is lost: notes already written stay, and you can run the sweep again later. If Lite sweeps strain your plan, run fewer of them or run them on different days.
