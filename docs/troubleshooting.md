# Troubleshooting

[Back to the README](../README.md)

The things that came up most in testing.

**"Unknown command" after installing.** Start a new Claude Code session. A plugin installed in the middle of a session is not loaded until the next one.

**The install command fails with a "not found" message.** Check the spelling: `/plugin marketplace add madecero/workdecero`, then `/plugin install workdecero@workdecero`. If it still fails, check that this computer can reach github.com.

**A window about "Command Line Developer Tools" appears, or Claude Code shows a hook error on every message.** python3 is missing. That window is macOS offering to install it. Click Install and wait for it to finish. In Terminal, `python3 --version` should then print a version. Start a new Claude Code session.

**The tables in Start here show as code, or are blank.** Update Obsidian to 1.9 or newer, and check that Bases is on under Settings, Core plugins.

**I said "run the job sweep" and Claude Code asked for permission.** That is expected when you start by plain words. Say yes, or use the slash command, which does not ask.

**Setup says another setup is in progress.** A setup was left open in another window, or was cut off. You are offered three choices: leave it, pick it up here with every answer kept, or discard it. Nothing was saved by the unfinished one.

**A sweep found little or nothing.** Three things to check. First, run `/workdecero:sources` and say "refresh my sources": it names any job board in your map that a sweep gets nothing from. Second, look at "Left out, and why" in the digest: if good roles are being left out, your profile is stricter than you meant, and `/workdecero:setup` changes it. Third, look at "Not covered": some sites can only be read with the browser connection turned on.

**A second sweep in the same conversation asks to allow web search.** Expected. Allow it, or start the sweep with its slash command.

**Something else.** Say "what went wrong?" in the same conversation and Claude will read the helper's message back in plain words. Your notes are never left half written: each note is written whole or not at all.
