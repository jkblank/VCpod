# library-manager

Cross-source deduplication and quarantine cleanup for the shared
`library/music/` — catches the same song downloaded from more than one
source (Apple Music + YouTube Music, say) and collapses it down to one
canonical file. Root workspace member.

## How dedup works

1. `scan_library` walks `--library-root`, reading each file's own
   `source`/`source_id`/`isrc` tags (written by every fetcher at
   download time) — files missing these tags are skipped, not errored
   on (distinguishes "ours" from something you dropped in manually).
2. `find_duplicate_groups` groups tracks that are the same song from
   *different* sources: exact ISRC match first, falling back to fuzzy
   `artist + title` matching (`--fuzzy-threshold`, default 92) for
   tracks with no ISRC. Same-source tracks are never grouped against
   each other.
3. For each group, the highest-fidelity copy is kept
   (`FIDELITY_ORDER = apple_music > spotify > ytmusic`) and every other
   copy is moved to `library_root/.duplicates/<source>/<source_id><ext>`
   — not deleted outright. Every given profile's state db gets its
   `local_path` for that track repointed at the canonical file, and
   every `.m3u8` referencing the moved path is rewritten to point at
   canonical instead.
4. `cleanup-duplicates` is the separate, later pass that actually
   deletes quarantined files — only once they've sat in `.duplicates/`
   past `--older-than-days` (default 14), giving a grace period to
   notice and undo a bad dedup decision before it's permanent.

## Usage

```bash
# Find + quarantine cross-source duplicates. --state-dir globs every
# *.sqlite under it, so one pass updates every profile sharing this
# library at once.
uv run library-manager dedup \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-dir state \
    --fuzzy-threshold 92.0

# Permanently delete quarantined files past the grace period.
uv run library-manager cleanup-duplicates \
    --library-root library/music \
    --older-than-days 14 \
    --dry-run   # list what would be removed without touching anything
```

## Automatic scheduling

Both commands also run automatically as a post-step in
`fetch-scheduler`'s tick, whenever any profile actually fetches — see
`config/global.yaml`'s `library_manager.dedup_enabled`/
`cleanup_enabled` and `services/fetch-scheduler/README.md`. No separate
schedule of their own; toggling these two booleans is the only config
needed, the CLI above stays available for manual/ad hoc runs on top of
that.

## Known gap

Dedup only scans one `--library-root` — it has no visibility into a
separate, pre-existing personal library mounted via a profile's
`external_library` config (see `sync-orchestrator/README.md`), so a
duplicate that exists between the managed library and an external one
won't be caught. Not yet fixed — see `notes.md`.
