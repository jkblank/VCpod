import { useEffect, useState } from 'react'
import { api, ApiError, type PodcastSubscription } from '../api'
import PocketCastsLoginForm from '../components/PocketCastsLoginForm'
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

  const shows = draft.podcasts.shows
  const isAll = shows === 'all'
  const selectedUuids = new Set(
    isAll ? [] : shows.map((s) => (typeof s === 'string' ? s : s.name)),
  )

  const toggle = (uuid: string) => {
    const current = isAll ? [] : [...shows]
    const next = current.includes(uuid) ? current.filter((u) => u !== uuid) : [...current, uuid]
    setDraft({ ...draft, podcasts: { ...draft.podcasts, shows: next } })
  }

  return (
    <>
      <p className="muted">Editing podcasts for profile: {draft.profile}</p>
      {saveErrors && <div className="error-banner">{saveErrors.join('\n')}</div>}
      {loadError && <div className="error-banner">{loadError}</div>}

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
              : `${selectedUuids.size} of ${subscriptions.length} shows selected.`}
          </p>
          {subscriptions.map((s) => (
            <label className="picker-row" key={s.uuid}>
              <input
                type="checkbox"
                checked={isAll || selectedUuids.has(s.uuid)}
                onChange={() => toggle(s.uuid)}
              />
              <span className="name">{s.title}</span>
              <span className="meta">{s.author}</span>
            </label>
          ))}
          <div className="row" style={{ marginTop: '16px' }}>
            <button className="btn" onClick={() => save()} disabled={saving}>
              {saving ? 'Saving…' : 'Save selection'}
            </button>
          </div>
        </>
      )}
    </>
  )
}
