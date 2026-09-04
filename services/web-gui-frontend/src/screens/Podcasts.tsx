import { useEffect, useState } from 'react'
import { api, ApiError, type PodcastSubscription } from '../api'
import PocketCastsLoginForm from '../components/PocketCastsLoginForm'
import ScheduleEditor from '../components/ScheduleEditor'
import type { ProfileStore } from '../useProfileStore'

export default function Podcasts({ store }: { store: ProfileStore }) {
  const { draft, setDraft, save, saving, saveErrors } = store
  const [subscriptions, setSubscriptions] = useState<PodcastSubscription[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [needsCredentials, setNeedsCredentials] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const loadSubscriptions = async (profileName: string) => {
    setLoading(true)
    setLoadError(null)
    setNeedsCredentials(false)
    setSubscriptions(null)
    try {
      const subs = await api.getPocketcastsSubscriptions(profileName)
      setSubscriptions(subs)
    } catch (e) {
      if (e instanceof ApiError && e.message.includes('not saved yet')) {
        setNeedsCredentials(true)
      } else {
        setLoadError(e instanceof ApiError ? e.message : String(e))
        setNeedsCredentials(true) // also offer re-entering credentials on any other failure
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (draft) void loadSubscriptions(draft.profile)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.profile])

  if (!draft) {
    return (
      <p className="no-profile-notice">
        Select or create a profile on the Profiles screen first — shows get added to whichever
        profile is currently selected.
      </p>
    )
  }

  const podcasts = draft.podcasts
  const shows = podcasts.shows
  const isAll = shows === 'all'
  const selectedUuids = new Set(
    isAll ? [] : shows.map((s) => (typeof s === 'string' ? s : s.name)),
  )

  const setPodcasts = (patch: Partial<typeof podcasts>) =>
    setDraft({ ...draft, podcasts: { ...podcasts, ...patch } })

  const toggle = (uuid: string) => {
    const current = isAll ? [] : [...shows]
    const next = current.includes(uuid) ? current.filter((u) => u !== uuid) : [...current, uuid]
    setPodcasts({ shows: next })
  }

  const setFillMode = (uuid: string, mode: 'newest' | 'next') =>
    setPodcasts({ fill_modes: { ...podcasts.fill_modes, [uuid]: mode } })

  return (
    <>
      <p className="muted">Editing podcasts for profile: {draft.profile}</p>
      {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}
      {loadError && <div className="error-banner">{loadError}</div>}

      <div className="card">
        <h3>Settings</h3>
        <div className="field">
          <label>Episode filter (what counts as "done" for sync_unplayed_only)</label>
          <select
            value={podcasts.episode_filter}
            onChange={(e) =>
              setPodcasts({ episode_filter: e.target.value as 'played' | 'archived' })
            }
          >
            <option value="played">Played (Pocket Casts playingStatus)</option>
            <option value="archived">Archived</option>
          </select>
        </div>
        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
            <input
              type="checkbox"
              checked={podcasts.sync_unplayed_only}
              onChange={(e) => setPodcasts({ sync_unplayed_only: e.target.checked })}
            />
            Only sync unplayed/unarchived episodes
          </label>
        </div>
        <div className="row">
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
            <input
              type="checkbox"
              checked={podcasts.delete_played_episodes}
              onChange={(e) => setPodcasts({ delete_played_episodes: e.target.checked })}
            />
            Delete played episodes locally (only applies when the above is on)
          </label>
        </div>
        <div className="field">
          <label>Max episodes per show</label>
          <input
            type="number"
            min={1}
            value={podcasts.max_episodes_per_show}
            onChange={(e) =>
              setPodcasts({ max_episodes_per_show: Number(e.target.value) || 1 })
            }
          />
        </div>
        <ScheduleEditor
          value={podcasts.fetch_schedule}
          onChange={(schedule) => setPodcasts({ fetch_schedule: schedule })}
        />
      </div>

      {needsCredentials && (
        <PocketCastsLoginForm
          path={`config/secrets/pocketcasts/${draft.profile}.json`}
          onSubmit={async (email, password) => {
            await api.putPocketcastsCredentials(draft.profile, email, password)
            await loadSubscriptions(draft.profile)
          }}
        />
      )}

      {loading && <p className="muted">Loading subscriptions…</p>}

      {subscriptions && (
        <>
          <p className="muted">
            {isAll
              ? 'Currently syncing all subscribed shows. Tick specific shows to switch to a curated list.'
              : `${selectedUuids.size} of ${subscriptions.length} shows selected. Fill mode only applies to selected shows.`}
          </p>
          <table className="table">
            <thead>
              <tr>
                <th></th>
                <th>Show</th>
                <th>Author</th>
                <th>Fill mode</th>
              </tr>
            </thead>
            <tbody>
              {subscriptions.map((s) => {
                const selected = isAll || selectedUuids.has(s.uuid)
                return (
                  <tr key={s.uuid} onClick={() => toggle(s.uuid)} style={{ cursor: 'pointer' }}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggle(s.uuid)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                    <td>{s.title}</td>
                    <td>{s.author}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {selected && !isAll && (
                        <select
                          value={podcasts.fill_modes[s.uuid] ?? 'newest'}
                          onChange={(e) => setFillMode(s.uuid, e.target.value as 'newest' | 'next')}
                        >
                          <option value="newest">Newest first</option>
                          <option value="next">Oldest unheard first</option>
                        </select>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: '16px' }}>
            <button className="btn" onClick={() => save()} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </>
      )}
    </>
  )
}
