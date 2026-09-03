import { useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  streamSyncExecute,
  streamSyncPlan,
  type ConnectedDevice,
  type SyncPlanSummary,
  type SyncResultSummary,
} from '../api'
import AutoSyncSetupCard from '../components/AutoSyncSetupCard'
import type { ProfileStore } from '../useProfileStore'

function formatBytes(bytes: number): string {
  const sign = bytes < 0 ? '-' : ''
  const abs = Math.abs(bytes)
  if (abs < 1024) return `${sign}${abs} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = abs / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${sign}${value.toFixed(1)} ${units[unit]}`
}

type RunningAction = 'plan' | 'execute' | 'dangerous' | null

export default function Sync({ store }: { store: ProfileStore }) {
  const { draft } = store
  const [devices, setDevices] = useState<ConnectedDevice[] | null>(null)
  const [deviceError, setDeviceError] = useState<string | null>(null)
  const [runningAction, setRunningAction] = useState<RunningAction>(null)
  const [log, setLog] = useState<string[]>([])
  const [plan, setPlan] = useState<SyncPlanSummary | null>(null)
  const [result, setResult] = useState<SyncResultSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [allowRemovals, setAllowRemovals] = useState(false)
  const [dangerousMode, setDangerousMode] = useState(false)
  const logRef = useRef<HTMLPreElement | null>(null)

  useEffect(() => {
    api
      .identifyDevice()
      .then((r) => setDevices(r.devices))
      .catch((e) => setDeviceError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  const connected =
    draft && devices
      ? devices.find((d) =>
          draft.device.match_by === 'serial'
            ? d.serial === draft.device.match_value
            : d.volume_label === draft.device.match_value,
        )
      : null

  const reset = () => {
    setLog([])
    setPlan(null)
    setResult(null)
    setError(null)
    setAllowRemovals(false)
  }

  const run = async (
    action: Exclude<RunningAction, null>,
    stream: AsyncGenerator<{ event: 'progress' | 'result' | 'error'; data: string }>,
    onResult: (data: string) => void,
  ) => {
    setRunningAction(action)
    try {
      for await (const evt of stream) {
        if (evt.event === 'progress') {
          setLog((prev) => [...prev, evt.data])
        } else if (evt.event === 'result') {
          onResult(evt.data)
        } else {
          setError(evt.data)
          return
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setRunningAction(null)
    }
  }

  const computePlan = async () => {
    if (!draft) return
    reset()
    await run('plan', streamSyncPlan({ profile: draft.profile }), (data) => {
      setPlan(JSON.parse(data) as SyncPlanSummary)
    })
  }

  const execute = async () => {
    if (!draft) return
    setResult(null)
    setError(null)
    await run(
      'execute',
      streamSyncExecute({ profile: draft.profile, allow_removals: allowRemovals }),
      (data) => setResult(JSON.parse(data) as SyncResultSummary),
    )
  }

  const dangerousSync = async () => {
    if (!draft) return
    reset()
    await run(
      'dangerous',
      streamSyncExecute({ profile: draft.profile, allow_removals: true }),
      (data) => setResult(JSON.parse(data) as SyncResultSummary),
    )
  }

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen to sync its device.
      </p>
    )
  }

  const hasRemovals = plan != null && (plan.to_remove_count > 0 || plan.playlists_to_remove.length > 0)
  const running = runningAction !== null

  return (
    <>
      <div className="card">
        <p>
          Device for <strong>{draft.profile}</strong> ({draft.device.match_by}=
          {draft.device.match_value}):{' '}
          {deviceError ? (
            <span className="muted">could not check ({deviceError})</span>
          ) : connected ? (
            <strong>
              connected — {connected.model_family} {connected.generation} ({connected.capacity})
            </strong>
          ) : (
            <span className="muted">not connected</span>
          )}
        </p>

        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
            <input
              type="checkbox"
              checked={dangerousMode}
              onChange={(e) => {
                setDangerousMode(e.target.checked)
                reset()
              }}
              disabled={running}
            />
            Dangerous mode
          </label>
        </div>

        {dangerousMode ? (
          <>
            <div className="warning-banner">
              Skips the plan-review step entirely. Clicking the button below immediately runs a
              real sync <strong>with removals allowed</strong> — anything no longer in scope gets
              deleted from the device right away, with no chance to review first.
            </div>
            <button className="btn danger" onClick={dangerousSync} disabled={running}>
              {runningAction === 'dangerous' ? 'Syncing…' : 'Sync now (dangerous)'}
            </button>
          </>
        ) : (
          <div className="row">
            <button className="btn" onClick={computePlan} disabled={running}>
              {runningAction === 'plan' ? 'Computing…' : 'Compute plan'}
            </button>
            <button
              className="btn"
              onClick={execute}
              disabled={running || !plan || (hasRemovals && !allowRemovals)}
            >
              {runningAction === 'execute' ? 'Syncing…' : 'Execute sync'}
            </button>
          </div>
        )}
      </div>

      {log.length > 0 && (
        <pre className="sync-log" ref={logRef}>
          {log.join('\n')}
        </pre>
      )}

      {error && <div className="error-banner">{error}</div>}

      {plan && (
        <div className="card">
          <h3>Plan</h3>
          <p>
            to add: {plan.to_add_count} · to remove: {plan.to_remove_count} · metadata updates:{' '}
            {plan.to_update_metadata_count} · file updates: {plan.to_update_file_count} · artwork
            updates: {plan.to_update_artwork_count}
          </p>
          <p>
            storage: +{formatBytes(plan.storage.bytes_to_add)} / -
            {formatBytes(plan.storage.bytes_to_remove)} (net{' '}
            {plan.storage.net_change >= 0 ? '+' : ''}
            {formatBytes(plan.storage.net_change)})
          </p>
          {plan.duplicates_count > 0 && (
            <p className="muted">{plan.duplicates_count} duplicate group(s) detected</p>
          )}
          {(plan.playlists_to_add.length > 0 ||
            plan.playlists_to_edit.length > 0 ||
            plan.playlists_to_remove.length > 0) && (
            <p>
              playlists: +{plan.playlists_to_add.length} ~{plan.playlists_to_edit.length} -
              {plan.playlists_to_remove.length}
            </p>
          )}
          {[
            ...plan.unresolved_selections,
            ...plan.unresolved_audiobook_selections,
            ...plan.unresolved_music_selections,
          ].map((sel) => (
            <div key={sel} className="warning-banner">
              Selection {JSON.stringify(sel)} matched 0 files — check for a typo.
            </div>
          ))}

          {plan.to_remove_sample.length > 0 && (
            <div className="field">
              <label>Sample of tracks proposed for removal</label>
              <pre className="code-block">
                {plan.to_remove_sample.join('\n')}
                {plan.to_remove_sample_more > 0 && `\n... and ${plan.to_remove_sample_more} more`}
              </pre>
            </div>
          )}
          {plan.to_add_sample.length > 0 && (
            <div className="field">
              <label>Sample of tracks proposed for addition</label>
              <pre className="code-block">
                {plan.to_add_sample.join('\n')}
                {plan.to_add_sample_more > 0 && `\n... and ${plan.to_add_sample_more} more`}
              </pre>
            </div>
          )}

          {hasRemovals && (
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
              <input
                type="checkbox"
                checked={allowRemovals}
                onChange={(e) => setAllowRemovals(e.target.checked)}
              />
              I've reviewed the above and want to allow removals
            </label>
          )}
        </div>
      )}

      {result && (
        <div className="success-banner">
          {result.summary} — {result.tracks_added} track(s) written,{' '}
          {result.after_track_count} now on device (was {result.before_track_count}).{' '}
          {result.snapshot_id && `Backup snapshot ${result.snapshot_id} available for rollback. `}
          {result.ejected ? 'Device ejected — safe to disconnect.' : ''}
        </div>
      )}

      <AutoSyncSetupCard />
    </>
  )
}
