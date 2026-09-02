# Changelog

User-facing changes worth knowing about before pulling an update —
especially breaking ones. This project doesn't use version numbers or
tags, so entries are dated instead. For the full day-to-day development
history (bugs found/fixed, investigations, design decisions), see
`notes.md`; this file is just the subset that could actually affect
someone else running this repo.

## 2026-09-02

### Breaking

- **`music-stack sync` is now `music-stack fetch`.** Same command, same
  flags, same behavior — only the subcommand name changed, to stop it
  reading as the same operation as `sync-orchestrator sync`/`full-sync`
  (which write to a connected iPod; `music-stack ...` never does — it
  only fetches from streaming sources into the local library). Any saved
  invocation (a script, a cron entry, a scheduled task) using
  `music-stack sync ...` will fail with an unrecognized-subcommand error
  until updated to `music-stack fetch ...`. No alias was added.
  `sync-orchestrator sync`/`full-sync`/`auto-sync` are unaffected.
