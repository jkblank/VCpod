import { useEffect, useState } from 'react'
import { api, ApiError, type Profile } from './api'

// Shared "which profile is currently being edited" state -- lifted out
// of Profiles.tsx so the picker/podcasts screens can read and write the
// same draft profile a save button on any of them commits. Plain
// useState + one hook, not a context/store library -- still only a
// handful of screens, premature to reach for more.

export function emptyProfile(name: string): Profile {
  return {
    profile: name,
    device: { match_by: 'serial', match_value: '' },
    sync: {
      trigger: 'manual',
      transcode_format: 'alac',
      push_play_status_back: false,
      mode: 'itunes',
    },
    fetch: { schedule: null },
    playlists: [],
    podcasts: {
      pocketcasts: { credentials_file: `/config/secrets/pocketcasts/${name}.json` },
      sync_unplayed_only: true,
      max_episodes_per_show: 5,
      shows: 'all',
    },
  }
}

export function useProfileStore() {
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState<Profile | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveErrors, setSaveErrors] = useState<string[] | null>(null)
  const [saving, setSaving] = useState(false)

  const load = () =>
    api
      .listProfiles()
      .then((p) => {
        setProfiles(p)
        setLoadError(null)
      })
      .catch((e: unknown) => setLoadError(e instanceof ApiError ? e.message : String(e)))

  useEffect(() => {
    load()
  }, [])

  const select = (name: string) => {
    setSelected(name)
    setDraft(profiles[name] ?? null)
    setSaveErrors(null)
  }

  const startNew = () => {
    const name = window.prompt('New profile name (e.g. "sam"):')
    if (!name) return
    setSelected(name)
    setDraft(emptyProfile(name))
    setSaveErrors(null)
  }

  const save = async (next?: Profile) => {
    const toSave = next ?? draft
    if (!toSave) return
    setSaving(true)
    setSaveErrors(null)
    try {
      const saved = await api.putProfile(toSave.profile, toSave)
      setProfiles((prev) => ({ ...prev, [saved.profile]: saved }))
      setDraft(saved)
      return saved
    } catch (e) {
      setSaveErrors(e instanceof ApiError ? e.errors : [String(e)])
      throw e
    } finally {
      setSaving(false)
    }
  }

  const remove = async (name: string) => {
    if (!window.confirm(`Delete config/profiles/${name}.yaml? This cannot be undone here.`)) return
    await api.deleteProfile(name)
    setProfiles((prev) => {
      const next = { ...prev }
      delete next[name]
      return next
    })
    if (selected === name) {
      setSelected(null)
      setDraft(null)
    }
  }

  return {
    profiles,
    selected,
    draft,
    setDraft,
    loadError,
    saveErrors,
    setSaveErrors,
    saving,
    select,
    startNew,
    save,
    remove,
  }
}

export type ProfileStore = ReturnType<typeof useProfileStore>
