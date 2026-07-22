# iopenpod: ArtworkDB `mhii` entries missing a structural chunk real iTunes always writes

**Status: fixed and live-verified (2026-07-22).** Album art now displays
correctly on the real 5.5th-gen iPod Video for every pre-existing track and
every track synced via `fetcher-apple` (Apple Music/`gamdl`), confirmed
directly on the device's own screen after a real `sync-orchestrator
--execute` run. The fix is a local workaround in this project
(`_apply_missing_artwork_index_chunk_workaround()` in
`services/sync-orchestrator/src/sync_orchestrator/sync.py`) — see that
function and the `_MHII_MISSING_INDEX_CHUNK` docstring for the exact
implementation, and `notes.md`'s "iopenpod ... ArtworkDB" section for the
full investigation history (identity resolution, byte-level pixel
verification, the iTunes-resync byte-diff that found this, and the local
patch). **Not yet fixed upstream** — the bug report below has been filed
with `TheRealSavi/iOpenPod` but not yet resolved there, so anyone running
unpatched `iopenpod` will still hit this.

(Separately, YouTube Music-sourced tracks — synced via `fetcher-ytmusic`
— have no artwork at all, unrelated to this bug: `fetcher-ytmusic` never
embeds cover art into the downloaded files in the first place, unlike
`gamdl`, which does so automatically. See `notes.md` for that distinct,
not-yet-fixed gap.)

Follow-up for the upstream bug report filed against `TheRealSavi/iOpenPod`
(originally about device-model identification failing for this 5.5th-gen
iPod Video). Written up here so it's tracked in-repo alongside the rest of
this investigation — copy the body below into the GitHub issue as a
follow-up comment.

---

## Follow-up: found the actual cause of missing album art (not just the model-ID issue)

Since filing this, I (Claude.ai) dug further because album art still wasn't displaying
even after manually working around the model-identification problem (forcing
`("iPod", "5.5th Gen")` so `capabilities_for_family_gen` resolves correctly).
Every layer I (Claude.ai) could check from software came back correct — capabilities/
format resolution, the raw RGB565 pixel data in the `.ithmb` files (decoded
and rendered, genuinely correct images), and the `iTunesDB`↔`ArtworkDB`
cross-reference (`artwork_id_ref`/`mhii_link`) — yet nothing ever showed on
the device's own screen, for either newly-written tracks or pre-existing
ones whose `ArtworkDB` index entry got rewritten by a sync.

To get a real reference to compare against, I (Claude.ai) did a fresh iTunes resync of
the same device (same physical unit, confirmed via FireWire GUID) and
byte-diffed the resulting `ArtworkDB` against one iopenpod had written for
the same tracks, using `iopenpod.artworkdb_parser.parser.parse_artworkdb`
against both files.

**Finding**: every `mhii` (artwork index) entry real iTunes writes has a
third child chunk beyond the per-format `THUMBNAIL_IMAGE` (`mhod` type 2)
containers — an `mhod` of type 6 (already named `UNKNOWN_CONTAINER_6 = 6`
in `artworkdb_shared/constants.py`, so this type is recognized, just never
written), wrapping a fixed 96-byte all-zero `mhaf` sub-chunk. `_write_mhii()`
in `artworkdb_writer/artworkdb_chunks.py` never emits it, and the
`childCount` field it writes (offset 12 in the `mhii` header) is one short
of what real iTunes writes as a result.

Checked systematically across both full databases, not just spot-checked
tracks:

| | entries | have the type-6 `mhod` child |
|---|---|---|
| real iTunes (fresh resync) | 1141 | 1141 (100%) |
| iopenpod-written | 5555 | 0 (0%) |

The chunk's payload is identical (all zero bytes) across every one of the
1141 real entries — so it's static boilerplate, not per-track computed
data. Raw bytes (hex, 120 bytes total = 24-byte `mhod` header + 96-byte
`mhaf` body):

```
6d686f6418000000780000000600000000000000000000006d686166600000003c000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
```

This looks like the actual root cause of album art never displaying despite
otherwise-correct writes — consistent with firmware validating an entry's
structural shape (child count / expected layout) and silently declining to
render anything that doesn't match, rather than erroring or refusing to
store it.

**Suggested fix** in `_write_mhii()`: append this fixed 120-byte chunk after
the existing `THUMBNAIL_IMAGE` children, and bump both `total_len` (offset
8) and `childCount` (offset 12) accordingly. I've verified this works as a
local patch (wrapping `_write_mhii` to post-process its output) against a
real device — happy to open a PR with this if useful, or share the patch
directly. Not sure what the `mhaf`/type-6 chunk is actually *for*
(iPhoto/Photos-related, per the always-zero `rating`/`originalDate`/
`exifTakenDate` fields nearby in the same `mhii` record) — it may just need
to be present structurally rather than meaningfully populated for the
click-wheel Now Playing screen specifically.

Device this was confirmed against: 5.5th-gen iPod Video (`idVendor 0x05ac`/
`idProduct 0x1209`), but since the missing chunk is unconditional in
`_write_mhii()` (not gated on device family/generation), I'd expect this to
affect every device family that goes through this writer.
