import { useEffect, useRef, useState } from 'react'
import { api, type SyncStatus } from '../api'
import AutoSyncSetupCard from '../components/AutoSyncSetupCard'
import Dialog from '../components/Dialog'
import {
  DeviceConnectedIcon,
  IdleIcon,
  Spinner,
  SyncedIcon,
  ToAddIcon,
  ToRemoveIcon,
  UnreachableIcon,
} from '../icons'
import { useConnectedDevices } from '../useConnectedDevices'
import type { ProfileStore } from '../useProfileStore'
import type { SyncSessions } from '../useSyncSessions'

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

export default function Sync({ store, sync }: { store: ProfileStore; sync: SyncSessions }) {
  const { draft } = store
  const { devices, error: deviceError } = useConnectedDevices()
  // Owned by App.tsx, keyed by profile -- survives this component
  // unmounting when you switch screens mid-sync (see useSyncSessions.ts).
  const session = draft ? sync.getSession(draft.profile) : null
  const { runningAction, log, plan, result, error } = session ?? {
    runningAction: null,
    log: [],
    plan: null,
    result: null,
    error: null,
  }
  const [allowRemovals, setAllowRemovals] = useState(false)
  const [dangerousMode, setDangerousMode] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [externalStatus, setExternalStatus] = useState<SyncStatus | null>(null)
  const logRef = useRef<HTMLPreElement | null>(null)

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  // Catches a sync we didn't ourselves start streaming: a fresh page
  // load/reload, a different browser/session, or a headless auto-sync
  // run (udev-triggered, never goes through this backend's own
  // /api/sync/execute at all -- see sync_status.py). Only polls while we
  // have no live SSE session of our own for this profile; once we do,
  // that's already a strictly better source of truth than polling.
  useEffect(() => {
    if (!draft || runningAction !== null) {
      setExternalStatus(null)
      return
    }
    let cancelled = false
    const poll = () => {
      api
        .getSyncStatus(draft.profile)
        .then((s) => !cancelled && setExternalStatus(s))
        .catch(() => !cancelled && setExternalStatus(null))
    }
    poll()
    const interval = window.setInterval(poll, 5000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [draft?.profile, runningAction])

  const connected =
    draft && devices
      ? devices.find((d) =>
          draft.device.match_by === 'serial'
            ? d.serial === draft.device.match_value
            : d.volume_label === draft.device.match_value,
        )
      : null

  const resetLocal = () => setAllowRemovals(false)

  const resetAll = () => {
    resetLocal()
    if (draft) sync.resetSession(draft.profile)
  }

  const computePlan = () => {
    if (!draft) return
    resetLocal()
    void sync.computePlan(draft.profile)
  }

  const execute = () => {
    if (!draft) return
    void sync.execute(draft.profile, allowRemovals)
  }

  const dangerousSync = () => {
    if (!draft) return
    resetLocal()
    void sync.dangerousSync(draft.profile)
  }

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen to sync its device.
      </p>
    )
  }

  if (draft.sync.mode === 'rockbox') {
    return (
      <p className="no-profile-notice">
        Rockbox mode — coming soon. sync-orchestrator doesn't have <code>--json</code> output for
        Rockbox mode yet, so this screen can't drive it. Use the CLI (
        <code>sync-orchestrator sync --profile {draft.profile} ...</code>) for now.
      </p>
    )
  }

  const hasRemovals = plan != null && (plan.to_remove_count > 0 || plan.playlists_to_remove.length > 0)
  const externalRunning = externalStatus?.running ?? false
  const running = runningAction !== null || externalRunning

  return (
    <>
      <div className="card">
        <p style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {deviceError ? (
            <UnreachableIcon size={18} />
          ) : connected ? (
            <DeviceConnectedIcon size={18} />
          ) : (
            <IdleIcon size={18} />
          )}
          <span>
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
          </span>
        </p>

        {externalRunning && (
          <div className="warning-banner">
            <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Spinner size={14} />A sync for <strong>{draft.profile}</strong> is already running —
              started elsewhere (a headless auto-sync, or another session). Waiting for it to
              finish before you can start another.
            </span>
            {externalStatus?.log_tail && (
              <pre className="sync-log" style={{ marginTop: '8px', marginBottom: 0 }}>
                {externalStatus.log_tail.join('\n')}
              </pre>
            )}
          </div>
        )}

        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
            <input
              type="checkbox"
              checked={dangerousMode}
              onChange={(e) => {
                setDangerousMode(e.target.checked)
                resetAll()
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
            <button
              className="btn danger"
              onClick={dangerousSync}
              disabled={running}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}
            >
              {runningAction === 'dangerous' && <Spinner size={14} />}
              {runningAction === 'dangerous' ? 'Syncing…' : 'Sync now (dangerous)'}
            </button>
          </>
        ) : (
          <div className="row">
            <button
              className="btn"
              onClick={computePlan}
              disabled={running}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}
            >
              {runningAction === 'plan' && <Spinner size={14} />}
              {runningAction === 'plan' ? 'Computing…' : 'Compute plan'}
            </button>
            <button
              className="btn"
              onClick={() => setConfirmOpen(true)}
              disabled={running || !plan || (hasRemovals && !allowRemovals)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}
            >
              {runningAction === 'execute' && <Spinner size={14} />}
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
          <div className="stat-row" style={{ marginBottom: '12px' }}>
            <div className="stat">
              <div className="value">{plan.to_add_count}</div>
              <div className="label">to add</div>
            </div>
            <div className="stat">
              <div className="value">{plan.to_remove_count}</div>
              <div className="label">to remove</div>
            </div>
            <div className="stat">
              <div className="value">{plan.before_track_count - plan.to_remove_count}</div>
              <div className="label">unchanged</div>
            </div>
            <div className="stat">
              <div className="value">
                {plan.storage.net_change >= 0 ? '+' : ''}
                {formatBytes(plan.storage.net_change)}
              </div>
              <div className="label">net storage change</div>
            </div>
          </div>
          <p style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
            <ToAddIcon size={16} /> to add: {plan.to_add_count}
            <span style={{ marginLeft: '8px' }} />
            <ToRemoveIcon size={16} /> to remove: {plan.to_remove_count} · metadata updates:{' '}
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
        <div className="success-banner" style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
          <SyncedIcon size={18} className="success-banner-icon" />
          <span>
            {result.summary} — {result.tracks_added} track(s) written,{' '}
            {result.after_track_count} now on device (was {result.before_track_count}).{' '}
            {result.snapshot_id && `Backup snapshot ${result.snapshot_id} available for rollback. `}
            {result.ejected ? 'Device ejected — safe to disconnect.' : ''}
          </span>
        </div>
      )}

      {confirmOpen && plan && (
        <Dialog
          title="Confirm sync"
          onClose={() => setConfirmOpen(false)}
          actions={
            <>
              <button className="btn secondary" onClick={() => setConfirmOpen(false)}>
                Cancel
              </button>
              <button
                className="btn"
                onClick={() => {
                  setConfirmOpen(false)
                  void execute()
                }}
              >
                Execute sync
              </button>
            </>
          }
        >
          <p>
            Writes to the device for <strong>{draft.profile}</strong>: {plan.to_add_count} to add,{' '}
            {plan.to_remove_count} to remove
            {allowRemovals ? '' : ' (removals not allowed this run)'}.
          </p>
          {hasRemovals && allowRemovals && (
            <p style={{ color: 'var(--danger)' }}>
              Removals are allowed for this run — anything no longer in scope is deleted from the
              device.
            </p>
          )}
        </Dialog>
      )}

      <AutoSyncSetupCard />
    </>
  )
}
