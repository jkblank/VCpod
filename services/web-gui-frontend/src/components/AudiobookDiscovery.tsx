import { useEffect, useRef, useState } from 'react'
import { api, ApiError, streamImportAudiobook, type DiscoveredBook, type GlobalConfig } from '../api'
import { formatRelativeTime } from '../format'
import { Spinner, SyncedIcon, ToAddIcon } from '../icons'

// Global, not tied to any one profile -- library/audiobooks is one
// shared pool synced from (same reasoning as library/music), so "where
// do raw captures sit before processing" isn't a per-profile question
// either. See common.models.AudiobookManagerConfig.
export default function AudiobookDiscovery() {
  const [globalConfig, setGlobalConfig] = useState<GlobalConfig | null>(null)
  const [rootInput, setRootInput] = useState('')
  const [savingRoot, setSavingRoot] = useState(false)
  const [books, setBooks] = useState<DiscoveredBook[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [importLog, setImportLog] = useState<string[]>([])
  const importLogRef = useRef<HTMLPreElement | null>(null)

  useEffect(() => {
    if (importLogRef.current) importLogRef.current.scrollTop = importLogRef.current.scrollHeight
  }, [importLog])

  const loadBooks = async () => {
    setError(null)
    try {
      const result = await api.discoverAudiobooks()
      setBooks(result.books)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  const reload = async () => {
    try {
      const config = await api.getGlobalConfig()
      setGlobalConfig(config)
      setRootInput(config.audiobook_manager.discover_root)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      return
    }
    await loadBooks()
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const saveRoot = async () => {
    if (!globalConfig) return
    setSavingRoot(true)
    setError(null)
    try {
      await api.putGlobalConfig({
        ...globalConfig,
        audiobook_manager: { discover_root: rootInput.trim() },
      })
      await reload()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSavingRoot(false)
    }
  }

  const processBook = async (name: string) => {
    setImporting(name)
    setImportError(null)
    setImportLog([])
    try {
      // Streamed (see web_gui_backend/audiobook_runner.py) rather than one
      // blocking request -- a real multi-hour book's ffmpeg encode plus
      // beets-audible's Audible lookup can take minutes, and this way the
      // log below shows *which* step is running instead of a bare spinner
      // with no sign of whether it's actually progressing.
      for await (const evt of streamImportAudiobook(name)) {
        if (evt.event === 'progress') {
          setImportLog((prev) => [...prev, evt.data])
        } else if (evt.event === 'result') {
          await loadBooks()
        } else {
          setImportError(`${name}: ${evt.data}`)
        }
      }
    } catch (e) {
      setImportError(`${name}: ${e instanceof ApiError ? e.message : String(e)}`)
    } finally {
      setImporting(null)
    }
  }

  return (
    <div className="card">
      <h3>Discover new audiobooks</h3>
      <p className="muted">
        Scans a folder of raw, not-yet-processed audiobook parts (see the manual Libby-capture
        workflow in <code>services/audiobook-manager/README.md</code>) and remembers which ones
        have already been merged and tagged into <code>library/audiobooks</code>.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <div className="field">
        <label>Drop-zone folder (on the machine running the backend)</label>
        <input
          value={rootInput}
          placeholder="/home/you/audiobook-captures"
          onChange={(e) => setRootInput(e.target.value)}
        />
      </div>
      <div className="row">
        <button
          className="btn secondary"
          onClick={saveRoot}
          disabled={savingRoot || !globalConfig}
        >
          {savingRoot ? 'Saving…' : 'Save folder'}
        </button>
        <button className="btn secondary" onClick={loadBooks} disabled={!globalConfig}>
          Rescan
        </button>
      </div>

      {importError && <div className="error-banner">{importError}</div>}

      {importing && importLog.length > 0 && (
        <pre className="sync-log" ref={importLogRef}>
          {importLog.join('\n')}
        </pre>
      )}

      {books && books.length === 0 && (
        <p className="muted">
          {globalConfig?.audiobook_manager.discover_root
            ? 'No audiobook candidates found in that folder.'
            : 'Set a folder above to scan it.'}
        </p>
      )}

      {books && books.length > 0 && (
        <table className="discover-table">
          <thead>
            <tr>
              <th>Book</th>
              <th>Files</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {books.map((book) => (
              <tr key={book.name}>
                <td title={book.name}>{book.name}</td>
                <td>{book.audio_file_count}</td>
                <td title={
                  book.already_imported
                    ? `Already imported (${formatRelativeTime(book.imported_at)})`
                    : 'New — needs processing'
                }>
                  <span className="discover-status">
                    {book.already_imported ? <SyncedIcon size={16} /> : <ToAddIcon size={16} />}
                    {book.already_imported
                      ? `Already imported (${formatRelativeTime(book.imported_at)})`
                      : 'New — needs processing'}
                  </span>
                </td>
                <td>
                  {!book.already_imported && (
                    <button
                      className="btn"
                      onClick={() => processBook(book.name)}
                      disabled={importing !== null}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}
                    >
                      {importing === book.name && <Spinner size={14} />}
                      {importing === book.name ? 'Processing…' : 'Process into library'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
