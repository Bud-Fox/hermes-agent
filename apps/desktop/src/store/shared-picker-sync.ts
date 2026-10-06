export interface PickerPreferences {
  version: number
  revision: number
  initialized: boolean
  favorites: string[]
  visibility: { visible: string[] | null; known: string[] | null }
  custom_models: { provider: string; model: string }[]
}

export type PickerPatch = Partial<Pick<PickerPreferences, 'favorites' | 'visibility' | 'custom_models'>>
export type PreferenceTransport = (method: string, params?: Record<string, unknown>) => Promise<PickerPreferences>

function checked(state: PickerPreferences): PickerPreferences {
  const vis = state?.visibility
  const list = (value: unknown) => Array.isArray(value)

  if (!state || typeof state.revision !== 'number' || typeof state.initialized !== 'boolean' || !list(state.favorites)
      || !list(state.custom_models) || !vis || !(vis.visible === null || list(vis.visible)) || !(vis.known === null || list(vis.known))) {
    throw new Error('Backend returned no shared picker preference state')
  }

  return state
}

export function createPreferenceSync(transport: PreferenceTransport, hydrate: (state: PickerPreferences) => void) {
  let state: PickerPreferences | undefined
  let queue = Promise.resolve()
  let generation = 0
  let cancelled = false

  async function read(legacy?: PickerPatch) {
    if (cancelled) {throw new Error('Picker binding replaced')}
    state = checked(await transport('model.preferences.get'))

    if (cancelled) {return state}

    if (!state.initialized && legacy && Object.keys(legacy).length) {
      state = checked(await transport('model.preferences.update', { expected_revision: state.revision, import_once: true, ...legacy }))
    }

    if (!cancelled) {hydrate(state)}

    return state
  }

  function load(legacy?: PickerPatch) {
    const job = queue.then(() => read(legacy))
    queue = job.then(() => {}, () => {})

    return job
  }

  function update(patch: PickerPatch): Promise<void> {
    const submittedGeneration = generation

    const job = queue.then(async () => {
      if (cancelled || submittedGeneration !== generation) {throw new Error('Picker preference conflict; reload and retry')}

      if (!state) {throw new Error('Shared picker preferences have not loaded')}

      try {
        state = checked(await transport('model.preferences.update', { expected_revision: state.revision, ...patch }))

        if (!cancelled) {hydrate(state)}
      } catch (error) {
        generation++
        // Fetch winning state but NEVER silently replay the stale edit over it.
        await read()
        throw error
      }
    })

    queue = job.catch(() => {})

    return job
  }

  return { load, update, drain: () => queue, cancel: () => { cancelled = true; generation++ } }
}
