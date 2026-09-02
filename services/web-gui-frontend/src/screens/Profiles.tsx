import { useEffect, useState } from 'react'
import { api, ApiError, type ConnectedDevice, type Profile } from '../api'

function emptyProfile(name: string): Profile {
  return {
    profile: name,
    device: { match_by: 'serial', match_value: '' },
    sync: {
      trigger: 'manual',
      transcode_format: 'alac',
      push_play_status_back: false,
      mode: 'itunes',
    },
    fetch: { schedule: null },
    playlists: [],
    podcasts: {
      pocketcasts: { credentials_file: `/config/secrets/pocketcasts/${name}.json` },
      sync_unplayed_only: true,
      max_episodes_per_show: 5,
      shows: 'all',
    },
  }
}

export default function Profiles() {
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState<Profile | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveErrors, setSaveErrors] = useState<string[] | null>(null)
  const [saving, setSaving] = useState(false)
  const [detected, setDetected] = useState<ConnectedDevice[] | null>(null)
  const [detecting, setDetecting] = useState(false)

  const load = () =>
    api
      .listProfiles()
      .then((p) => {
        setProfiles(p)
        setLoadError(null)
      })
      .catch((e: unknown) => setLoadError(e instanceof ApiError ? e.message : String(e)))

  useEffect(() => {
    load()
  }, [])

  const select = (name: string) => {
    setSelected(name)
    setDraft(profiles[name] ?? null)
    setSaveErrors(null)
    setDetected(null)
  }

  const startNew = () => {
    const name = window.prompt('New profile name (e.g. "sam"):')
    if (!name) return
    setSelected(name)
    setDraft(emptyProfile(name))
    setSaveErrors(null)
    setDetected(null)
  }

  const save = async () => {
    if (!draft) return
    setSaving(true)
    setSaveErrors(null)
    try {
      const saved = await api.putProfile(draft.profile, draft)
      setProfiles((prev) => ({ ...prev, [saved.profile]: saved }))
      setDraft(saved)
    } catch (e) {
      setSaveErrors(e instanceof ApiError ? e.errors : [String(e)])
    } finally {
      setSaving(false)
    }
  }

  const remove = async (name: string) => {
    if (!window.confirm(`Delete config/profiles/${name}.yaml? This cannot be undone here.`)) return
    await api.deleteProfile(name)
    setProfiles((prev) => {
      const next = { ...prev }
      delete next[name]
      return next
    })
    if (selected === name) {
      setSelected(null)
      setDraft(null)
    }
  }

  const detect = async () => {
    setDetecting(true)
    try {
      const { devices } = await api.identifyDevice()
      setDetected(devices)
    } catch (e) {
      setSaveErrors([e instanceof ApiError ? e.message : String(e)])
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
          {saveErrors && (
            <div className="error-banner">{saveErrors.join('\n')}</div>
          )}

          <div className="row">
            <button className="btn secondary" onClick={detect} disabled={detecting}>
              {detecting ? 'Detecting…' : 'Detect connected device'}
            </button>
          </div>
          {detected && detected.length === 0 && (
            <p className="detected-line">No device connected.</p>
          )}
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
            <input
              value={draft.sync.transcode_format}
              onChange={(e) =>
                setDraft({ ...draft, sync: { ...draft.sync, transcode_format: e.target.value } })
              }
            />
          </div>

          <div className="row">
            <button className="btn" onClick={save} disabled={saving}>
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
