import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  type OneOffEpisode,
  type PodcastRssEpisode,
  type PodcastSearchResult,
} from '../api'

type Props = { profileName: string }

function formatDuration(seconds: number | null): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function formatDate(value: string): string {
  if (!value) return ''
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString()
}

// Search for, and download a single episode of, a show the profile is
// NOT subscribed to in Pocket Casts -- entirely independent of Pocket
// Casts (powered by iTunes Search + the show's own RSS feed). See
// services/podcast-manager/README.md for why these use a synthetic
// per-episode identity instead of a Pocket Casts one.
export default function PodcastSearch({ profileName }: Props) {
  const [term, setTerm] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [results, setResults] = useState<PodcastSearchResult[] | null>(null)

  const [selectedShow, setSelectedShow] = useState<PodcastSearchResult | null>(null)
  const [episodes, setEpisodes] = useState<PodcastRssEpisode[] | null>(null)
  const [episodesLoading, setEpisodesLoading] = useState(false)
  const [episodesError, setEpisodesError] = useState<string | null>(null)

  const [downloading, setDownloading] = useState<Set<string>>(new Set())
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({})

  const [oneOff, setOneOff] = useState<OneOffEpisode[] | null>(null)
  const [oneOffError, setOneOffError] = useState<string | null>(null)

  const loadOneOff = async () => {
    try {
      setOneOff(await api.getOneOffEpisodes(profileName))
      setOneOffError(null)
    } catch (e) {
      setOneOffError(e instanceof ApiError ? e.message : String(e))
    }
  }

  useEffect(() => {
    void loadOneOff()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileName])

  const search = async () => {
    if (!term.trim()) return
    setSearching(true)
    setSearchError(null)
    setSelectedShow(null)
    setEpisodes(null)
    try {
      setResults(await api.searchPodcasts(term))
    } catch (e) {
      setSearchError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  const selectShow = async (show: PodcastSearchResult) => {
    setSelectedShow(show)
    setEpisodes(null)
    setEpisodesError(null)
    setEpisodesLoading(true)
    try {
      setEpisodes(await api.getPodcastEpisodes(show.feed_url))
    } catch (e) {
      setEpisodesError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setEpisodesLoading(false)
    }
  }

  const download = async (episode: PodcastRssEpisode) => {
    if (!selectedShow) return
    setDownloading((prev) => new Set(prev).add(episode.enclosure_url))
    setDownloadErrors((prev) => {
      const next = { ...prev }
      delete next[episode.enclosure_url]
      return next
    })
    try {
      await api.downloadOneOffEpisode(profileName, {
        show_title: selectedShow.title,
        feed_url: selectedShow.feed_url,
        episode,
      })
      await loadOneOff()
    } catch (e) {
      setDownloadErrors((prev) => ({
        ...prev,
        [episode.enclosure_url]: e instanceof ApiError ? e.message : String(e),
      }))
    } finally {
      setDownloading((prev) => {
        const next = new Set(prev)
        next.delete(episode.enclosure_url)
        return next
      })
    }
  }

  const remove = async (episodeUuid: string) => {
    try {
      await api.deleteOneOffEpisode(profileName, episodeUuid)
      await loadOneOff()
    } catch (e) {
      setOneOffError(e instanceof ApiError ? e.message : String(e))
    }
  }

  const downloadedUuids = new Set(oneOff?.map((e) => e.audio_url) ?? [])

  return (
    <>
      <div className="card">
        <h3>Search for a show</h3>
        <p className="muted">
          Not tied to your Pocket Casts account — grab a single episode of anything, without
          subscribing. Works even if you haven't saved Pocket Casts credentials for this profile.
        </p>
        <div className="row">
          <input
            type="text"
            placeholder="Show title…"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void search()}
          />
          <button className="btn" onClick={() => void search()} disabled={searching || !term.trim()}>
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>
        {searchError && <div className="error-banner">{searchError}</div>}

        {results && results.length === 0 && <p className="muted">No shows found.</p>}

        {results && results.length > 0 && !selectedShow && (
          <table className="table">
            <thead>
              <tr>
                <th>Show</th>
                <th>Author</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((show) => (
                <tr key={show.collection_id} onClick={() => void selectShow(show)} style={{ cursor: 'pointer' }}>
                  <td>{show.title}</td>
                  <td>{show.author}</td>
                  <td>
                    <button className="btn secondary" onClick={() => void selectShow(show)}>
                      View episodes
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {selectedShow && (
          <>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <p className="muted">
                Episodes of <strong>{selectedShow.title}</strong>
              </p>
              <button className="btn ghost" onClick={() => setSelectedShow(null)}>
                Back to results
              </button>
            </div>
            {episodesLoading && <p className="muted">Loading episodes…</p>}
            {episodesError && <div className="error-banner">{episodesError}</div>}
            {episodes && episodes.length === 0 && (
              <p className="muted">Could not load this show's episode list.</p>
            )}
            {episodes && episodes.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Episode</th>
                    <th>Published</th>
                    <th>Duration</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {episodes.map((ep) => {
                    const isDownloading = downloading.has(ep.enclosure_url)
                    const alreadyDownloaded = downloadedUuids.has(ep.enclosure_url)
                    const error = downloadErrors[ep.enclosure_url]
                    return (
                      <tr key={ep.enclosure_url}>
                        <td>{ep.title}</td>
                        <td>{formatDate(ep.published ?? '')}</td>
                        <td>{formatDuration(ep.duration_seconds)}</td>
                        <td>
                          {alreadyDownloaded ? (
                            <span className="tag tag-outline">Downloaded</span>
                          ) : (
                            <button
                              className="btn secondary"
                              onClick={() => void download(ep)}
                              disabled={isDownloading}
                            >
                              {isDownloading ? 'Downloading…' : 'Download'}
                            </button>
                          )}
                          {error && <div className="error-banner">{error}</div>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h3>One-off downloads</h3>
        <p className="muted">
          Episodes grabbed via search above, for this profile. Not tracked by Pocket Casts and
          never automatically pruned — remove them here when you're done with them.
        </p>
        {oneOffError && <div className="error-banner">{oneOffError}</div>}
        {oneOff && oneOff.length === 0 && <p className="muted">None yet.</p>}
        {oneOff && oneOff.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Show</th>
                <th>Episode</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {oneOff.map((ep) => (
                <tr key={ep.episode_uuid}>
                  <td>{ep.show_name}</td>
                  <td>{ep.title}</td>
                  <td>
                    <button className="btn danger" onClick={() => void remove(ep.episode_uuid)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
