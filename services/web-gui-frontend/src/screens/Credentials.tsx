import { useEffect, useState } from 'react'
import { api, ApiError, type GlobalConfig, type SourcesStatus } from '../api'
import CookieCaptureForm from '../components/CookieCaptureForm'
import PocketCastsLoginForm from '../components/PocketCastsLoginForm'
import { formatRelativeTime } from '../format'
import type { ProfileStore } from '../useProfileStore'

type OpenForm = 'apple_music' | 'ytmusic-cookies' | 'pocketcasts' | null

export default function Credentials({ store }: { store: ProfileStore }) {
  const { draft } = store
  const [status, setStatus] = useState<SourcesStatus | null>(null)
  const [globalConfig, setGlobalConfig] = useState<GlobalConfig | null>(null)
  const [pcStatus, setPcStatus] = useState<{ exists: boolean; updated_at: number | null } | null>(
    null,
  )
  const [error, setError] = useState<string | null>(null)
  const [openForm, setOpenForm] = useState<OpenForm>(null)

  const reload = async () => {
    setError(null)
    try {
      const [s, g] = await Promise.all([api.getSourcesStatus(), api.getGlobalConfig()])
      setStatus(s)
      setGlobalConfig(g)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
    if (draft) {
      try {
        setPcStatus(await api.getPocketcastsStatus(draft.profile))
      } catch {
        setPcStatus(null)
      }
    } else {
      setPcStatus(null)
    }
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.profile])

  const toggleEnabled = async (source: 'apple_music' | 'ytmusic' | 'spotify', enabled: boolean) => {
    if (!globalConfig) return
    const next: GlobalConfig = {
      ...globalConfig,
      sources: { ...globalConfig.sources, [source]: { ...globalConfig.sources[source], enabled } },
    }
    setGlobalConfig(next) // optimistic -- reload() below reconciles either way
    try {
      await api.putGlobalConfig(next)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      void reload()
    }
  }

  if (error) return <div className="error-banner">{error}</div>
  if (!status || !globalConfig) return <p className="muted">Loading…</p>

  return (
    <>
      <div className="card">
        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', flex: 1 }}>
            <input
              type="checkbox"
              checked={globalConfig.sources.apple_music.enabled}
              onChange={(e) => toggleEnabled('apple_music', e.target.checked)}
            />
            <strong>Apple Music</strong>
          </label>
          <button className="btn secondary" onClick={() => setOpenForm('apple_music')}>
            {status.apple_music.exists ? 'Re-export cookies' : 'Set up cookies'}
          </button>
        </div>
        <p className="muted">
          {status.apple_music.exists
            ? `Cookies saved, updated ${formatRelativeTime(status.apple_music.updated_at)}.`
            : 'No cookies saved yet.'}{' '}
          gamdl wrapper — cookies expire every few weeks.
        </p>
        {openForm === 'apple_music' && (
          <CookieCaptureForm
            sourceName="Apple Music"
            path="config/secrets/apple_music_cookies.txt"
            onSubmit={async (text) => {
              await api.putAppleMusicCookies(text)
              setOpenForm(null)
              await reload()
            }}
          />
        )}
      </div>

      <div className="card">
        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', flex: 1 }}>
            <input
              type="checkbox"
              checked={globalConfig.sources.ytmusic.enabled}
              onChange={(e) => toggleEnabled('ytmusic', e.target.checked)}
            />
            <strong>YouTube Music</strong>
          </label>
          <button className="btn secondary" onClick={() => setOpenForm('ytmusic-cookies')}>
            {status.ytmusic.cookies.exists ? 'Re-export cookies' : 'Set up cookies'}
          </button>
        </div>
        <p className="muted">
          Cookies (required for every download):{' '}
          {status.ytmusic.cookies.exists
            ? `saved, updated ${formatRelativeTime(status.ytmusic.cookies.updated_at)}`
            : 'not saved yet'}
          . OAuth (optional, only for listing your own private playlists):{' '}
          {status.ytmusic.oauth.exists
            ? `saved, updated ${formatRelativeTime(status.ytmusic.oauth.updated_at)}`
            : 'not set up'}
          .
        </p>
        {openForm === 'ytmusic-cookies' && (
          <CookieCaptureForm
            sourceName="YouTube Music"
            path="config/secrets/youtube_cookies.txt"
            onSubmit={async (text) => {
              await api.putYtmusicCookies(text)
              setOpenForm(null)
              await reload()
            }}
          />
        )}
      </div>

      <div className="card">
        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', flex: 1 }}>
            <input
              type="checkbox"
              checked={globalConfig.sources.spotify.enabled}
              onChange={(e) => toggleEnabled('spotify', e.target.checked)}
            />
            <strong>Spotify</strong>
          </label>
        </div>
        <p className="muted">
          Shelved — auth works, but downloads are blocked on a Spotify Premium API requirement
          outside this project's control. Nothing to configure here yet.
        </p>
      </div>

      {draft && (
        <div className="card">
          <div className="row">
            <strong style={{ flex: 1 }}>Pocket Casts — {draft.profile}</strong>
            <button className="btn secondary" onClick={() => setOpenForm('pocketcasts')}>
              {pcStatus?.exists ? 'Update login' : 'Set up login'}
            </button>
          </div>
          <p className="muted">
            {pcStatus?.exists
              ? `Saved, updated ${formatRelativeTime(pcStatus.updated_at)}.`
              : 'Not saved yet.'}{' '}
            Per-profile — each profile has its own Pocket Casts login.
          </p>
          {openForm === 'pocketcasts' && (
            <PocketCastsLoginForm
              path={`config/secrets/pocketcasts/${draft.profile}.json`}
              onSubmit={async (email, password) => {
                await api.putPocketcastsCredentials(draft.profile, email, password)
                setOpenForm(null)
                await reload()
              }}
            />
          )}
        </div>
      )}
      {!draft && (
        <p className="muted">
          Select a profile on the Profiles screen to manage its Pocket Casts login here too.
        </p>
      )}
    </>
  )
}
