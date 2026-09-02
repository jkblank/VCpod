import { api, type AudiobooksConfig } from '../api'
import DirectoryPicker from '../components/DirectoryPicker'
import type { ProfileStore } from '../useProfileStore'

const DEFAULT_CONFIG: AudiobooksConfig = { mode: 'include', selections: [] }

export default function Audiobooks({ store }: { store: ProfileStore }) {
  const { draft, setDraft, save, saving, saveErrors } = store

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen first.
      </p>
    )
  }

  const config = draft.audiobooks

  const setConfig = (next: AudiobooksConfig | null) => setDraft({ ...draft, audiobooks: next })

  return (
    <>
      {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}

      <p className="muted">
        Leaving this off syncs every audiobook to this profile — same as an empty curated list
        below with "include" mode. Only turn this on to actually narrow it down.
      </p>

      <div className="row">
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
          <input
            type="checkbox"
            checked={config != null}
            onChange={(e) => setConfig(e.target.checked ? DEFAULT_CONFIG : null)}
          />
          Curate which audiobooks sync to this profile
        </label>
      </div>

      {config && (
        <div className="card">
          <div className="field">
            <label>Mode</label>
            <select
              value={config.mode}
              onChange={(e) =>
                setConfig({ ...config, mode: e.target.value as AudiobooksConfig['mode'] })
              }
            >
              <option value="include">Include only what's ticked below</option>
              <option value="exclude">Sync everything except what's ticked below</option>
            </select>
          </div>

          <DirectoryPicker
            browse={(subpath) => api.browseAudiobooks(subpath)}
            selections={config.selections}
            onSelectionsChange={(selections) => setConfig({ ...config, selections })}
          />

          <div className="row" style={{ marginTop: '16px' }}>
            <button className="btn" onClick={() => save()} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
