import { DAY_NAMES, cronToSchedule, pad2, scheduleToCron, type Schedule } from '../cronBuilder'

type Props = {
  value: string | null
  onChange: (cron: string | null) => void
}

// No cron syntax required for the common cases (manual/daily/every-N-
// hours/weekly) -- generates the equivalent cron string underneath.
// Anything already saved that doesn't fit one of those shapes round-
// trips through "Custom (cron)" instead of being silently mangled.
// Purely derived from `value` each render (no local state) -- safe to
// re-derive on every prop change since scheduleToCron/cronToSchedule
// round-trip losslessly for every non-custom kind.
export default function ScheduleEditor({ value, onChange }: Props) {
  const schedule = cronToSchedule(value)
  const update = (next: Schedule) => onChange(scheduleToCron(next))

  const timeValue = (hour: number, minute: number) => `${pad2(hour)}:${pad2(minute)}`
  const parseTime = (v: string): [number, number] => {
    const [h, m] = v.split(':').map(Number)
    return [h || 0, m || 0]
  }

  return (
    <>
      <div className="field">
        <label>Fetch schedule</label>
        <select
          value={schedule.kind}
          onChange={(e) => {
            const kind = e.target.value as Schedule['kind']
            if (kind === 'manual') update({ kind: 'manual' })
            else if (kind === 'daily') update({ kind: 'daily', hour: 3, minute: 0 })
            else if (kind === 'every_n_hours') update({ kind: 'every_n_hours', n: 6 })
            else if (kind === 'weekly')
              update({ kind: 'weekly', dayOfWeek: 0, hour: 3, minute: 0 })
            else update({ kind: 'custom', cron: value ?? '0 3 * * *' })
          }}
        >
          <option value="manual">Manual only</option>
          <option value="daily">Every day</option>
          <option value="every_n_hours">Every N hours</option>
          <option value="weekly">Weekly</option>
          <option value="custom">Custom (cron)</option>
        </select>
      </div>

      {schedule.kind === 'daily' && (
        <div className="field">
          <label>Time</label>
          <input
            type="time"
            value={timeValue(schedule.hour, schedule.minute)}
            onChange={(e) => {
              const [hour, minute] = parseTime(e.target.value)
              update({ kind: 'daily', hour, minute })
            }}
          />
        </div>
      )}

      {schedule.kind === 'every_n_hours' && (
        <div className="field">
          <label>Every N hours</label>
          <input
            type="number"
            min={1}
            max={23}
            value={schedule.n}
            onChange={(e) => update({ kind: 'every_n_hours', n: Number(e.target.value) || 1 })}
          />
        </div>
      )}

      {schedule.kind === 'weekly' && (
        <div className="row">
          <div className="field">
            <label>Day</label>
            <select
              value={schedule.dayOfWeek}
              onChange={(e) => update({ ...schedule, dayOfWeek: Number(e.target.value) })}
            >
              {DAY_NAMES.map((name, i) => (
                <option key={i} value={i}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Time</label>
            <input
              type="time"
              value={timeValue(schedule.hour, schedule.minute)}
              onChange={(e) => {
                const [hour, minute] = parseTime(e.target.value)
                update({ ...schedule, hour, minute })
              }}
            />
          </div>
        </div>
      )}

      {schedule.kind === 'custom' && (
        <div className="field">
          <label>Cron expression</label>
          <input
            value={schedule.cron}
            placeholder="0 3 * * *"
            onChange={(e) => update({ kind: 'custom', cron: e.target.value })}
          />
        </div>
      )}
    </>
  )
}
