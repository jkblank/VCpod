# fetcher-apple

Downloads Apple Music playlist tracks via [`gamdl`](https://github.com/glomatico/gamdl)
(metadata + decrypted audio), tags them, writes `.m3u8`, records state db
rows — same contract as `fetcher-ytmusic`/`fetcher-spotify`. Root
workspace member.

## Requirements

`--cookies-path` is a Netscape-format cookies file exported from a real,
logged-in Apple Music session in your browser (an extension like "Get
cookies.txt" works) — `gamdl` uses it to authenticate as your account.
These cookies expire every few weeks; a fetch that suddenly fails with
`GamdlApiResponseError: Error fetching account info` (or the
`media-user-token` cookie not found) after previously working almost
always means it's time to re-export, not a code problem.

**`GamdlApiResponseError: Error fetching Apple Music homepage` is a
different failure mode and is *not* a cookie problem** — that specific
call (`gamdl`'s internal `get_token()`) is an unauthenticated homepage
scrape, cookies aren't involved at all. If you hit this, check network
connectivity/DNS before touching cookies. Confirmed live (2026-08-21):
a host with a non-routable IPv6 address (e.g. a ULA `fd00::/8` with no
real uplink) causes this every time — `httpx`/`httpcore` (used
internally by `gamdl`, no configurable timeout) tries the dead IPv6
route first and hits its hardcoded ~5s `ConnectTimeout` before ever
falling back to IPv4, while `curl` on the same host succeeds instantly
via real Happy Eyeballs. Worked around in `fetcher_apple/_net.py`
(`force_ipv4_dns()`, forces `socket.getaddrinfo` to AF_INET-only) —
applied both in-process (`api.py`, for `list-playlists`/metadata calls)
and in the separate `gamdl` CLI subprocess (`download.py`, via a
`PYTHONPATH`-injected `sitecustomize.py` in `_sitecustomize_ipv4/`,
since that subprocess is a different Python process the in-process
patch can't reach). If this host's IPv6 routing ever gets fixed for
real, the patch is still harmless to leave in place.

## Usage

```bash
uv run fetcher-apple list-playlists --cookies-path config/secrets/apple_music_cookies.txt

uv run fetcher-apple fetch \
    --profile config/profiles/<you>.yaml \
    --playlist "<name from your profile>" \
    --cookies-path config/secrets/apple_music_cookies.txt \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-path state/<you>.sqlite
```

`fetch` looks up the named playlist in the given profile (must be an
`apple_music` entry) and downloads only that one — call it once per
playlist, or use `music-stack fetch` (see `services/music-stack-cli`) to
fetch an entire profile's playlists across every source in one call
instead of invoking each fetcher directly.

`--library-root` is resolved (`Path(...).resolve()`) before any output
path is derived from it — a relative path here would otherwise write
relative paths into `.m3u8`/the state db that `sync-orchestrator`'s
playlist-file matching can't resolve later (confirmed live to silently
drop entire playlists — see `notes.md`). Always pass an absolute path,
or run from the repo root where the relative default resolves correctly.

Two fetch strategies exist under the hood depending on the playlist ID
shape (`_fetch_via_playlist_url` for real Apple-catalog playlist IDs,
a slower per-track fallback for Apple's algorithmic Mix playlists,
which don't expose a stable catalog ID) — both produce the same output
contract, this is invisible from the CLI.
