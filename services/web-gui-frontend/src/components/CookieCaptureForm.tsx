import { useState } from 'react'
import { ApiError } from '../api'
import CredentialWarning from './CredentialWarning'

type Props = {
  sourceName: string
  path: string
  onSubmit: (cookiesTxt: string) => Promise<void>
}

// Manual paste/upload of an already-exported cookies.txt -- not
// automated capture. Real cross-origin cookie reading from a browser
// is impossible (same-origin policy); the only way to actually
// automate this is a Playwright-driven separate browser instance,
// kept explicitly out of this build. See notes.md's 2026-09-02 entry.
export default function CookieCaptureForm({ sourceName, path, onSubmit }: Props) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const handleFile = async (file: File) => {
    setText(await file.text())
  }

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(text)
      setDone(true)
      setText('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="card">
      <h3>{sourceName} cookies</h3>
      <p className="muted">
        Export your <code>cookies.txt</code> from a real, logged-in browser session (e.g. the "Get
        cookies.txt" extension), then paste its contents below or upload the file directly.
      </p>
      <CredentialWarning path={path} />
      {error && <div className="error-banner">{error}</div>}
      {done && <div className="success-banner">Saved.</div>}

      <div className="field">
        <label>Upload cookies.txt</label>
        <input
          type="file"
          accept=".txt"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void handleFile(file)
          }}
        />
      </div>
      <div className="field">
        <label>...or paste its contents</label>
        <textarea
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="# Netscape HTTP Cookie File&#10;..."
        />
      </div>
      <button className="btn" onClick={submit} disabled={submitting || !text.trim()}>
        {submitting ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}
