import { useEffect, useState } from 'react'
import { api, ApiError, type GlobalConfig, type Profile } from '../api'

export default function Overview() {
  const [profiles, setProfiles] = useState<Record<string, Profile> | null>(null)
  const [globalConfig, setGlobalConfig] = useState<GlobalConfig | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.listProfiles(), api.getGlobalConfig()])
      .then(([p, g]) => {
        setProfiles(p)
        setGlobalConfig(g)
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  if (error) return <div className="error-banner">{error}</div>
  if (!profiles || !globalConfig) return <p className="muted">Loading…</p>

  const enabledSources = Object.entries(globalConfig.sources).filter(
    ([, s]) => (s as { enabled: boolean }).enabled,
  ).length

  return (
    <div className="stat-row">
      <div className="stat">
        <div className="value">{Object.keys(profiles).length}</div>
        <div className="label">profiles</div>
      </div>
      <div className="stat">
        <div className="value">{enabledSources}</div>
        <div className="label">enabled sources</div>
      </div>
      <div className="stat">
        <div className="value">{globalConfig.paths.library_root}</div>
        <div className="label">library_root</div>
      </div>
    </div>
  )
}
