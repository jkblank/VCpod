export function formatRelativeTime(unixSeconds: number | null): string {
  if (unixSeconds == null) return 'never'
  const seconds = Date.now() / 1000 - unixSeconds
  return formatRelativeSeconds(seconds)
}

// Same relative-time formatting as above, but from an ISO 8601 string
// (what /api/overview, /api/activity, and /api/alerts return) rather
// than the unix-seconds shape the credential-status routes use.
export function formatRelativeIso(iso: string | null, futureLabel = 'ago'): string {
  if (iso == null) return 'never'
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (seconds < 0) return formatRelativeSeconds(-seconds, futureLabel === 'ago' ? 'from now' : futureLabel)
  return formatRelativeSeconds(seconds)
}

function formatRelativeSeconds(seconds: number, suffix = 'ago'): string {
  if (seconds < 60) return suffix === 'ago' ? 'just now' : `moments ${suffix}`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ${suffix}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ${suffix}`
  const days = Math.floor(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ${suffix}`
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exp
  return `${exp === 0 ? value : value.toFixed(1)} ${units[exp]}`
}
