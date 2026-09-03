import { useEffect, useState } from 'react'
import { api, ApiError, type DiscoveredBook, type GlobalConfig } from '../api'
import { formatRelativeTime } from '../format'

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
    try {
      await api.importDiscoveredAudiobook(name)
      await loadBooks()
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
                <td>{book.name}</td>
                <td>{book.audio_file_count}</td>
                <td>
                  {book.already_imported
                    ? `Already imported (${formatRelativeTime(book.imported_at)})`
                    : 'New — needs processing'}
                </td>
                <td>
                  {!book.already_imported && (
                    <button
                      className="btn"
                      onClick={() => processBook(book.name)}
                      disabled={importing !== null}
                    >
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
