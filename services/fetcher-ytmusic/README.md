# fetcher-ytmusic

Downloads YouTube Music playlist tracks via `ytmusicapi` (metadata) +
`yt-dlp` (audio), tags them, writes `.m3u8`, records state db rows —
same contract as `fetcher-apple`/`fetcher-spotify`.

## Requirements

**The `bgutil-ytdlp-pot-provider` companion server must be running**
before any real download will work. Without it, YouTube's bot-check
blocks every real audio format (only thumbnail storyboards resolve) —
confirmed live, not a theoretical concern. See `notes.md` for the full
investigation.

1. `deno` must be on `PATH` (`sudo pacman -S deno` or your distro's
   equivalent) — needed both by yt-dlp itself (signature/"n challenge"
   solving) and to run the companion server below.
2. Clone the companion server once:
   ```bash
   git clone --single-branch --branch 1.3.1 \
       https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
       services/fetcher-ytmusic/pot-provider
   cd services/fetcher-ytmusic/pot-provider/server
   deno install --allow-scripts=npm:canvas --frozen
   ```
3. Run it (must stay running for the whole time you're fetching —
   it mints PO Tokens on demand, there's no offline/cached mode):
   ```bash
   cd services/fetcher-ytmusic/pot-provider/server/node_modules
   deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts
   ```
   Listens on `127.0.0.1:4416` by default. yt-dlp auto-detects it once
   the pip-installable plugin half is present (already in the root
   workspace's `pyproject.toml` — `uv sync` at the repo root installs
   it, nothing extra needed there).

`pot-provider/` is gitignored — it's a separate, third-party project
cloned in place, not vendored into this repo.

## Usage

```bash
uv run fetcher-ytmusic list-playlists --oauth-path <oauth.json>
uv run fetcher-ytmusic fetch \
    --profile config/profiles/<you>.yaml \
    --playlist "<name>" \
    --cookies-path config/secrets/youtube_cookies.txt \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-path state/<you>.sqlite
```

`--cookies-path` is yt-dlp's YouTube cookies (Netscape format) — export
from a real, logged-in browser session, same process as Apple Music's
cookies file. `--oauth-path` (`list-playlists` only, optional on
`fetch`) is `ytmusicapi`'s own OAuth token — only needed for the
account's own private library listing; public playlists resolve fine
without it (`ytmusicapi.get_playlist()` works fully unauthenticated,
confirmed live).
