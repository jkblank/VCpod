import { useState } from 'react'
import {
  ApiError,
  streamSyncExecute,
  streamSyncPlan,
  type SSEEvent,
  type SyncPlanSummary,
  type SyncResultSummary,
} from './api'

// Per-profile sync progress, lifted out of Sync.tsx and owned at the App
// level -- instantiated once in App.tsx and passed down, same pattern
// useConnectedDevices() already uses. The point: Sync.tsx unmounts every
// time you switch screens (App.tsx only ever renders the active one),
// so state that lived inside it used to vanish the moment you navigated
// away mid-sync and came back to find a blank screen even though the
// real sync-orchestrator subprocess was still running untouched
// server-side. Keeping this state here instead means the SSE stream
// keeps updating a live session regardless of which screen is mounted,
// and Sync.tsx just renders whatever's already there when it remounts.

export type RunningAction = 'plan' | 'execute' | 'dangerous' | null

export type SyncSessionState = {
  runningAction: RunningAction
  log: string[]
  plan: SyncPlanSummary | null
  result: SyncResultSummary | null
  error: string | null
}

const EMPTY_SESSION: SyncSessionState = {
  runningAction: null,
  log: [],
  plan: null,
  result: null,
  error: null,
}

export function useSyncSessions() {
  const [sessions, setSessions] = useState<Record<string, SyncSessionState>>({})

  const getSession = (profile: string): SyncSessionState => sessions[profile] ?? EMPTY_SESSION

  const updateSession = (
    profile: string,
    patch: Partial<SyncSessionState> | ((s: SyncSessionState) => Partial<SyncSessionState>),
  ) => {
    setSessions((prev) => {
      const current = prev[profile] ?? EMPTY_SESSION
      const patchObj = typeof patch === 'function' ? patch(current) : patch
      return { ...prev, [profile]: { ...current, ...patchObj } }
    })
  }

  const resetSession = (profile: string) =>
    updateSession(profile, { log: [], plan: null, result: null, error: null })

  const run = async (
    profile: string,
    action: Exclude<RunningAction, null>,
    stream: AsyncGenerator<SSEEvent>,
    onResult: (data: string) => void,
  ) => {
    updateSession(profile, { runningAction: action })
    try {
      for await (const evt of stream) {
        if (evt.event === 'progress') {
          updateSession(profile, (s) => ({ log: [...s.log, evt.data] }))
        } else if (evt.event === 'result') {
          onResult(evt.data)
        } else {
          updateSession(profile, { error: evt.data })
          return
        }
      }
    } catch (e) {
      updateSession(profile, { error: e instanceof ApiError ? e.message : String(e) })
    } finally {
      updateSession(profile, { runningAction: null })
    }
  }

  const computePlan = async (profile: string) => {
    resetSession(profile)
    await run(profile, 'plan', streamSyncPlan({ profile }), (data) => {
      updateSession(profile, { plan: JSON.parse(data) as SyncPlanSummary })
    })
  }

  const execute = async (profile: string, allowRemovals: boolean) => {
    updateSession(profile, { result: null, error: null })
    await run(
      profile,
      'execute',
      streamSyncExecute({ profile, allow_removals: allowRemovals }),
      (data) => updateSession(profile, { result: JSON.parse(data) as SyncResultSummary }),
    )
  }

  const dangerousSync = async (profile: string) => {
    resetSession(profile)
    await run(profile, 'dangerous', streamSyncExecute({ profile, allow_removals: true }), (data) =>
      updateSession(profile, { result: JSON.parse(data) as SyncResultSummary }),
    )
  }

  return { getSession, computePlan, execute, dangerousSync, resetSession }
}

export type SyncSessions = ReturnType<typeof useSyncSessions>
