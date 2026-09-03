import { useState } from 'react'
import { api, ApiError, type ConnectedDevice, type Profile } from '../api'
import ScheduleEditor from '../components/ScheduleEditor'
import type { ProfileStore } from '../useProfileStore'

export default function Profiles({ store }: { store: ProfileStore }) {
  const { profiles, selected, draft, setDraft, loadError, saveErrors, saving, select, startNew, save, remove } =
    store
  const [detected, setDetected] = useState<ConnectedDevice[] | null>(null)
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)

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

  return (
    <>
      <div className="profile-list">
        {Object.keys(profiles).map((name) => (
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
            <select
              value={draft.device.match_by}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  device: { ...draft.device, match_by: e.target.value as 'serial' | 'volume_label' },
                })
              }
            >
              <option value="serial">Serial</option>
              <option value="volume_label">Volume label</option>
            </select>
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
            <select
              value={draft.sync.trigger}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  sync: { ...draft.sync, trigger: e.target.value as Profile['sync']['trigger'] },
                })
              }
            >
              <option value="on_connect">On connect</option>
              <option value="manual">Manual</option>
              <option value="cron">Cron</option>
            </select>
          </div>
          <div className="field">
            <label>Transcode format</label>
            <select
              value={draft.sync.transcode_format}
              onChange={(e) =>
                setDraft({ ...draft, sync: { ...draft.sync, transcode_format: e.target.value } })
              }
            >
              <option value="alac">ALAC (lossless)</option>
              <option value="aac">AAC (lossy, smaller)</option>
            </select>
          </div>
          <div className="field">
            <label>Write mode</label>
            <select
              value={draft.sync.mode}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  sync: { ...draft.sync, mode: e.target.value as Profile['sync']['mode'] },
                })
              }
            >
              <option value="itunes">iTunes (iTunesDB/ArtworkDB)</option>
              <option value="rockbox" disabled>
                Rockbox (plain file mirror) — coming soon
              </option>
            </select>
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
            <button className="btn" onClick={() => save()} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button className="btn danger" onClick={() => remove(draft.profile)}>
              Delete
            </button>
          </div>
        </div>
      )}
    </>
  )
}
