import { useEffect, useRef, useState } from 'react'
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
  const [urlInput, setUrlInput] = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolveError, setResolveError] = useState<string | null>(null)
  // Apple Music's playlist listing is a slow, real network call --
  // switching to another tab before it resolves must not let its late
  // response land after the faster tab's own response and overwrite it
  // (confirmed live: this exact race showed Apple Music playlists on
  // the YouTube Music tab). Only the most recently *requested* load's
  // result is ever committed to state.
  const requestIdRef = useRef(0)

  const loadPlaylists = async (source: SourceId) => {
    if (source === 'spotify' || !draft) return
    const requestId = ++requestIdRef.current
    setLoading(true)
    setLoadError(null)
    setPlaylists([])
    try {
      // Profile-scoped: resolves this profile's own override credentials
      // if it has any, falling back to the shared global login otherwise
      // -- so a profile with its own separate account sees its own
      // playlists here, not necessarily the household's. See
      // routers/profile_sources.py.
      const list =
        source === 'apple_music'
          ? await api.listProfileAppleMusicPlaylists(draft.profile)
          : await api.listProfileYtmusicPlaylists(draft.profile)
      if (requestId !== requestIdRef.current) return // superseded by a newer tab switch
      setPlaylists(list)
    } catch (e) {
      if (requestId !== requestIdRef.current) return
      setLoadError(e instanceof ApiError ? e.message : String(e))
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    setShowCookieForm(false)
    void loadPlaylists(tab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, draft?.profile])

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

  const resolveByUrl = async () => {
    setResolving(true)
    setResolveError(null)
    try {
      const summary = await api.resolveYtmusicPlaylist(urlInput)
      setPlaylists((prev) =>
        prev.some((p) => p.source_id === summary.source_id) ? prev : [summary, ...prev],
      )
      if (!isSelected(summary.source_id)) toggle(summary)
      setUrlInput('')
    } catch (e) {
      setResolveError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setResolving(false)
    }
  }

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
              path={`config/secrets/${draft.profile}/apple_music_cookies.txt`}
              onSubmit={async (text) => {
                // Always writes this profile's own override, never the
                // shared global file -- re-exporting fresh cookies for a
                // specific profile from here is an explicit "just for
                // this profile" action, same principle as the
                // Credentials screen's "set up separate credentials".
                await api.putProfileAppleMusicCookies(draft.profile, text)
                setShowCookieForm(false)
                await loadPlaylists(tab)
              }}
            />
          )}
          {showCookieForm && tab === 'ytmusic' && (
            <CookieCaptureForm
              sourceName="YouTube Music"
              path={`config/secrets/${draft.profile}/youtube_cookies.txt`}
              onSubmit={async (text) => {
                await api.putProfileYtmusicCookies(draft.profile, text)
                setShowCookieForm(false)
                await loadPlaylists(tab)
              }}
            />
          )}

          {tab === 'ytmusic' && (
            <div className="card">
              <h3>Add a public playlist by link</h3>
              <p className="muted">
                For playlists shared with you but not saved to your own account — paste a
                youtube.com/music.youtube.com share link (or a bare playlist ID). Works for any
                public playlist, no login needed.
              </p>
              {resolveError && <div className="error-banner">{resolveError}</div>}
              <div className="row">
                <input
                  style={{ flex: 1 }}
                  placeholder="https://music.youtube.com/playlist?list=..."
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                />
                <button
                  className="btn secondary"
                  onClick={resolveByUrl}
                  disabled={resolving || !urlInput.trim()}
                >
                  {resolving ? 'Resolving…' : 'Add'}
                </button>
              </div>
            </div>
          )}

          {loading && <p className="muted">Loading playlists…</p>}
          {!loading && !loadError && playlists.length === 0 && (
            <p className="muted">No playlists found.</p>
          )}
          {playlists.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th></th>
                  <th>Name</th>
                  <th>Tracks</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {playlists.map((p) => (
                  <tr key={p.source_id} onClick={() => toggle(p)} style={{ cursor: 'pointer' }}>
                    <td>
                      <input
                        type="checkbox"
                        checked={isSelected(p.source_id)}
                        onChange={() => toggle(p)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                    <td>{p.name}</td>
                    <td>{p.track_count}</td>
                    <td>{p.owner ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

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
