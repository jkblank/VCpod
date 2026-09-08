# audiobook-manager

Turns a folder of raw, sequentially-numbered MP3 parts (e.g. `01.mp3`..
`12.mp3`) into one properly tagged, chaptered `.m4b` file that iOpenPod's
headless sync correctly classifies as an audiobook (resumable position,
not shuffled like music). Root workspace member.

Built because the automated Libby/OverDrive acquisition path is dead —
`odmpy` was empirically tested against a real account and confirmed
incompatible with how Libby's app links devices today, and OverDrive
killed `.odm` manifests entirely in January 2025. The only currently-
working acquisition method is manual: play the book in Libby's *web*
player (libbyapp.com, desktop browser only) and capture each chapter's
plain MP3 segment from your browser's DevTools Network tab. This tool
picks up from there. See `notes.md` for the full investigation.

Uses [ffmpeg](https://ffmpeg.org/) to merge+chapterize, and
[beets](https://beets.io/) + [beets-audible](https://github.com/Neurrone/beets-audible)
to look up rich metadata (author/narrator/series/cover art) from
Audible/Audnex and tag+place the final file — a heavier, network-
dependent dependency chain than any other service in this workspace
except `podcast-manager`, which is why this is its own service rather
than folded into `library-manager` (kept deliberately minimal).

## Usage

```bash
# One-shot: merge a folder of parts and tag+place the result, in one call.
uv run audiobook-manager import-audiobook \
    --parts-dir "path/to/Franz Kafka - The Trial" \
    --library-root library/audiobooks \
    --state-root state
```

Or run the two steps separately (useful for retrying just the tagging
step after a skip, see "Known gap" below):

```bash
uv run audiobook-manager merge \
    --parts-dir "path/to/Franz Kafka - The Trial" \
    --output state/audiobooks/staging/the-trial/merged.m4b

uv run audiobook-manager tag \
    --source-dir state/audiobooks/staging/the-trial \
    --library-root library/audiobooks \
    --state-root state
```

`import-audiobook` stages the merged file at
`state/audiobooks/staging/<parts-dir-name>/merged.m4b` before tagging —
keeping the original folder's "Author - Title"-shaped name intact, since
beets-audible's own match priority falls back to folder name when no
`metadata.yml`/existing tags are present. On success, beets moves the
file into `library/audiobooks/{Author}/{Album}/{Title}.m4b` (plus
`cover.png`/`desc.txt`/`reader.txt` sidecars) and the empty staging
folder is removed.

## Discovering what still needs processing

If you drop several captured "Author - Title" folders into one drop-zone
directory over time, `discover` scans it and tells you which ones have
already been merged+tagged and which still need `import-audiobook`:

```bash
uv run audiobook-manager discover \
    --root path/to/drop-zone \
    --state-root state
```

```
Franz Kafka - The Trial	12 file(s)	already imported
George Orwell - Animal Farm	9 file(s)	NEW -- needs processing
```

"Already imported" is tracked in `state/audiobooks/discovered_state.json`
(written automatically by every successful `import-audiobook`/`tag` run,
keyed by the parts-dir's own folder name — see `discover.py`), not
guessed from folder-name matching against `library/audiobooks` — a book
beets renamed during tagging still shows up correctly on the next scan.
A drop-zone folder with no audio files in it, or `--root` not existing
yet, is treated as "nothing to report," not an error.

The web GUI's Audiobooks screen wraps this same scan (plus a "Process
into library" button per new book, which runs the same merge+tag
pipeline `import-audiobook` does) behind `config/global.yaml`'s
`audiobook_manager.discover_root` setting — see
[`services/web-gui-backend/README.md`](../web-gui-backend/README.md).

## Known gap: beets-audible can't confidently match every book

`beet import -q` (quiet, non-interactive) never prompts — if it can't
confidently match a book against Audible/Audnex, it **skips** the import
entirely rather than guessing. When that happens, `import-audiobook`
exits 1, prints the exact merged file's path, and prints the retry
command using `tag`. To resolve it, drop a `metadata.yml` next to the
merged file (see beets-audible's own docs for the format — typically
title/author/ASIN) and re-run `tag` against that same staging directory.

## Syncing audiobooks to a device

`library/audiobooks` syncs automatically once it exists — no
`--pc-folder` flag needed, `sync-orchestrator` includes it the same way
it includes `library/music`:

```bash
uv run sync-orchestrator sync \
    --profile config/profiles/<you>.yaml \
    --library-root library \
    --state-root state \
    --execute
```

Which books actually reach a given profile's device is controlled by
that profile's `audiobooks:` config block (default: every audiobook
syncs) — see
[`services/sync-orchestrator/README.md`](../sync-orchestrator/README.md#audiobooks)
for the include/exclude `selections` shape.

## Running with Docker

Gated behind its own Compose profile (unlike `library-manager`/
`podcast-manager`, which have none) — it pulls in beets' full dependency
tree (`numpy`/`scipy`/`numba`/`llvmlite`, unconditional base deps of
beets 2.12, real weight) and does live Audnex/Audible network lookups,
so most households shouldn't have to build it:

```bash
docker compose --profile audiobooks run --rm audiobook-manager \
    import-audiobook --parts-dir /data/library/... --library-root /data/library/audiobooks --state-root /data/state
```
