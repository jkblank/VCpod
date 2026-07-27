# fetcher-spotify

Downloads Spotify playlist tracks via [zotify](https://github.com/zotify-dev/zotify)
(specifically the actively-maintained [Googolplexed0/zotify](https://github.com/Googolplexed0/zotify)
fork — the original is effectively abandoned, still pinned to a
pre-login5-migration `librespot` commit). Same fetch contract as
`fetcher-apple`/`fetcher-ytmusic`: tags tracks, writes `.m3u8`, records
state db rows.

## Status: shelved

Authentication works, but real downloads are currently blocked on a
**Spotify Premium requirement** for the API access this depends on —
not a bug in this project, an external platform restriction outside its
control. `music-stack sync`/`fetch-scheduler` both explicitly treat
`spotify` as an unsupported source (a clear "not supported by this
command yet" message, not a crash) rather than silently no-op-ing or
attempting a fetch that's known to fail. See `notes.md` for the
migration history and the specific blocker.

## Standalone project

Not a root-workspace member — `zotify`/`librespot` are heavy/pinned-fork
dependencies kept isolated from the rest of the workspace, same
reasoning as `sync-orchestrator`'s `iopenpod` isolation. Run everything
from inside this directory:

```bash
cd services/fetcher-spotify
uv sync
uv run pytest   # not picked up by the root workspace's `uv run pytest`
```

## Usage

```bash
uv run fetcher-spotify list-playlists --credentials-path config/secrets/spotify_credentials.json

uv run fetcher-spotify fetch \
    --profile ../../config/profiles/<you>.yaml \
    --playlist "<name from your profile>" \
    --credentials-path ../../config/secrets/spotify_credentials.json \
    --library-root ../../library/music \
    --playlists-root ../../library/playlists \
    --state-path ../../state/<you>.sqlite
```

Same one-playlist-per-call shape as `fetcher-apple`/`fetcher-ytmusic` —
`fetch` looks up the named playlist in the given profile (must be a
`spotify` entry) and downloads only that one.
