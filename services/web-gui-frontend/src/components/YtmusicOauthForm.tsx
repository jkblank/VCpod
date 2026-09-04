import { useEffect, useRef, useState } from 'react'
import { ApiError, type OAuthDeviceCode } from '../api'
import CredentialWarning from './CredentialWarning'

type Props = {
  clientPath: string
  oauthPath: string
  clientAlreadySaved: boolean
  onClientSaved: () => Promise<void>
  onTokenSaved: () => Promise<void>
  // Injected rather than calling api.* directly -- this form backs both
  // the global (household-shared) OAuth client/flow and, per-profile,
  // each profile's own -- same UI, different endpoint underneath.
  saveClient: (clientId: string, clientSecret: string) => Promise<unknown>
  startFlow: () => Promise<OAuthDeviceCode>
  pollFlow: (deviceCode: string) => Promise<{ status: 'ok' | 'pending' }>
}

// Two steps, since ytmusicapi has no default/shared OAuth client of its
// own -- every user has to create their own "TVs and Limited Input
// devices" client via Google Cloud Console before the device-code flow
// (RFC 8628) below can even start. See notes.md's ytmusic-oauth entry.
export default function YtmusicOauthForm({
  clientPath,
  oauthPath,
  clientAlreadySaved,
  onClientSaved,
  onTokenSaved,
  saveClient: saveClientApi,
  startFlow: startFlowApi,
  pollFlow: pollFlowApi,
}: Props) {
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [savingClient, setSavingClient] = useState(false)
  const [clientError, setClientError] = useState<string | null>(null)

  const [code, setCode] = useState<OAuthDeviceCode | null>(null)
  const [starting, setStarting] = useState(false)
  const [flowError, setFlowError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current)
      if (expiryTimer.current) clearTimeout(expiryTimer.current)
    }
  }, [])

  const saveClient = async () => {
    setSavingClient(true)
    setClientError(null)
    try {
      await saveClientApi(clientId.trim(), clientSecret.trim())
      setClientSecret('')
      await onClientSaved()
    } catch (e) {
      setClientError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSavingClient(false)
    }
  }

  const stopPolling = () => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current)
      pollTimer.current = null
    }
    if (expiryTimer.current) {
      clearTimeout(expiryTimer.current)
      expiryTimer.current = null
    }
  }

  const startFlow = async () => {
    setStarting(true)
    setFlowError(null)
    setDone(false)
    try {
      const started = await startFlowApi()
      setCode(started)
      pollTimer.current = setInterval(async () => {
        try {
          const result = await pollFlowApi(started.device_code)
          if (result.status === 'ok') {
            stopPolling()
            setDone(true)
            setCode(null)
            await onTokenSaved()
          }
          // 'pending' -- keep polling, nothing to do
        } catch (e) {
          stopPolling()
          setFlowError(e instanceof ApiError ? e.message : String(e))
        }
      }, started.interval * 1000)
      expiryTimer.current = setTimeout(() => {
        stopPolling()
        setFlowError('Code expired before the login was finished -- start over.')
        setCode(null)
      }, started.expires_in * 1000)
    } catch (e) {
      setFlowError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  if (!clientAlreadySaved) {
    return (
      <div className="card">
        <h3>YouTube Music OAuth client</h3>
        <p className="muted">
          ytmusicapi has no shared OAuth client of its own -- create a "TVs and Limited Input
          devices" OAuth client in the Google Cloud Console (YouTube Data API enabled) and paste
          its credentials here. Only needed for listing your own private playlists; downloads
          themselves use the cookies above.
        </p>
        <CredentialWarning path={clientPath} />
        {clientError && <div className="error-banner">{clientError}</div>}
        <div className="field">
          <label>Client ID</label>
          <input type="text" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </div>
        <div className="field">
          <label>Client secret</label>
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </div>
        <button
          className="btn"
          onClick={saveClient}
          disabled={savingClient || !clientId.trim() || !clientSecret.trim()}
        >
          {savingClient ? 'Saving…' : 'Save client'}
        </button>
      </div>
    )
  }

  return (
    <div className="card">
      <h3>Sign in to YouTube Music</h3>
      <CredentialWarning path={oauthPath} />
      {flowError && <div className="error-banner">{flowError}</div>}
      {done && <div className="success-banner">Signed in — OAuth token saved.</div>}

      {!code && (
        <button className="btn" onClick={startFlow} disabled={starting}>
          {starting ? 'Starting…' : 'Start sign-in'}
        </button>
      )}

      {code && (
        <div>
          <p>
            Go to{' '}
            <strong>
              <code>{code.verification_url}</code>
            </strong>{' '}
            and enter this code:
          </p>
          <p style={{ fontSize: '28px', fontWeight: 700, letterSpacing: '2px' }}>
            {code.user_code}
          </p>
          <p className="muted">Waiting for you to finish signing in there…</p>
        </div>
      )}
    </div>
  )
}
