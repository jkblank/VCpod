import { useEffect, useState } from 'react'
import { api, ApiError, type AutoSyncSetup } from '../api'

// One-time-ish setup, not a config edit -- shows generated files +
// copy-paste commands only. No "install for me" button anywhere: this
// backend never executes anything privileged, see web-gui-backend/
// README.md's Security posture section.
export default function AutoSyncSetupCard() {
  const [setup, setSetup] = useState<AutoSyncSetup | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const load = async () => {
    setError(null)
    try {
      setSetup(await api.getAutoSyncSetup())
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (open && !setup) void load()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="card">
      <div className="row">
        <h3 style={{ flex: 1, margin: 0 }}>Auto-sync setup</h3>
        <button className="btn secondary" onClick={() => setOpen((o) => !o)}>
          {open ? 'Hide' : 'Show'}
        </button>
      </div>
      <p className="muted">
        Unattended sync whenever your iPod is plugged in — a udev rule + systemd service, filled
        in with this install's real paths. Never installed automatically: copy the two files into
        place yourself with the commands below.
      </p>

      {open && (
        <>
          {error && <div className="error-banner">{error}</div>}
          {!setup && !error && <p className="muted">Loading…</p>}
          {setup && (
            <>
              <p>
                systemd service:{' '}
                <strong>{setup.status.systemd_installed ? 'installed' : 'not installed'}</strong>
                {' · '}
                udev rule:{' '}
                <strong>{setup.status.udev_rule_installed ? 'installed' : 'not installed'}</strong>
              </p>

              <div className="field">
                <label>music-stack-auto-sync.service</label>
                <pre className="code-block">{setup.systemd_unit}</pre>
              </div>
              <div className="field">
                <label>99-ipod-music-stack.rules</label>
                <pre className="code-block">{setup.udev_rule}</pre>
              </div>

              <div className="warning-banner">
                The udev rule above only matches a 5th/5.5th generation iPod Video (the one
                device this project has confirmed live). A different generation needs its own
                USB <code>idVendor</code>/<code>idProduct</code> — connect it and run{' '}
                <code>lsusb</code>, then edit the rule before installing.
              </div>

              <div className="field">
                <label>Install commands (run these yourself)</label>
                <pre className="code-block">{setup.install_commands.join('\n')}</pre>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
