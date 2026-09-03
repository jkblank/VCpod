import { useEffect, useState, type ReactElement } from 'react'
import { api, type AutoSyncSetup } from './api'
import Activity from './screens/Activity'
import Audiobooks from './screens/Audiobooks'
import Credentials from './screens/Credentials'
import ExternalLibrary from './screens/ExternalLibrary'
import Overview from './screens/Overview'
import Podcasts from './screens/Podcasts'
import Profiles from './screens/Profiles'
import Sources from './screens/Sources'
import Sync from './screens/Sync'
import Dialog from './components/Dialog'
import { useConnectedDevices } from './useConnectedDevices'
import { useProfileStore } from './useProfileStore'
import { toYamlish } from './yamlish'
import {
  AudiobookIcon,
  CredentialsIcon,
  ExternalLibraryIcon,
  Mark,
  PodcastIcon,
  ProfileIcon,
  ScheduledIcon,
  StreamingPlaylistIcon,
  SyncedIcon,
  type IconProps,
} from './icons'
import './App.css'

type ScreenId =
  | 'overview'
  | 'profiles'
  | 'sources'
  | 'podcasts'
  | 'external_library'
  | 'audiobooks'
  | 'credentials'
  | 'sync'
  | 'activity'

const SCREENS: Record<ScreenId, { label: string; blurb: string; icon: (props: IconProps) => ReactElement }> = {
  overview: {
    label: 'Overview',
    blurb: 'Read from config/ and state/ — this console never keeps its own copy.',
    icon: Mark,
  },
  profiles: {
    label: 'Profiles',
    blurb: 'One YAML file per person and iPod. Adding someone is a new file — no code changes.',
    icon: ProfileIcon,
  },
  sources: {
    label: 'Music sources',
    blurb: "Your accounts' own playlists, listed through each fetcher. Tick what should sync.",
    icon: StreamingPlaylistIcon,
  },
  podcasts: {
    label: 'Podcasts',
    blurb:
      'Pocket Casts stays the source of truth for subscriptions and played state. This only picks which shows land on the device.',
    icon: PodcastIcon,
  },
  external_library: {
    label: 'External library',
    blurb: 'Sync a subset of a personal music folder that lives outside the managed library.',
    icon: ExternalLibraryIcon,
  },
  audiobooks: {
    label: 'Audiobooks',
    blurb: 'Merged, chaptered .m4b files under library/audiobooks.',
    icon: AudiobookIcon,
  },
  credentials: {
    label: 'Sources & credentials',
    blurb: 'Global enable flags and credential health, plus the current profile’s Pocket Casts login.',
    icon: CredentialsIcon,
  },
  sync: {
    label: 'Sync',
    blurb: 'Compute a real sync plan, review it, and write it to a connected device.',
    icon: SyncedIcon,
  },
  activity: {
    label: 'Activity',
    blurb: 'What fetch-scheduler and sync-orchestrator actually did, newest first.',
    icon: ScheduledIcon,
  },
}

const NAV_GROUPS: { label: string; items: ScreenId[] }[] = [
  { label: 'Overview', items: ['overview'] },
  { label: 'Library', items: ['sources', 'external_library', 'podcasts', 'audiobooks'] },
  { label: 'Device', items: ['sync', 'activity'] },
  { label: 'Setup', items: ['profiles', 'credentials'] },
]

const GROUP_LABEL_BY_SCREEN: Record<ScreenId, string> = NAV_GROUPS.reduce(
  (acc, group) => {
    for (const id of group.items) acc[id] = group.label
    return acc
  },
  {} as Record<ScreenId, string>,
)

// Deterministic per-profile color for the sidebar picker's dot -- no
// per-profile color field exists in config, so this is display-only,
// derived from the name itself (stable across reloads, no state needed).
function profileColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) % 360
  return `hsl(${hash}, 55%, 62%)`
}

function StatusFooter() {
  const [setup, setSetup] = useState<AutoSyncSetup | null>(null)
  const [setupError, setSetupError] = useState(false)
  const { devices } = useConnectedDevices()

  useEffect(() => {
    api
      .getAutoSyncSetup()
      .then(setSetup)
      .catch(() => setSetupError(true))
  }, [])

  const armed = !!setup && setup.status.systemd_installed && setup.status.udev_rule_installed

  return (
    <div className="nav-status">
      <div className="nav-status-row">
        <span className={`status-dot ${armed ? 'ok' : 'off'}`} />
        {setupError ? 'auto-sync: unknown' : armed ? 'auto-sync armed' : 'auto-sync not installed'}
      </div>
      <div className="nav-status-row">
        <span className={`status-dot ${devices && devices.length > 0 ? 'ok' : 'off'}`} />
        {devices == null
          ? 'checking device…'
          : devices.length > 0
            ? `${devices.length} device${devices.length === 1 ? '' : 's'} connected`
            : 'no device connected'}
      </div>
    </div>
  )
}

export default function App() {
  const [screen, setScreen] = useState<ScreenId>('overview')
  const [yamlOpen, setYamlOpen] = useState(false)
  const store = useProfileStore()

  const badges: Partial<Record<ScreenId, number>> = {
    sources: store.draft ? store.draft.playlists.length : undefined,
    profiles: Object.keys(store.profiles).length || undefined,
  }

  return (
    <div className="shell">
      <nav className="nav">
        <div className="nav-title">
          <Mark size={30} />
          VCpod
        </div>

        <div className="nav-group-label">Profile</div>
        <div className="nav-profile-picker">
          {Object.keys(store.profiles).length === 0 && (
            <div className="nav-profile-empty muted">no profiles yet</div>
          )}
          {Object.keys(store.profiles)
            .sort()
            .map((name) => (
              <button
                key={name}
                className={`nav-profile-row${store.selected === name ? ' active' : ''}`}
                onClick={() => store.select(name)}
              >
                <span className="profile-dot" style={{ background: profileColor(name) }} />
                {name}
              </button>
            ))}
        </div>

        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="nav-group-label">{group.label}</div>
            {group.items.map((id) => {
              const Icon = SCREENS[id].icon
              const badge = badges[id]
              return (
                <button
                  key={id}
                  className={id === screen ? 'nav-item active' : 'nav-item'}
                  onClick={() => setScreen(id)}
                >
                  <Icon size={20} className="nav-item-icon" />
                  <span style={{ flex: 1 }}>{SCREENS[id].label}</span>
                  {badge !== undefined && <span className="tag tag-outline">{badge}</span>}
                </button>
              )
            })}
          </div>
        ))}

        {store.draft && (
          <div className="nav-editing">
            editing: <strong>{store.draft.profile}</strong>
          </div>
        )}
        <StatusFooter />
      </nav>
      <main className="main">
        <header className="page-header">
          <div className="breadcrumb">{GROUP_LABEL_BY_SCREEN[screen]}</div>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h1>{SCREENS[screen].label}</h1>
              <p>{SCREENS[screen].blurb}</p>
            </div>
            <div className="row" style={{ marginBottom: 0, flexShrink: 0 }}>
              {store.draft && (
                <button className="btn ghost" onClick={() => setYamlOpen(true)}>
                  View YAML
                </button>
              )}
              {screen !== 'sync' && (
                <button className="btn secondary" onClick={() => setScreen('sync')}>
                  Sync now
                </button>
              )}
            </div>
          </div>
        </header>
        {screen === 'overview' && <Overview />}
        {screen === 'profiles' && <Profiles store={store} />}
        {screen === 'sources' && <Sources store={store} />}
        {screen === 'podcasts' && <Podcasts store={store} />}
        {screen === 'external_library' && <ExternalLibrary store={store} />}
        {screen === 'audiobooks' && <Audiobooks store={store} />}
        {screen === 'credentials' && <Credentials store={store} />}
        {screen === 'sync' && <Sync store={store} />}
        {screen === 'activity' && <Activity />}
      </main>
      {yamlOpen && store.draft && (
        <Dialog title={`${store.draft.profile}.yaml`} onClose={() => setYamlOpen(false)}>
          <pre className="code-block">{toYamlish(store.draft)}</pre>
        </Dialog>
      )}
    </div>
  )
}
