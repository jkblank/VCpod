import { useState } from 'react'
import { ApiError } from '../api'

type Props = {
  using: 'global' | 'override'
  otherProfiles: string[]
  onImport: (fromProfile: string) => Promise<void>
  onRevert: () => Promise<void>
  revertLabel?: string
}

// Shared control for both Apple Music's and YouTube Music's per-profile
// override -- "import" points this profile's config at the exact same
// file another profile already references (not a byte copy, see
// routers/profile_sources.py); "revert" just clears the override,
// falling back to the shared global login. Never automatic -- a
// profile only ever gets here via one of these two explicit clicks.
export default function ImportOrRevertSource({
  using,
  otherProfiles,
  onImport,
  onRevert,
  revertLabel = 'Revert to shared login',
}: Props) {
  const [importFrom, setImportFrom] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const doImport = async () => {
    if (!importFrom) return
    setBusy(true)
    setError(null)
    try {
      await onImport(importFrom)
      setImportFrom('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const doRevert = async () => {
    setBusy(true)
    setError(null)
    try {
      await onRevert()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}
      {using === 'override' ? (
        <button className="btn secondary" onClick={doRevert} disabled={busy}>
          {revertLabel}
        </button>
      ) : otherProfiles.length > 0 ? (
        <div className="row">
          <select value={importFrom} onChange={(e) => setImportFrom(e.target.value)}>
            <option value="">Import from…</option>
            {otherProfiles.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <button className="btn secondary" onClick={doImport} disabled={!importFrom || busy}>
            Import
          </button>
        </div>
      ) : null}
    </div>
  )
}
