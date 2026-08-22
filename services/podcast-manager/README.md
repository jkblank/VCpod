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

Every `sync` also refreshes each show's played state from Pocket Casts
for *every* already-downloaded episode, not just ones still in this
run's download window — otherwise an episode played only through the
Pocket Casts app (never round-tripped through a device) would never get
recorded locally once `sync_unplayed_only` excludes it from candidates.
Then, if `podcasts.delete_played_episodes` (default `true`) and
`sync_unplayed_only` are both on, any episode played — remotely via
Pocket Casts, or locally via `sync-orchestrator`'s device read-back — has
its downloaded audio file deleted, so it stops taking up disk space and
drops out of the next `sync-orchestrator` run's device plan (which
separately proposes removing it from the iPod itself if it's already
there — see that service's README). The state-db row is kept (so a
re-sync doesn't re-download something already listened to); only the
file goes. Set `delete_played_episodes: false` on a profile to keep a
local archive of played episodes instead. `sync_unplayed_only: false`
already means "keep played episodes downloaded too" (e.g. an archive
profile), so deletion never runs in that case regardless of
`delete_played_episodes`.

```bash
uv run podcast-manager push-play-status \
    --credentials-path config/secrets/pocketcasts/<you>.json \
    --state-path state/<you>.sqlite
```

Pushes locally-recorded device play state (written by
`sync-orchestrator`'s read-back after a real device sync — see
`playstate.py`) back to Pocket Casts, so listening progress on the iPod
shows up in the Pocket Casts app too. Manual invocation as shown above
is still supported, but `sync-orchestrator` already calls this
automatically as a subprocess after every real `--execute` sync
(`sync`, `full-sync`, and `auto-sync` alike), gated on that profile's
own `sync.push_play_status_back: true` — see `_maybe_push_play_status`
in `sync-orchestrator/cli.py`. Resume-*position* sync (as opposed to
played/unplayed status) doesn't reliably work via this path — Pocket
Casts' API silently no-ops `played_up_to` — see `notes.md`.

## Credentials file format

`--credentials-path` points at a small JSON file:

```json
{"email": "you@example.com", "password": "your-pocketcasts-password"}
```

Gitignored under `config/secrets/pocketcasts/` — Pocket Casts
credentials are per-user (unlike the shared household logins for Apple
Music/Spotify/YouTube Music), so each profile has its own. Not
encrypted at rest currently — a known gap, see `notes.md`.
