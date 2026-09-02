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

// Profile/GlobalConfig carry several more nested sections (playlists,
// podcasts, external_library, audiobooks, music, sources, ...) --
// passed through as unknown here rather than typed, since M11 only
// reads/writes the fields above and must never drop the rest of the
// payload on a round-trip (the backend's StrictModel would reject a
// write missing a required field the UI doesn't otherwise touch).
export type Profile = {
  profile: string
  device: DeviceMatch
  sync: SyncSettings
  fetch: FetchSettings
  [key: string]: unknown
}

export type GlobalConfig = {
  paths: { library_root: string; state_root: string }
  sources: {
    apple_music: { enabled: boolean; cookies_file: string }
    spotify: { enabled: boolean; credentials_file: string }
    ytmusic: { enabled: boolean; oauth_file: string; cookies_file: string }
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
  identifyDevice: () => request<{ devices: ConnectedDevice[] }>('/api/device/identify'),
}
