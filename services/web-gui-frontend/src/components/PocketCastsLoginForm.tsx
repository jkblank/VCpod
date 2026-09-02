import { useState } from 'react'
import { ApiError } from '../api'
import CredentialWarning from './CredentialWarning'

type Props = {
  path: string
  onSubmit: (email: string, password: string) => Promise<void>
}

export default function PocketCastsLoginForm({ path, onSubmit }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(email, password)
      setPassword('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="card">
      <h3>Pocket Casts login</h3>
      <p className="muted">
        Checked against Pocket Casts' real login before anything is saved — a wrong password is
        rejected immediately, not silently written to disk.
      </p>
      <CredentialWarning path={path} />
      {error && <div className="error-banner">{error}</div>}

      <div className="field">
        <label>Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="field">
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <button className="btn" onClick={submit} disabled={submitting || !email || !password}>
        {submitting ? 'Checking…' : 'Save'}
      </button>
    </div>
  )
}
