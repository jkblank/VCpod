import { useEffect, useState } from 'react'
import { api, ApiError, type PlaylistEntry, type PlaylistSummary } from '../api'
import CookieCaptureForm from '../components/CookieCaptureForm'
import type { ProfileStore } from '../useProfileStore'

type SourceId = 'apple_music' | 'ytmusic' | 'spotify'

const TABS: { id: SourceId; label: string }[] = [
  { id: 'apple_music', label: 'Apple Music' },
  { id: 'ytmusic', label: 'YouTube Music' },
  { id: 'spotify', label: 'Spotify' },
]

export default function Sources({ store }: { store: ProfileStore }) {
  const { draft, setDraft, save, saving, saveErrors } = store
  const [tab, setTab] = useState<SourceId>('apple_music')
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showCookieForm, setShowCookieForm] = useState(false)

  const loadPlaylists = async (source: SourceId) => {
    if (source === 'spotify') return
    setLoading(true)
    setLoadError(null)
    setPlaylists([])
    try {
      const list =
        source === 'apple_music'
          ? await api.listAppleMusicPlaylists()
          : await api.listYtmusicPlaylists()
      setPlaylists(list)
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setShowCookieForm(false)
    void loadPlaylists(tab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen first — playlists get added to
        whichever profile is currently selected.
      </p>
    )
  }

  const isSelected = (sourceId: string) =>
    draft.playlists.some((p) => p.source === tab && p.source_id === sourceId)

  const toggle = (playlist: PlaylistSummary) => {
    const exists = isSelected(playlist.source_id)
    const next: PlaylistEntry[] = exists
      ? draft.playlists.filter((p) => !(p.source === tab && p.source_id === playlist.source_id))
      : [
          ...draft.playlists,
          {
            name: playlist.name,
            source: tab as 'apple_music' | 'ytmusic',
            source_id: playlist.source_id,
            sync_mode: 'absolute',
            fetch_schedule: null,
          },
        ]
    setDraft({ ...draft, playlists: next })
  }

  const selectedCount = draft.playlists.filter((p) => p.source === tab).length

  return (
    <>
      <p className="muted">Editing playlists for profile: {draft.profile}</p>

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={t.id === tab ? 'tab active' : 'tab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'spotify' && (
        <p className="muted">
          Spotify is shelved — auth works, but downloads are blocked on a Spotify Premium API
          requirement outside this project's control. See services/fetcher-spotify/README.md.
        </p>
      )}

      {tab !== 'spotify' && (
        <>
          {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}
          {loadError && (
            <div className="error-banner">
              {loadError}
              {!showCookieForm && (
                <>
                  {' '}
                  <button className="btn secondary" onClick={() => setShowCookieForm(true)}>
                    Update cookies
                  </button>
                </>
              )}
            </div>
          )}
          {showCookieForm && tab === 'apple_music' && (
            <CookieCaptureForm
              sourceName="Apple Music"
              path="config/secrets/apple_music_cookies.txt"
              onSubmit={async (text) => {
                await api.putAppleMusicCookies(text)
                setShowCookieForm(false)
                await loadPlaylists(tab)
              }}
            />
          )}
          {showCookieForm && tab === 'ytmusic' && (
            <CookieCaptureForm
              sourceName="YouTube Music"
              path="config/secrets/youtube_cookies.txt"
              onSubmit={async (text) => {
                await api.putYtmusicCookies(text)
                setShowCookieForm(false)
                await loadPlaylists(tab)
              }}
            />
          )}

          {loading && <p className="muted">Loading playlists…</p>}
          {!loading && !loadError && playlists.length === 0 && (
            <p className="muted">No playlists found.</p>
          )}
          {playlists.map((p) => (
            <label className="picker-row" key={p.source_id}>
              <input
                type="checkbox"
                checked={isSelected(p.source_id)}
                onChange={() => toggle(p)}
              />
              <span className="name">{p.name}</span>
              <span className="meta">
                {p.track_count} tracks{p.owner ? ` · ${p.owner}` : ''}
              </span>
            </label>
          ))}

          <div className="row" style={{ marginTop: '16px' }}>
            <button className="btn" onClick={() => save()} disabled={saving}>
              {saving ? 'Saving…' : `Save selection (${selectedCount} selected)`}
            </button>
          </div>
        </>
      )}
    </>
  )
}
