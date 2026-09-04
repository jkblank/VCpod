import { useEffect, useState } from 'react'
import { api, ApiError, type Overview as OverviewData } from '../api'
import { formatBytes, formatRelativeIso } from '../format'

const ALERT_LABEL: Record<string, string> = {
  missing: 'Missing',
  stale: 'Stale',
  unreachable: 'Unreachable',
}

// Polled, not just fetched-once -- this is a dashboard meant to be left
// open, and every field it shows (device connection, activity log,
// alerts) can change from something else entirely (a udev-triggered
// auto-sync, a fetch-scheduler tick) without this tab doing anything.
const POLL_MS = 30_000

export default function Overview() {
  const [data, setData] = useState<OverviewData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .getOverview()
        .then((d) => {
          if (!cancelled) {
            setData(d)
            setError(null)
          }
        })
        .catch((e: unknown) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)))
    load()
    const interval = window.setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  if (error) return <div className="error-banner">{error}</div>
  if (!data) return <p className="muted">Loading…</p>

  return (
    <div>
      {data.alerts.length > 0 && (
        <div className="card">
          <div className="card-kicker">Alerts</div>
          {data.alerts.map((alert, i) => (
            <div key={i} className="row" style={{ marginBottom: i === data.alerts.length - 1 ? 0 : 8 }}>
              <span className={`tag tag-${alert.severity === 'missing' ? 'danger' : 'accent'}`}>
                {ALERT_LABEL[alert.severity] ?? alert.severity}
              </span>
              <span className="card-body" style={{ margin: 0 }}>
                {alert.profile ? `[${alert.profile}] ` : ''}
                {alert.message}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="card-kicker" style={{ marginTop: 4 }}>Devices</div>
      {data.devices.length === 0 && <p className="muted">No profiles yet — add one under Profiles.</p>}
      <div className="stat-row" style={{ marginBottom: 20 }}>
        {data.devices.map((card) => (
          <div key={card.profile} className="card" style={{ minWidth: 220, flex: '1 1 220px', marginBottom: 0 }}>
            <div className="card-title">{card.profile}</div>
            {card.connected_device ? (
              <>
                <span className="tag tag-accent">Connected</span>
                <div className="card-body">
                  {card.connected_device.model_family} {card.connected_device.generation}
                  <br />
                  {formatBytes(card.connected_device.used_bytes)} used /{' '}
                  {formatBytes(card.connected_device.free_bytes)} free
                </div>
              </>
            ) : (
              <span className="tag tag-neutral">Not connected</span>
            )}
            <div className="card-body" style={{ marginTop: 8 }}>
              {card.track_count} tracks · {card.episode_count} episodes
              {card.unplayed_episode_count > 0 && ` (${card.unplayed_episode_count} unplayed)`}
              <br />
              last sync: {formatRelativeIso(card.last_sync)}
              <br />
              next fetch: {formatRelativeIso(card.next_fetch, 'from now')}
            </div>
          </div>
        ))}
      </div>

      <div className="row" style={{ alignItems: 'flex-start', gap: 24 }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <div className="card-kicker">Recent activity</div>
          {data.recent_activity.length === 0 ? (
            <p className="muted">Nothing recorded yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Profile</th>
                  <th>Description</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_activity.map((entry, i) => (
                  <tr key={i}>
                    <td>{formatRelativeIso(entry.started_at)}</td>
                    <td>{entry.profile}</td>
                    <td>{entry.description}</td>
                    <td>
                      <span className={`tag ${entry.result === 'ok' ? 'tag-accent' : 'tag-danger'}`}>
                        {entry.result}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div style={{ minWidth: 160 }}>
          <div className="card-kicker">Library</div>
          <div className="stat">
            <div className="value">{data.library.track_count}</div>
            <div className="label">tracks</div>
          </div>
        </div>
      </div>
    </div>
  )
}
