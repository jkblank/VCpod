// A client-side JSON -> YAML-ish pretty-printer for the "View YAML"
// dialog -- the frontend already has the full profile object in memory
// (via store.draft), so this is display-only convenience, not a real
// YAML serializer and not a new backend endpoint. Good enough to show
// the shape of what a save will write; the actual file is written by
// common.config.save_profile_config server-side, the only real writer.
export function toYamlish(value: unknown, indent = 0): string {
  const pad = '  '.repeat(indent)

  if (value === null || value === undefined) return 'null'
  if (typeof value !== 'object') return String(value)

  if (Array.isArray(value)) {
    if (value.length === 0) return `${pad}[]`
    return value
      .map((item) => {
        if (item !== null && typeof item === 'object' && Object.keys(item).length > 0) {
          return `${pad}- ${toYamlish(item, indent + 1).trimStart()}`
        }
        return `${pad}- ${toYamlish(item, 0)}`
      })
      .join('\n')
  }

  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length === 0) return `${pad}{}`
  return entries
    .map(([key, val]) => {
      if (val !== null && typeof val === 'object' && Object.keys(val).length > 0) {
        return `${pad}${key}:\n${toYamlish(val, indent + 1)}`
      }
      return `${pad}${key}: ${toYamlish(val, 0)}`
    })
    .join('\n')
}
