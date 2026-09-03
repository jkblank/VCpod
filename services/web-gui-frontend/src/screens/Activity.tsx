import { useEffect, useState } from 'react'
import { api, ApiError, type ActivityEntry } from '../api'
import { formatRelativeIso } from '../format'

export default function Activity() {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getActivity(50)
      .then((r) => setEntries(r.entries))
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  if (error) return <div className="error-banner">{error}</div>
  if (!entries) return <p className="muted">Loading…</p>
  if (entries.length === 0) {
    return (
      <p className="muted">
        Nothing recorded yet — this fills in as fetch-scheduler ticks and sync-orchestrator runs happen.
      </p>
    )
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>When</th>
          <th>Service</th>
          <th>Profile</th>
          <th>Description</th>
          <th>Duration</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry, i) => (
          <tr key={i}>
            <td>{formatRelativeIso(entry.started_at)}</td>
            <td>{entry.service}</td>
            <td>{entry.profile}</td>
            <td>{entry.description}</td>
            <td>{entry.duration_seconds < 1 ? '<1s' : `${Math.round(entry.duration_seconds)}s`}</td>
            <td>
              <span className={`tag ${entry.result === 'ok' ? 'tag-accent' : 'tag-danger'}`}>
                {entry.result}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
