import { atom } from 'nanostores'

import { connectionScoped } from '@/api/client'
import { onPersistenceEvent, readKey, writeKey } from '@/lib/storage'

import { $customModels } from './custom-models'
import { $favoriteModels } from './favorite-models'
import { $knownModels, $visibleModels } from './model-visibility'
import { notifyError } from './notifications'
import { createPreferenceSync, type PickerPatch, type PreferenceTransport } from './shared-picker-sync'

const keys = {
  favorites: 'hermes.desktop.favorite-models',
  visible: 'hermes.desktop.visible-models',
  known: 'hermes.desktop.known-models',
  custom_models: 'hermes.desktop.custom-models'
}

let unbind: (() => void) | undefined
let owner = ''
let sync: ReturnType<typeof createPreferenceSync> | undefined
let pending: PickerPatch = {}
let ready = false
let binding = 0
let boundTransportIdentity: unknown
/** `shared: false` = the bound backend has no usable preference API: this machine's own legacy
 *  values are shown and edits stay renderer-local (pre-sync behavior), never sent anywhere. */
export const $sharedPickerStatus = atom<{ owner: string; ready: boolean; shared?: boolean }>({ owner: '', ready: false })
/** The installation a preference transport talks to: an explicit owner connection, else the
 *  window's ambient connection (untagged requests), else this machine. Never assume 'local'
 *  for an ambient remote primary. */
export const sharedPickerConnection = (connection?: string | null) => connection || connectionScoped().connectionId || 'local'
export const sharedPickerOwner = (connection?: string | null, profile?: string | null) =>
  sharedPickerConnection(connection) + ':' + ((profile ?? '').trim() || 'default')

export function sharedPickerReady(expectedOwner: string): boolean {
  const state = $sharedPickerStatus.get()

  return state.owner === expectedOwner && state.ready
}

export function refreshSharedPicker(expectedOwner?: string): Promise<unknown> {
  if (expectedOwner && expectedOwner !== owner) {return Promise.resolve()}

  return sync?.load() ?? Promise.resolve()
}

function snapshot(): PickerPatch {
  return {
    favorites: [...$favoriteModels.get()],
    visibility: { visible: $visibleModels.get() === null ? null : [...$visibleModels.get()!], known: $knownModels.get() === null ? null : [...$knownModels.get()!] },
    custom_models: $customModels.get().map(row => ({ ...row }))
  }
}

async function hydrateBinding(token: number, transport: PreferenceTransport): Promise<ReturnType<typeof createPreferenceSync>> {
  // Preserve the original local values before any remote hydration. Never remove legacy keys.
  const originals = Object.fromEntries(Object.values(keys).map(key => [key, readKey(key)]))
  const backupKey = 'hermes.desktop.picker-migration-backup'

  if (readKey(backupKey) === null) {writeKey(backupKey, JSON.stringify(originals))}
  const savedOriginals = JSON.parse(readKey(backupKey) || '{}') as Record<string, string | null>

  const parse = (key: string, fallback: unknown) => {
    if (savedOriginals[key] === null || savedOriginals[key] === undefined) {return fallback}
    const parsed: unknown = JSON.parse(savedOriginals[key]!)

    if (!Array.isArray(parsed)) {throw new Error('Legacy picker preferences are malformed; recovery backup was retained')}

    return parsed
  }

  const legacy: PickerPatch | undefined = Object.values(savedOriginals).some(value => value !== null) ? {
    favorites: parse(keys.favorites, []),
    visibility: { visible: parse(keys.visible, null), known: parse(keys.known, null) },
    custom_models: parse(keys.custom_models, [])
  } as PickerPatch : undefined

  const instance = createPreferenceSync(transport, state => {
    if (token !== binding) {return}
    $favoriteModels.set([...state.favorites])
    $visibleModels.set(state.visibility.visible === null ? null : new Set(state.visibility.visible))
    $knownModels.set(state.visibility.known === null ? null : new Set(state.visibility.known))
    $customModels.set(state.custom_models.map(row => ({ ...row })))
  })

  sync = instance
  await instance.load(legacy)

  return instance
}

/** Pre-sync behavior for a backend without a usable preference API: this renderer's own
 *  legacy stores are shown again (never another installation's hydrated values). */
function restoreLocalPresentation(): void {
  const local = (key: string): unknown[] | null => {
    try {
      const value: unknown = JSON.parse(readKey(key) ?? 'null')

      return Array.isArray(value) ? value : null
    } catch {
      return null
    }
  }

  const visible = local(keys.visible) as string[] | null
  const known = local(keys.known) as string[] | null
  $favoriteModels.set((local(keys.favorites) as string[] | null) ?? [])
  $visibleModels.set(visible ? new Set(visible) : null)
  $knownModels.set(known ? new Set(known) : null)
  $customModels.set((local(keys.custom_models) as { provider: string; model: string }[] | null) ?? [])
}

export async function bindSharedPicker(nextOwner: string, transport: PreferenceTransport, transportIdentity: unknown = transport): Promise<void> {
  const previous = sync
  const preserveIntent = owner === nextOwner && boundTransportIdentity === transportIdentity && previous
  boundTransportIdentity = transportIdentity
  const token = ++binding
  unbind?.()
  ready = false
  owner = nextOwner
  $sharedPickerStatus.set({ owner, ready: false })

  // Ordinary reopen replaces the transport, but first settles accepted intent on
  // the original transport/revision. Never transfer patches to a new binding.
  if (preserveIntent) {
    if (Object.keys(pending).length) {
      void previous.update(pending).catch(error => notifyError(error, 'Shared picker preferences were not saved; reload and retry.'))
    }

    pending = {}
    await previous.drain()

    if (token !== binding) {return}
  }

  previous?.cancel()
  pending = {}
  let scheduled = false
  let instance: ReturnType<typeof createPreferenceSync>

  try {
    instance = await hydrateBinding(token, transport)
  } catch (error) {
    if (token === binding) {
      // Edits stay renderer-local; no listener is installed, so nothing is written through.
      restoreLocalPresentation()
      $sharedPickerStatus.set({ owner, ready: true, shared: false })
    }

    throw error
  }

  if (token !== binding) {return}
  ready = true
  $sharedPickerStatus.set({ owner, ready: true, shared: true })
  unbind = onPersistenceEvent(event => {
    if (event.op === 'read' || !ready) {return}
    const state = snapshot()

    if (event.key === keys.favorites) {pending.favorites = state.favorites}
    else if (event.key === keys.custom_models) {pending.custom_models = state.custom_models}
    else if (event.key === keys.visible || event.key === keys.known) {pending.visibility = state.visibility}
    else {return}

    if (scheduled) {return}
    scheduled = true
    queueMicrotask(() => {
      if (token !== binding || !ready) {return}
      scheduled = false
      const patch = pending
      pending = {}
      void instance.update(patch).catch(error => notifyError(error, 'Shared picker preferences were not saved; reload and retry.'))
    })
  })
}
