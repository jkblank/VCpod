import { api, type ExternalLibraryConfig } from '../api'
import DirectoryPicker from '../components/DirectoryPicker'
import type { ProfileStore } from '../useProfileStore'

const DEFAULT_CONFIG: ExternalLibraryConfig = { path: '', mode: 'include', selections: [] }

export default function ExternalLibrary({ store }: { store: ProfileStore }) {
  const { draft, setDraft, save, saving, saveErrors } = store

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen first.
      </p>
    )
  }

  const config = draft.external_library

  const setConfig = (next: ExternalLibraryConfig | null) =>
    setDraft({ ...draft, external_library: next })

  return (
    <>
      {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}

      <div className="row">
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
          <input
            type="checkbox"
            checked={config != null}
            onChange={(e) => setConfig(e.target.checked ? DEFAULT_CONFIG : null)}
          />
          Sync a personal music folder outside the managed library for this profile
        </label>
      </div>

      {config && (
        <div className="card">
          <div className="field">
            <label>Folder path (on the machine running the backend)</label>
            <input
              value={config.path}
              placeholder="/home/alice/Music/MusicLibrary"
              onChange={(e) => setConfig({ ...config, path: e.target.value })}
            />
          </div>
          <div className="field">
            <label>Mode</label>
            <select
              value={config.mode}
              onChange={(e) =>
                setConfig({ ...config, mode: e.target.value as ExternalLibraryConfig['mode'] })
              }
            >
              <option value="include">Include only what's ticked below</option>
              <option value="exclude">Sync everything except what's ticked below</option>
            </select>
          </div>

          {config.path.trim() ? (
            <DirectoryPicker
              browse={(subpath) => api.browseExternalLibrary(config.path, subpath)}
              selections={config.selections}
              onSelectionsChange={(selections) => setConfig({ ...config, selections })}
            />
          ) : (
            <p className="muted">Enter a folder path above to browse it.</p>
          )}

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
