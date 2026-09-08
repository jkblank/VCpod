import { useEffect, useState } from 'react'
import { ApiError, type BrowseResult, type DirEntry } from '../api'

type Props = {
  // Fetches one directory's listing. Called with the segments the user
  // has navigated into so far (joined with "/" by this component) --
  // kept source-agnostic so both External library (an arbitrary root)
  // and Audiobooks (a fixed, backend-known root) can share this one
  // picker.
  browse: (subpath: string) => Promise<BrowseResult>
  selections: string[]
  onSelectionsChange: (next: string[]) => void
}

// Browse-then-tick picker for a real directory tree, matching the
// Sources/Podcasts screens' own pattern -- one level at a time (a
// breadcrumb, not a full expand-all tree) since a personal library can
// be large. Checking a folder selects everything under it (adds its
// full relative path to `selections`); checking a file selects just
// that file. Unchecking removes that exact entry regardless of whether
// some ancestor folder is separately selected -- kept simple/
// predictable rather than trying to reconcile overlapping selections.
export default function DirectoryPicker({ browse, selections, onSelectionsChange }: Props) {
  const [path, setPath] = useState<string[]>([])
  const [entries, setEntries] = useState<DirEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    browse(path.join('/'))
      .then((res) => setEntries(res.entries))
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path.join('/')])

  const fullPath = (name: string) => [...path, name].join('/')
  const isSelected = (name: string) => selections.includes(fullPath(name))

  const toggle = (name: string) => {
    const p = fullPath(name)
    onSelectionsChange(
      selections.includes(p) ? selections.filter((s) => s !== p) : [...selections, p],
    )
  }

  return (
    <div>
      <div className="breadcrumb">
        <button className="crumb" onClick={() => setPath([])}>
          (root)
        </button>
        {path.map((segment, i) => (
          <span key={i}>
            {' / '}
            <button className="crumb" onClick={() => setPath(path.slice(0, i + 1))}>
              {segment}
            </button>
          </span>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && entries.length === 0 && <p className="muted">Empty.</p>}

      {entries.map((entry) => (
        <div className="picker-row" key={entry.name}>
          <input type="checkbox" checked={isSelected(entry.name)} onChange={() => toggle(entry.name)} />
          {entry.is_dir ? (
            <button className="dir-name" onClick={() => setPath([...path, entry.name])}>
              {entry.name}/
            </button>
          ) : (
            <span className="name">{entry.name}</span>
          )}
        </div>
      ))}

      {selections.length > 0 && (
        <p className="muted" style={{ marginTop: '12px' }}>
          {selections.length} selected: {selections.join(', ')}
        </p>
      )}
    </div>
  )
}
