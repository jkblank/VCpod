import { useState, type ReactElement } from 'react'
import Audiobooks from './screens/Audiobooks'
import Credentials from './screens/Credentials'
import ExternalLibrary from './screens/ExternalLibrary'
import Overview from './screens/Overview'
import Podcasts from './screens/Podcasts'
import Profiles from './screens/Profiles'
import Sources from './screens/Sources'
import Sync from './screens/Sync'
import { useProfileStore } from './useProfileStore'
import {
  AudiobookIcon,
  CredentialsIcon,
  ExternalLibraryIcon,
  Mark,
  PodcastIcon,
  ProfileIcon,
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
}

export default function App() {
  const [screen, setScreen] = useState<ScreenId>('overview')
  const store = useProfileStore()

  return (
    <div className="shell">
      <nav className="nav">
        <div className="nav-title">
          <Mark size={25} />
          VCpod
        </div>
        {(Object.keys(SCREENS) as ScreenId[]).map((id) => {
          const Icon = SCREENS[id].icon
          return (
            <button
              key={id}
              className={id === screen ? 'nav-item active' : 'nav-item'}
              onClick={() => setScreen(id)}
            >
              <Icon size={20} className="nav-item-icon" />
              {SCREENS[id].label}
            </button>
          )
        })}
        {store.draft && (
          <div className="nav-editing">
            editing: <strong>{store.draft.profile}</strong>
          </div>
        )}
      </nav>
      <main className="main">
        <header className="page-header">
          <h1>{SCREENS[screen].label}</h1>
          <p>{SCREENS[screen].blurb}</p>
        </header>
        {screen === 'overview' && <Overview />}
        {screen === 'profiles' && <Profiles store={store} />}
        {screen === 'sources' && <Sources store={store} />}
        {screen === 'podcasts' && <Podcasts store={store} />}
        {screen === 'external_library' && <ExternalLibrary store={store} />}
        {screen === 'audiobooks' && <Audiobooks store={store} />}
        {screen === 'credentials' && <Credentials store={store} />}
        {screen === 'sync' && <Sync store={store} />}
      </main>
    </div>
  )
}
