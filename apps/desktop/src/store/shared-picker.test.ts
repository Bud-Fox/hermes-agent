import { describe, expect, it, vi } from 'vitest'

import { createPreferenceSync } from './shared-picker-sync'

const initial = { version: 1, revision: 0, initialized: false, favorites: [] as string[], visibility: { visible: null, known: null }, custom_models: [] }

describe('shared picker authority', () => {
  it('flushes an accepted same-owner edit before its persistence microtask on reopen', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    let state = { ...initial, initialized: true }
    const writes: unknown[] = []

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.update')) {
        writes.push(params)
        state = { ...state, ...params, revision: state.revision + 1 } as typeof state
      }

      return state
    }

    await bindSharedPicker('reopen-microtask', transport)
    setFavoriteModels(['a::accepted'])
    await bindSharedPicker('reopen-microtask', transport)
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(writes).toEqual([{ expected_revision: 0, favorites: ['a::accepted'] }])
    expect($favoriteModels.get()).toEqual(['a::accepted'])
  })
  it('drains accepted intent behind an inflight write before same-owner reopen hydrates', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    let state = { ...initial, initialized: true }
    let finish!: () => void
    const writes: unknown[] = []

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.update')) {
        writes.push(params)

        if (writes.length === 1) {await new Promise<void>(resolve => { finish = resolve })}
        state = { ...state, ...params, revision: state.revision + 1 } as typeof state
      }

      return state
    }

    await bindSharedPicker('reopen-inflight', transport)
    setFavoriteModels(['a::first'])
    await new Promise(resolve => setTimeout(resolve, 0))
    setFavoriteModels(['a::second'])
    await new Promise(resolve => setTimeout(resolve, 0))
    const reopened = bindSharedPicker('reopen-inflight', transport)
    finish()
    await reopened
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(writes).toEqual([
      { expected_revision: 0, favorites: ['a::first'] },
      { expected_revision: 1, favorites: ['a::second'] }
    ])
    expect($favoriteModels.get()).toEqual(['a::second'])
  })
  it('hydrates the late committed write when reopening the same owner', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    let state = { ...initial, initialized: true }
    let finish!: () => void

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.update')) {
        await new Promise<void>(resolve => { finish = resolve })
        state = { ...state, ...params, revision: state.revision + 1 } as typeof state
      }

      return state
    }

    await bindSharedPicker('reopen-late', transport)
    setFavoriteModels(['a::committed'])
    await new Promise(resolve => setTimeout(resolve, 0))
    const reopened = bindSharedPicker('reopen-late', transport)
    finish()
    await reopened
    await new Promise(resolve => setTimeout(resolve, 0))
    expect($favoriteModels.get()).toEqual(['a::committed'])
  })
  it('does not replay pending same-owner intent through a replacement transport', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { setFavoriteModels } = await import('./favorite-models')
    const old = vi.fn(async () => ({ ...initial, initialized: true }))
    const replacement = vi.fn(async () => ({ ...initial, initialized: true }))
    await bindSharedPicker('replaced-transport', old)
    setFavoriteModels(['obsolete::queued'])
    await bindSharedPicker('replaced-transport', replacement)
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(old).toHaveBeenCalledTimes(1)
    expect(replacement).toHaveBeenCalledTimes(1)
  })
  it('does not replay a CAS-losing pending edit during same-owner reopen', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    let state = { ...initial, initialized: true }
    const writes: unknown[] = []

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.update')) {writes.push(params); throw new Error('conflict')}

      return state
    }

    await bindSharedPicker('reopen-conflict', transport)
    setFavoriteModels(['stale::edit'])
    state = { ...state, revision: 1, favorites: ['winner::keep'] }
    await bindSharedPicker('reopen-conflict', transport)
    expect(writes).toEqual([{ expected_revision: 0, favorites: ['stale::edit'] }])
    expect($favoriteModels.get()).toEqual(['winner::keep'])
  })
  it('replaces the transport even for the same owner', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    const a = vi.fn(async () => ({ ...initial, initialized: true, favorites: ['a::one'] }))
    const b = vi.fn(async () => ({ ...initial, initialized: true, favorites: ['b::one'] }))
    await bindSharedPicker('reconnect', a)
    await bindSharedPicker('reconnect', b)
    expect($favoriteModels.get()).toEqual(['b::one'])
    setFavoriteModels(['b::two'])
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(2)
  })
  it('cancels old A hydration and queued writes across A/B/A', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    let finish!: (value: typeof initial) => void
    const old = vi.fn(() => new Promise<typeof initial>(resolve => { finish = resolve }))
    const pendingA = bindSharedPicker('aba-a', old)
    await Promise.resolve()
    await bindSharedPicker('aba-b', async () => ({ ...initial, initialized: true, favorites: ['b::one'] }))
    await bindSharedPicker('aba-a', async () => ({ ...initial, initialized: true, favorites: ['new-a::one'] }))
    finish({ ...initial, initialized: true, favorites: ['old-a::one'] })
    await pendingA
    expect($favoriteModels.get()).toEqual(['new-a::one'])
    setFavoriteModels(['queued::one'])
    await bindSharedPicker('aba-c', async () => ({ ...initial, initialized: true }))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(old).toHaveBeenCalledTimes(1)
  })
  it('never sends an old queued renderer edit to a newly bound owner', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { setFavoriteModels } = await import('./favorite-models')
    const a = vi.fn(async () => ({ ...initial, initialized: true }))
    const b = vi.fn(async () => ({ ...initial, initialized: true }))
    await bindSharedPicker('queue-a', a)
    setFavoriteModels(['a::queued'])
    const replacing = bindSharedPicker('queue-b', b)
    await replacing
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
  })
  it('falls back to the local presentation of this machine when the backend has no preference API', async () => {
    const { $sharedPickerStatus, bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    setFavoriteModels(['local::fav'])
    await bindSharedPicker('remote-x', async () => ({ ...initial, initialized: true, favorites: ['remote::won'] }))
    expect($favoriteModels.get()).toEqual(['remote::won'])
    const old = vi.fn(async () => { throw new Error('unknown method model.preferences.get') })
    await expect(bindSharedPicker('old-backend', old)).rejects.toThrow('unknown method')
    expect($sharedPickerStatus.get()).toEqual({ owner: 'old-backend', ready: true, shared: false })
    expect($favoriteModels.get()).toEqual(['local::fav'])
    setFavoriteModels(['local::fav', 'local::two'])
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(old).toHaveBeenCalledTimes(1)
  })
  it('rejects a non-preference payload instead of hydrating or importing over it', async () => {
    const transport = vi.fn(async () => ({ providers: [] }) as never)
    const sync = createPreferenceSync(transport, () => { throw new Error('must not hydrate') })
    await expect(sync.load({ favorites: ['a::b'] })).rejects.toThrow('preference')
    expect(transport).toHaveBeenCalledTimes(1)
  })
  it('does not replay a queued stale edit after a conflict', async () => {
    const remote = { ...initial, initialized: true, revision: 2, favorites: ['remote::won'] }
    let calls = 0

    const sync = createPreferenceSync(async method => {
      if (method.endsWith('.get')) {return calls ? remote : { ...initial, initialized: true }}
      calls++
      throw new Error('conflict')
    }, () => {})

    await sync.load()
    const first = sync.update({ favorites: ['local::first'] })
    const second = sync.update({ favorites: ['local::second'] })
    await expect(first).rejects.toThrow('conflict')
    await expect(second).rejects.toThrow('conflict')
    expect(calls).toBe(1)
  })
  it('hydrates existing stores and forwards renderer persistence edits', async () => {
    const { bindSharedPicker } = await import('./shared-picker')
    const { $favoriteModels, setFavoriteModels } = await import('./favorite-models')
    const { $visibleModels } = await import('./model-visibility')
    const { $customModels } = await import('./custom-models')
    let state = { ...initial, initialized: true, favorites: ['remote::one'], visibility: { visible: ['remote::'], known: ['remote::one'] }, custom_models: [{ provider: 'remote', model: 'typed:tag' }] }
    const writes: unknown[] = []

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.get')) {return state}
      writes.push(params)
      state = { ...state, ...params, revision: state.revision + 1 } as typeof state

      return state
    }

    await bindSharedPicker('test-owner', transport)
    expect($favoriteModels.get()).toEqual(['remote::one'])
    expect($visibleModels.get()).toEqual(new Set(['remote::']))
    expect($customModels.get()).toEqual([{ provider: 'remote', model: 'typed:tag' }])
    setFavoriteModels(['remote::two'])
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(writes).toEqual([{ expected_revision: 0, favorites: ['remote::two'] }])
  })
  it('imports once and patches fields with revision CAS, never rebases a conflict', async () => {
    let state = { ...initial }
    const writes: unknown[] = []

    const transport = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      if (method.endsWith('.get')) {return state}
      writes.push(params)

      if (params?.expected_revision !== state.revision) {throw new Error('conflict')}
      state = { ...state, ...params, initialized: true, revision: state.revision + 1 } as typeof state

      return state
    })

    const hydrate = vi.fn()
    const sync = createPreferenceSync(transport, hydrate)
    await sync.load({ favorites: ['gone::model:tag'] })
    expect(state.favorites).toEqual(['gone::model:tag'])
    await sync.update({ favorites: [] })
    expect(state.favorites).toEqual([])
    expect(writes).toEqual([
      { expected_revision: 0, import_once: true, favorites: ['gone::model:tag'] },
      { expected_revision: 1, favorites: [] }
    ])
    state = { ...state, revision: 3, favorites: ['remote::won'] as never[] }
    await expect(sync.update({ favorites: ['local::stale'] })).rejects.toThrow('conflict')
    expect(writes).toHaveLength(3)
    expect(hydrate).toHaveBeenLastCalledWith(state)
  })
})
