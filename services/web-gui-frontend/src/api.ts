// Typed client for web-gui-backend's JSON API. Field shapes mirror
// services/common/src/common/models.py's ProfileConfig/GlobalConfig
// exactly (via FastAPI's model_dump(mode="json")) -- kept minimal to
// what M11's Overview/Profiles screens actually use, not every field
// every model has; extend as later screens need more.

export type DeviceMatch = {
  match_by: 'serial' | 'volume_label'
  match_value: string
}

export type SyncSettings = {
  trigger: 'on_connect' | 'manual' | 'cron'
  transcode_format: string
  push_play_status_back: boolean
  mode: 'itunes' | 'rockbox'
}

export type FetchSettings = {
  schedule: string | null
}

export type PlaylistEntry = {
  name: string
  source: 'apple_music' | 'spotify' | 'ytmusic'
  source_id: string
  sync_mode: 'absolute' | 'additive'
  fetch_schedule: string | null
}

export type PodcastsConfig = {
  pocketcasts: { credentials_file: string }
  sync_unplayed_only: boolean
  max_episodes_per_show: number
  shows: 'all' | (string | { name: string; fetch_schedule?: string | null })[]
  fetch_schedule: string | null
  episode_filter: 'played' | 'archived'
  fill_modes: Record<string, 'newest' | 'next'>
  delete_played_episodes: boolean
}

export type SelectionConfig = {
  mode: 'include' | 'exclude'
  selections: string[]
}

export type ExternalLibraryConfig = SelectionConfig & { path: string }
export type AudiobooksConfig = SelectionConfig

// Profile carries one more nested section (music, the general-library
// scoping) -- passed through untyped via the index signature, since
// this pass doesn't edit it and must never drop it on a round-trip
// (the backend's StrictModel would reject a write missing a required
// field the UI doesn't otherwise touch).
export type Profile = {
  profile: string
  device: DeviceMatch
  sync: SyncSettings
  fetch: FetchSettings
  playlists: PlaylistEntry[]
  podcasts: PodcastsConfig
  external_library?: ExternalLibraryConfig | null
  audiobooks?: AudiobooksConfig | null
  [key: string]: unknown
}

export type GlobalConfig = {
  paths: { library_root: string; state_root: string }
  sources: {
    apple_music: { enabled: boolean; cookies_file: string }
    spotify: { enabled: boolean; credentials_file: string }
    ytmusic: {
      enabled: boolean
      oauth_file: string
      cookies_file: string
      oauth_client_file: string
    }
  }
  [key: string]: unknown
}

export type ConnectedDevice = {
  path: string
  volume_label: string
  serial: string
  firewire_guid: string
  model_family: string
  generation: string
  model_number: string
  capacity: string
}

export type PlaylistSummary = {
  source_id: string
  name: string
  track_count: number
  owner: string | null
}

export type PodcastSubscription = {
  uuid: string
  title: string
  author: string
}

export type CredentialFileStatus = {
  exists: boolean
  updated_at: number | null
}

export type SourceStatus = CredentialFileStatus & { enabled: boolean }

export type YtmusicStatus = {
  enabled: boolean
  cookies: CredentialFileStatus
  oauth: CredentialFileStatus
  oauth_client: CredentialFileStatus
}

export type OAuthDeviceCode = {
  device_code: string
  user_code: string
  verification_url: string
  expires_in: number
  interval: number
}

export type SourcesStatus = {
  apple_music: SourceStatus
  ytmusic: YtmusicStatus
  spotify: SourceStatus
}

export type DirEntry = { name: string; is_dir: boolean }
export type BrowseResult = { subpath: string; entries: DirEntry[] }

// The backend's ConfigError shape: {"path": "...", "errors": ["dotted.field — message", ...]}
export class ApiError extends Error {
  status: number
  errors: string[]

  constructor(status: number, detail: unknown) {
    const errors =
      typeof detail === 'object' && detail !== null && 'errors' in detail
        ? ((detail as { errors: string[] }).errors)
        : [String(detail)]
    super(errors.join('; '))
    this.status = status
    this.errors = errors
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => resp.statusText)
    throw new ApiError(resp.status, (detail as { detail?: unknown })?.detail ?? detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

export const api = {
  listProfiles: () => request<Record<string, Profile>>('/api/profiles'),
  getProfile: (name: string) => request<Profile>(`/api/profiles/${encodeURIComponent(name)}`),
  putProfile: (name: string, profile: Profile) =>
    request<Profile>(`/api/profiles/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),
  deleteProfile: (name: string) =>
    request<void>(`/api/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  getGlobalConfig: () => request<GlobalConfig>('/api/global-config'),
  putGlobalConfig: (config: GlobalConfig) =>
    request<GlobalConfig>('/api/global-config', { method: 'PUT', body: JSON.stringify(config) }),
  identifyDevice: () => request<{ devices: ConnectedDevice[] }>('/api/device/identify'),

  listAppleMusicPlaylists: () => request<PlaylistSummary[]>('/api/sources/apple-music/playlists'),
  listYtmusicPlaylists: () => request<PlaylistSummary[]>('/api/sources/ytmusic/playlists'),
  resolveYtmusicPlaylist: (url: string) =>
    request<PlaylistSummary>(`/api/sources/ytmusic/resolve?url=${encodeURIComponent(url)}`),
  putAppleMusicCookies: (cookiesTxt: string) =>
    request<{ status: string }>('/api/sources/apple-music/cookies', {
      method: 'PUT',
      body: JSON.stringify({ cookies_txt: cookiesTxt }),
    }),
  putYtmusicCookies: (cookiesTxt: string) =>
    request<{ status: string }>('/api/sources/ytmusic/cookies', {
      method: 'PUT',
      body: JSON.stringify({ cookies_txt: cookiesTxt }),
    }),
  getSourcesStatus: () => request<SourcesStatus>('/api/sources/status'),

  putYtmusicOauthClient: (clientId: string, clientSecret: string) =>
    request<{ status: string }>('/api/sources/ytmusic/oauth-client', {
      method: 'PUT',
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
    }),
  startYtmusicOauth: () =>
    request<OAuthDeviceCode>('/api/sources/ytmusic/oauth/start', { method: 'POST' }),
  pollYtmusicOauth: (deviceCode: string) =>
    request<{ status: 'ok' | 'pending' }>('/api/sources/ytmusic/oauth/poll', {
      method: 'POST',
      body: JSON.stringify({ device_code: deviceCode }),
    }),

  getPocketcastsStatus: (profileName: string) =>
    request<CredentialFileStatus>(
      `/api/profiles/${encodeURIComponent(profileName)}/pocketcasts-status`,
    ),
  getPocketcastsSubscriptions: (profileName: string) =>
    request<PodcastSubscription[]>(
      `/api/profiles/${encodeURIComponent(profileName)}/pocketcasts/subscriptions`,
    ),
  putPocketcastsCredentials: (profileName: string, email: string, password: string) =>
    request<{ status: string }>(
      `/api/profiles/${encodeURIComponent(profileName)}/pocketcasts-credentials`,
      { method: 'PUT', body: JSON.stringify({ email, password }) },
    ),

  browseExternalLibrary: (root: string, subpath: string) =>
    request<BrowseResult>(
      `/api/external-library/browse?root=${encodeURIComponent(root)}&subpath=${encodeURIComponent(subpath)}`,
    ),
  browseAudiobooks: (subpath: string) =>
    request<BrowseResult>(`/api/audiobooks/browse?subpath=${encodeURIComponent(subpath)}`),
}
