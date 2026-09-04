import { useState } from 'react'
import { api, ApiError, type ConnectedDevice, type Profile } from '../api'
import Dialog from '../components/Dialog'
import ScheduleEditor from '../components/ScheduleEditor'
import type { ProfileStore } from '../useProfileStore'

type DiffRow = { label: string; from: string; to: string }

// Compares the fields this screen actually edits (device/sync/fetch --
// playlists/podcasts/etc. are edited on their own screens) against the
// last-loaded-or-saved copy already sitting in store.profiles, so
// "Review & save" can show a real before/after, not a guess.
function fieldDiff(before: Profile | undefined, after: Profile): DiffRow[] {
  const rows: DiffRow[] = []
  const push = (label: string, from: unknown, to: unknown) => {
    if (JSON.stringify(from) === JSON.stringify(to)) return
    rows.push({
      label,
      from: from === undefined ? '(new)' : JSON.stringify(from),
      to: JSON.stringify(to),
    })
  }
  push('Device match by', before?.device.match_by, after.device.match_by)
  push('Device match value', before?.device.match_value, after.device.match_value)
  push('Sync trigger', before?.sync.trigger, after.sync.trigger)
  push('Transcode format', before?.sync.transcode_format, after.sync.transcode_format)
  push('Write mode', before?.sync.mode, after.sync.mode)
  push('Push play status back', before?.sync.push_play_status_back, after.sync.push_play_status_back)
  push('Fetch schedule', before?.fetch.schedule, after.fetch.schedule)
  return rows
}

export default function Profiles({ store }: { store: ProfileStore }) {
  const { profiles, selected, draft, setDraft, loadError, saveErrors, saving, select, startNew, save, remove } =
    store
  const [detected, setDetected] = useState<ConnectedDevice[] | null>(null)
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)

  const detect = async () => {
    setDetecting(true)
    setDetectError(null)
    try {
      const { devices } = await api.identifyDevice()
      setDetected(devices)
    } catch (e) {
      setDetectError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setDetecting(false)
    }
  }

  const useDetected = (d: ConnectedDevice) => {
    if (!draft) return
    setDraft({
      ...draft,
      device: { match_by: 'serial', match_value: d.serial || d.volume_label },
    })
  }

  if (loadError) return <div className="error-banner">{loadError}</div>

  const diffRows = draft ? fieldDiff(selected ? profiles[selected] : undefined, draft) : []

  return (
    <div className="profiles-layout">
      <div className="profile-list-col">
        {Object.keys(profiles)
          .sort()
          .map((name) => (
            <button
              key={name}
              className={name === selected ? 'profile-chip active' : 'profile-chip'}
              onClick={() => select(name)}
            >
              {name}
            </button>
          ))}
        <button className="btn secondary" onClick={startNew}>
          + New profile
        </button>
      </div>

      {draft && (
        <div className="profiles-editor">
          <div className="card">
            {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}
            {detectError && <div className="error-banner">{detectError}</div>}

            <div className="row">
              <button className="btn secondary" onClick={detect} disabled={detecting}>
                {detecting ? 'Detecting…' : 'Detect connected device'}
              </button>
            </div>
            {detected && detected.length === 0 && <p className="detected-line">No device connected.</p>}
            {detected?.map((d) => (
              <p className="detected-line" key={d.path}>
                Detected: {d.volume_label || '(no label)'} · serial {d.serial || '(none)'} ·{' '}
                {d.model_family} {d.generation} · {d.capacity}{' '}
                <button className="btn secondary" onClick={() => useDetected(d)}>
                  Use this
                </button>
              </p>
            ))}

            <div className="field">
              <label>Device match by</label>
              <div className="seg">
                {(['serial', 'volume_label'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    className={`seg-opt${draft.device.match_by === v ? ' active' : ''}`}
                    onClick={() => setDraft({ ...draft, device: { ...draft.device, match_by: v } })}
                  >
                    {v === 'serial' ? 'Serial' : 'Volume label'}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>Device match value</label>
              <input
                value={draft.device.match_value}
                onChange={(e) =>
                  setDraft({ ...draft, device: { ...draft.device, match_value: e.target.value } })
                }
              />
            </div>

            <div className="field">
              <label>Sync trigger</label>
              <div className="seg">
                {(['on_connect', 'manual', 'cron'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    className={`seg-opt${draft.sync.trigger === v ? ' active' : ''}`}
                    onClick={() => setDraft({ ...draft, sync: { ...draft.sync, trigger: v } })}
                  >
                    {v === 'on_connect' ? 'On connect' : v === 'manual' ? 'Manual' : 'Cron'}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>Transcode format</label>
              <div className="seg">
                {(['alac', 'aac'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    className={`seg-opt${draft.sync.transcode_format === v ? ' active' : ''}`}
                    onClick={() => setDraft({ ...draft, sync: { ...draft.sync, transcode_format: v } })}
                  >
                    {v === 'alac' ? 'ALAC (lossless)' : 'AAC (lossy, smaller)'}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>Write mode</label>
              <div className="seg">
                <button
                  type="button"
                  className={`seg-opt${draft.sync.mode === 'itunes' ? ' active' : ''}`}
                  onClick={() =>
                    setDraft({ ...draft, sync: { ...draft.sync, mode: 'itunes' as Profile['sync']['mode'] } })
                  }
                >
                  iTunes (iTunesDB/ArtworkDB)
                </button>
                <button type="button" className="seg-opt" disabled title="Coming soon">
                  Rockbox — coming soon
                </button>
              </div>
              {draft.sync.mode === 'rockbox' && (
                <p className="muted">
                  This profile is already set to Rockbox mode (edited outside the GUI) — the Sync
                  screen doesn't support it yet (no <code>--json</code> output from
                  sync-orchestrator in Rockbox mode). CLI sync still works normally.
                </p>
              )}
            </div>
            <div className="row">
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
                <input
                  type="checkbox"
                  checked={draft.sync.push_play_status_back}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      sync: { ...draft.sync, push_play_status_back: e.target.checked },
                    })
                  }
                />
                Push played state back to Pocket Casts after sync
              </label>
            </div>

            <ScheduleEditor
              value={draft.fetch.schedule}
              onChange={(schedule) => setDraft({ ...draft, fetch: { schedule } })}
            />

            <div className="row">
              <button
                className="btn"
                onClick={() => (diffRows.length > 0 ? setReviewOpen(true) : save())}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Review & save'}
              </button>
              <button className="btn danger" onClick={() => remove(draft.profile)}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {reviewOpen && draft && (
        <Dialog
          title={`Save ${draft.profile}.yaml`}
          onClose={() => setReviewOpen(false)}
          actions={
            <>
              <button className="btn secondary" onClick={() => setReviewOpen(false)}>
                Cancel
              </button>
              <button
                className="btn"
                onClick={async () => {
                  setReviewOpen(false)
                  await save()
                }}
              >
                Save
              </button>
            </>
          }
        >
          {diffRows.length === 0 ? (
            <p>No changes.</p>
          ) : (
            <table className="diff-table">
              <tbody>
                {diffRows.map((row) => (
                  <tr key={row.label}>
                    <td>{row.label}</td>
                    <td className="diff-from">{row.from}</td>
                    <td className="diff-to">{row.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Dialog>
      )}
    </div>
  )
}
