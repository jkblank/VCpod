# podcast-manager

[Pocket Casts](https://pocketcasts.com) client + episode downloader —
Pocket Casts is the source of truth for subscriptions and played/
unplayed state, but episode audio is downloaded directly from each
show's own RSS enclosure, not through Pocket Casts. Root workspace
member.

## Usage

```bash
uv run podcast-manager list-subscriptions --credentials-path config/secrets/pocketcasts/<you>.json

uv run podcast-manager sync \
    --profile config/profiles/<you>.yaml \
    --credentials-path config/secrets/pocketcasts/<you>.json \
    --library-root library/podcasts \
    --state-path state/<you>.sqlite \
    --show "<optional: restrict to one show, by UUID or title, repeatable>"
```

Unlike the music fetchers, `sync` downloads every subscribed (or
`--show`-filtered) show's unplayed episodes in one call — no
per-playlist repetition needed. Respects the profile's
`podcasts.sync_unplayed_only`, `podcasts.max_episodes_per_show`,
`podcasts.episode_filter` (`played` vs. Pocket Casts' `archived`
feature — see `notes.md` for why these two signals genuinely diverge),
and per-show `fill_modes` (`newest` vs. `next`/chronological).

```bash
uv run podcast-manager push-play-status \
    --credentials-path config/secrets/pocketcasts/<you>.json \
    --state-path state/<you>.sqlite
```

Pushes locally-recorded device play state (written by
`sync-orchestrator`'s read-back after a real device sync — see
`playstate.py`) back to Pocket Casts, so listening progress on the iPod
shows up in the Pocket Casts app too. Not run automatically anywhere
yet — a manual step after a sync, or worth wiring into a scheduled job
yourself if you want it hands-off.

## Credentials file format

`--credentials-path` points at a small JSON file:

```json
{"email": "you@example.com", "password": "your-pocketcasts-password"}
```

Gitignored under `config/secrets/pocketcasts/` — Pocket Casts
credentials are per-user (unlike the shared household logins for Apple
Music/Spotify/YouTube Music), so each profile has its own. Not
encrypted at rest currently — a known gap, see `notes.md`.
