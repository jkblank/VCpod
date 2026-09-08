import { useEffect, useState } from 'react'
import { api, ApiError, type ConnectedDevice } from './api'

// Shared "what's plugged in right now" poll -- lifted out of Sync.tsx so
// the sidebar's status footer and the Sync screen read the same single
// identify-device call instead of each running their own.
export function useConnectedDevices() {
  const [devices, setDevices] = useState<ConnectedDevice[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .identifyDevice()
      .then((r) => {
        if (!cancelled) setDevices(r.devices)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { devices, error }
}
