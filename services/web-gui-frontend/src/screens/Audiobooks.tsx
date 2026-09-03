import { api, type AudiobooksConfig } from '../api'
import AudiobookDiscovery from '../components/AudiobookDiscovery'
import DirectoryPicker from '../components/DirectoryPicker'
import type { ProfileStore } from '../useProfileStore'

const DEFAULT_CONFIG: AudiobooksConfig = { mode: 'include', selections: [] }

export default function Audiobooks({ store }: { store: ProfileStore }) {
  const { draft, setDraft, save, saving, saveErrors } = store

  const config = draft?.audiobooks ?? null

  const setConfig = (next: AudiobooksConfig | null) =>
    draft && setDraft({ ...draft, audiobooks: next })

  return (
    <>
      <AudiobookDiscovery />

      {!draft && (
        <p className="no-profile-notice">
          Select or create a profile on the Profiles screen to curate which audiobooks sync to
          it.
        </p>
      )}

      {draft && (
        <>
          {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}

          <p className="muted">
            Leaving this off syncs every audiobook to this profile — same as an empty curated
            list below with "include" mode. Only turn this on to actually narrow it down.
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
      )}
    </>
  )
}
