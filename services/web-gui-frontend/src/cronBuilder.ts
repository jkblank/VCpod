// Converts between a plain cron string (what the backend actually
// stores -- common.models.CronSchedule, validated server-side via
// croniter) and a small structured shape a form can edit without the
// user ever needing to know cron syntax. Only covers the recurrence
// shapes actually seen in this project's real profiles (nightly,
// every-N-hours, weekly-on-a-day) -- anything else round-trips through
// as "custom" so an existing schedule this can't represent is never
// silently mangled.

export type Schedule =
  | { kind: 'manual' }
  | { kind: 'daily'; hour: number; minute: number }
  | { kind: 'every_n_hours'; n: number }
  | { kind: 'weekly'; dayOfWeek: number; hour: number; minute: number } // 0 = Sunday, matching cron
  | { kind: 'custom'; cron: string }

export function cronToSchedule(cron: string | null): Schedule {
  if (!cron) return { kind: 'manual' }
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return { kind: 'custom', cron }
  const [min, hour, dom, month, dow] = parts
  const isInt = (s: string) => /^\d+$/.test(s)

  if (dom === '*' && month === '*' && dow === '*') {
    if (isInt(min) && isInt(hour)) {
      return { kind: 'daily', hour: Number(hour), minute: Number(min) }
    }
    const everyHours = /^\*\/(\d+)$/.exec(hour)
    if (min === '0' && everyHours) {
      return { kind: 'every_n_hours', n: Number(everyHours[1]) }
    }
    return { kind: 'custom', cron }
  }

  if (dom === '*' && month === '*' && isInt(dow) && isInt(min) && isInt(hour)) {
    return { kind: 'weekly', dayOfWeek: Number(dow), hour: Number(hour), minute: Number(min) }
  }

  return { kind: 'custom', cron }
}

export function scheduleToCron(schedule: Schedule): string | null {
  switch (schedule.kind) {
    case 'manual':
      return null
    case 'daily':
      return `${schedule.minute} ${schedule.hour} * * *`
    case 'every_n_hours':
      return `0 */${schedule.n} * * *`
    case 'weekly':
      return `${schedule.minute} ${schedule.hour} * * ${schedule.dayOfWeek}`
    case 'custom':
      return schedule.cron
  }
}

export const DAY_NAMES = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
]

export function pad2(n: number): string {
  return String(n).padStart(2, '0')
}
