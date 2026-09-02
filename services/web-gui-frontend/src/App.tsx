import { useState } from 'react'
import Overview from './screens/Overview'
import Podcasts from './screens/Podcasts'
import Profiles from './screens/Profiles'
import Sources from './screens/Sources'
import { useProfileStore } from './useProfileStore'
import './App.css'

type ScreenId = 'overview' | 'profiles' | 'sources' | 'podcasts'

const SCREENS: Record<ScreenId, { label: string; blurb: string }> = {
  overview: {
    label: 'Overview',
    blurb: 'Read from config/ and state/ — this console never keeps its own copy.',
  },
  profiles: {
    label: 'Profiles',
    blurb: 'One YAML file per person and iPod. Adding someone is a new file — no code changes.',
  },
  sources: {
    label: 'Music sources',
    blurb: "Your accounts' own playlists, listed through each fetcher. Tick what should sync.",
  },
  podcasts: {
    label: 'Podcasts',
    blurb:
      'Pocket Casts stays the source of truth for subscriptions and played state. This only picks which shows land on the device.',
  },
}

export default function App() {
  const [screen, setScreen] = useState<ScreenId>('overview')
  const store = useProfileStore()

  return (
    <div className="shell">
      <nav className="nav">
        <div className="nav-title">VCpod</div>
        {(Object.keys(SCREENS) as ScreenId[]).map((id) => (
          <button
            key={id}
            className={id === screen ? 'nav-item active' : 'nav-item'}
            onClick={() => setScreen(id)}
          >
            {SCREENS[id].label}
          </button>
        ))}
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
      </main>
    </div>
  )
}
