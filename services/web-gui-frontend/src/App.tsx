import { useState } from 'react'
import Overview from './screens/Overview'
import Profiles from './screens/Profiles'
import './App.css'

type ScreenId = 'overview' | 'profiles'

const SCREENS: Record<ScreenId, { label: string; blurb: string }> = {
  overview: {
    label: 'Overview',
    blurb: 'Read from config/ and state/ — this console never keeps its own copy.',
  },
  profiles: {
    label: 'Profiles',
    blurb: 'One YAML file per person and iPod. Adding someone is a new file — no code changes.',
  },
}

export default function App() {
  const [screen, setScreen] = useState<ScreenId>('overview')

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
      </nav>
      <main className="main">
        <header className="page-header">
          <h1>{SCREENS[screen].label}</h1>
          <p>{SCREENS[screen].blurb}</p>
        </header>
        {screen === 'overview' && <Overview />}
        {screen === 'profiles' && <Profiles />}
      </main>
    </div>
  )
}
