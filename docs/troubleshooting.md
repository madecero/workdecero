# Troubleshooting

[Back to the README](../README.md)

The things that came up most in testing.

**"Unknown command" after installing.** Start a new Claude Code session. A plugin installed in the middle of a session is not loaded until the next one.

**The install command fails with an access or "not found" message.** While the repository is private, your computer must be signed in to GitHub with an account that has been given access. See the note under [Install](../README.md#install).

**A window about "Command Line Developer Tools" appears.** That is macOS offering to install python3. Click Install, wait for it to finish, and run the command again.

**I said "run the job sweep" and Claude Code asked for permission.** That is expected when you start by plain words. Say yes, or use the slash command, which does not ask.

**Setup says another setup is in progress.** A setup was left open in another window, or was cut off. You are offered three choices: leave it, pick it up here with every answer kept, or discard it. Nothing was saved by the unfinished one.

**A sweep found little or nothing.** Three things to check. First, run `/workdecero:sources` and say "refresh my sources": it names any job board in your map that a sweep gets nothing from. Second, look at "Left out, and why" in the digest: if good roles are being left out, your profile is stricter than you meant, and `/workdecero:setup` changes it. Third, look at "Not covered": some sites can only be read with the browser connection turned on.

**A second sweep in the same conversation asks to allow web search.** Expected. Allow it, or start the sweep with its slash command.

**Something else.** Say "what went wrong?" in the same conversation and Claude will read the helper's message back in plain words. Your notes are never left half written: each note is written whole or not at all.
