import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  type GlobalConfig,
  type ProfileSourcesStatus,
  type SourcesStatus,
} from '../api'
import CookieCaptureForm from '../components/CookieCaptureForm'
import ImportOrRevertSource from '../components/ImportOrRevertSource'
import PocketCastsLoginForm from '../components/PocketCastsLoginForm'
import YtmusicOauthForm from '../components/YtmusicOauthForm'
import { formatRelativeTime } from '../format'
import { NeedsAttentionIcon, SyncedIcon } from '../icons'
import type { ProfileStore } from '../useProfileStore'

type OpenForm =
  | 'apple_music'
  | 'ytmusic-cookies'
  | 'ytmusic-oauth'
  | 'pocketcasts'
  | 'profile-apple-music'
  | 'profile-ytmusic-cookies'
  | 'profile-ytmusic-oauth'
  | null

export default function Credentials({ store }: { store: ProfileStore }) {
  const { draft, profiles } = store
  const [status, setStatus] = useState<SourcesStatus | null>(null)
  const [globalConfig, setGlobalConfig] = useState<GlobalConfig | null>(null)
  const [profileStatus, setProfileStatus] = useState<ProfileSourcesStatus | null>(null)
  const [pcStatus, setPcStatus] = useState<{ exists: boolean; updated_at: number | null } | null>(
    null,
  )
  const [error, setError] = useState<string | null>(null)
  const [openForm, setOpenForm] = useState<OpenForm>(null)

  const otherProfiles = Object.keys(profiles).filter((name) => name !== draft?.profile)

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
      try {
        setProfileStatus(await api.getProfileSourcesStatus(draft.profile))
      } catch {
        setProfileStatus(null)
      }
    } else {
      setPcStatus(null)
      setProfileStatus(null)
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
            {status.apple_music.exists ? <SyncedIcon size={16} /> : <NeedsAttentionIcon size={16} />}
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

        {draft && profileStatus && (
          <div className="field" style={{ marginTop: '12px' }}>
            <label>For {draft.profile}</label>
            <p className="muted">
              {profileStatus.apple_music.using === 'override'
                ? `Using its own login, updated ${formatRelativeTime(profileStatus.apple_music.updated_at)}.`
                : "Using the shared login above."}
            </p>
            <div className="row">
              <button
                className="btn secondary"
                onClick={() => setOpenForm('profile-apple-music')}
              >
                Set up separate credentials
              </button>
            </div>
            {openForm === 'profile-apple-music' && (
              <CookieCaptureForm
                sourceName="Apple Music"
                path={`config/secrets/${draft.profile}/apple_music_cookies.txt`}
                onSubmit={async (text) => {
                  await api.putProfileAppleMusicCookies(draft.profile, text)
                  setOpenForm(null)
                  await reload()
                }}
              />
            )}
            <ImportOrRevertSource
              using={profileStatus.apple_music.using}
              otherProfiles={otherProfiles}
              onImport={async (from) => {
                await api.importProfileSource(draft.profile, 'apple_music', from)
                await reload()
              }}
              onRevert={async () => {
                await api.deleteProfileSourceOverride(draft.profile, 'apple_music')
                await reload()
              }}
            />
          </div>
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
            {status.ytmusic.cookies.exists ? <SyncedIcon size={16} /> : <NeedsAttentionIcon size={16} />}
            <strong>YouTube Music</strong>
          </label>
          <button className="btn secondary" onClick={() => setOpenForm('ytmusic-cookies')}>
            {status.ytmusic.cookies.exists ? 'Re-export cookies' : 'Set up cookies'}
          </button>
          <button className="btn secondary" onClick={() => setOpenForm('ytmusic-oauth')}>
            {status.ytmusic.oauth.exists ? 'Re-authenticate' : 'Set up OAuth'}
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
        {openForm === 'ytmusic-oauth' && (
          <YtmusicOauthForm
            clientPath={globalConfig.sources.ytmusic.oauth_client_file}
            oauthPath={globalConfig.sources.ytmusic.oauth_file}
            clientAlreadySaved={status.ytmusic.oauth_client.exists}
            onClientSaved={reload}
            onTokenSaved={async () => {
              setOpenForm(null)
              await reload()
            }}
            saveClient={(id, secret) => api.putYtmusicOauthClient(id, secret)}
            startFlow={() => api.startYtmusicOauth()}
            pollFlow={(code) => api.pollYtmusicOauth(code)}
          />
        )}

        {draft && profileStatus && (
          <div className="field" style={{ marginTop: '12px' }}>
            <label>For {draft.profile}</label>
            <p className="muted">
              Cookies:{' '}
              {profileStatus.ytmusic.cookies.using === 'override'
                ? `its own, updated ${formatRelativeTime(profileStatus.ytmusic.cookies.updated_at)}`
                : 'shared login above'}
              . OAuth:{' '}
              {profileStatus.ytmusic.oauth.using === 'override'
                ? `its own, updated ${formatRelativeTime(profileStatus.ytmusic.oauth.updated_at)}`
                : profileStatus.ytmusic.oauth.exists
                  ? 'shared login above'
                  : 'not set up'}
              .
            </p>
            <div className="row">
              <button
                className="btn secondary"
                onClick={() => setOpenForm('profile-ytmusic-cookies')}
              >
                Set up separate cookies
              </button>
              <button className="btn secondary" onClick={() => setOpenForm('profile-ytmusic-oauth')}>
                Sign in as {draft.profile}
              </button>
            </div>
            {openForm === 'profile-ytmusic-cookies' && (
              <CookieCaptureForm
                sourceName="YouTube Music"
                path={`config/secrets/${draft.profile}/youtube_cookies.txt`}
                onSubmit={async (text) => {
                  await api.putProfileYtmusicCookies(draft.profile, text)
                  setOpenForm(null)
                  await reload()
                }}
              />
            )}
            {openForm === 'profile-ytmusic-oauth' && (
              <YtmusicOauthForm
                clientPath={`config/secrets/${draft.profile}/ytmusic_oauth_client.json`}
                oauthPath={`config/secrets/${draft.profile}/ytmusic_oauth.json`}
                clientAlreadySaved={profileStatus.ytmusic.oauth_client.exists}
                onClientSaved={reload}
                onTokenSaved={async () => {
                  setOpenForm(null)
                  await reload()
                }}
                saveClient={(id, secret) =>
                  api.putProfileYtmusicOauthClient(draft.profile, id, secret)
                }
                startFlow={() => api.startProfileYtmusicOauth(draft.profile)}
                pollFlow={(code) => api.pollProfileYtmusicOauth(draft.profile, code)}
              />
            )}
            <ImportOrRevertSource
              using={profileStatus.ytmusic.cookies.using}
              otherProfiles={otherProfiles}
              revertLabel="Revert to shared login (cookies + OAuth)"
              onImport={async (from) => {
                await api.importProfileSource(draft.profile, 'ytmusic', from)
                await reload()
              }}
              onRevert={async () => {
                await api.deleteProfileSourceOverride(draft.profile, 'ytmusic')
                await reload()
              }}
            />
          </div>
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
