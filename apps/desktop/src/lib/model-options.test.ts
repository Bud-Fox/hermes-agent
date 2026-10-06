import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getGlobalModelOptions } from '@/hermes'

import { catalogProviderMatches, customDefaultSupersedesPick, moaPickRemoved, modelOptionsQueryKey, pickerModelList, requestModelOptions } from './model-options'

const globalOptions = { model: 'hermes-4', provider: 'nous', providers: [] }

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: vi.fn(() => Promise.resolve(globalOptions))
}))

describe('requestModelOptions', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('uses the connected gateway even before a session exists', async () => {
    const gatewayPayload = {
      model: 'BeastMode',
      provider: 'moa',
      providers: [{ models: ['BeastMode'], name: 'Mixture of Agents', slug: 'moa' }]
    }

    const gateway = {
      request: vi.fn(() => Promise.resolve(gatewayPayload))
    }

    await expect(requestModelOptions({ gateway: gateway as never, sessionId: null })).resolves.toBe(gatewayPayload)

    expect(gateway.request).toHaveBeenCalledWith('model.options', { explicit_only: true })
    expect(getGlobalModelOptions).not.toHaveBeenCalled()
  })

  it('recovers an empty gateway catalog through profile-scoped REST without replacing the session selection', async () => {
    const gatewayPayload = { model: 'hermes-local', provider: 'hermes-local' }

    const restPayload = {
      model: 'profile-default',
      provider: 'openai-codex',
      providers: [{ models: ['hermes-local'], name: 'Hermes Local vLLM', slug: 'hermes-local' }]
    }

    const gateway = {
      request: vi.fn(() => Promise.resolve(gatewayPayload))
    }

    vi.mocked(getGlobalModelOptions).mockResolvedValueOnce(restPayload)

    await expect(requestModelOptions({ gateway: gateway as never, sessionId: 'session-1' })).resolves.toEqual({
      ...restPayload,
      model: 'hermes-local',
      provider: 'hermes-local'
    })

    expect(getGlobalModelOptions).toHaveBeenCalledWith({ explicitOnly: true })
  })

  it('recovers through profile-scoped REST when the gateway catalog request fails', async () => {
    const restPayload = {
      model: 'hermes-local',
      provider: 'hermes-local',
      providers: [{ models: ['hermes-local'], name: 'Hermes Local vLLM', slug: 'hermes-local' }]
    }

    const gateway = {
      request: vi.fn(() => Promise.reject(new Error('gateway request unavailable')))
    }

    vi.mocked(getGlobalModelOptions).mockResolvedValueOnce(restPayload)

    await expect(requestModelOptions({ gateway: gateway as never, sessionId: 'session-1' })).resolves.toEqual(
      restPayload
    )
    expect(getGlobalModelOptions).toHaveBeenCalledWith({ explicitOnly: true })
  })

  it('preserves the gateway error when its REST recovery path also fails', async () => {
    const gatewayError = new Error('gateway request unavailable')

    const gateway = {
      request: vi.fn(() => Promise.reject(gatewayError))
    }

    vi.mocked(getGlobalModelOptions).mockRejectedValueOnce(new Error('REST request unavailable'))

    await expect(requestModelOptions({ gateway: gateway as never })).rejects.toBe(gatewayError)
  })

  it('keeps the gateway result when both catalog paths have no selectable models', async () => {
    const gatewayPayload = { model: 'hermes-local', provider: 'hermes-local', providers: [] }

    const gateway = {
      request: vi.fn(() => Promise.resolve(gatewayPayload))
    }

    await expect(requestModelOptions({ gateway: gateway as never })).resolves.toBe(gatewayPayload)
  })

  it('passes the active session id and refresh flag through the gateway', async () => {
    const gateway = {
      request: vi.fn(() => Promise.resolve(globalOptions))
    }

    await requestModelOptions({ gateway: gateway as never, refresh: true, sessionId: 'session-1' })

    expect(gateway.request).toHaveBeenCalledWith('model.options', {
      explicit_only: true,
      refresh: true,
      session_id: 'session-1'
    })
    expect(getGlobalModelOptions).toHaveBeenCalledWith({ explicitOnly: true, refresh: true })
  })

  it('passes the catalog owner profile through the shared gateway RPC', async () => {
    const gateway = {
      request: vi.fn(() => Promise.resolve(globalOptions))
    }

    await requestModelOptions({ gateway: gateway as never, profile: 'fred-work' })

    expect(gateway.request).toHaveBeenCalledWith('model.options', {
      explicit_only: true,
      profile: 'fred-work'
    })
  })

  it('falls back to REST when no gateway is connected', async () => {
    await requestModelOptions({ refresh: true })

    expect(getGlobalModelOptions).toHaveBeenCalledWith({ explicitOnly: true, refresh: true })
  })

  it('prefers an owner-routed request over the ambient gateway socket', async () => {
    const gatewayPayload = {
      model: 'chrome-model',
      provider: 'nous',
      providers: [{ models: ['chrome-model'], name: 'Nous', slug: 'nous' }]
    }

    const routedPayload = {
      model: 'berry-model',
      provider: 'openai',
      providers: [{ models: ['berry-model'], name: 'OpenAI', slug: 'openai' }]
    }

    const gateway = {
      request: vi.fn(() => Promise.resolve(gatewayPayload))
    }

    const request = vi.fn(() => Promise.resolve(routedPayload)) as unknown as <T>(
      method: string,
      params?: Record<string, unknown>
    ) => Promise<T>

    await expect(requestModelOptions({ gateway: gateway as never, request, sessionId: 'tile-1' })).resolves.toBe(
      routedPayload
    )

    expect(request).toHaveBeenCalledWith('model.options', { explicit_only: true, session_id: 'tile-1' })
    expect(gateway.request).not.toHaveBeenCalled()
  })

  it('does not recover an owner-routed failure through the ambient REST connection', async () => {
    const ownerError = new Error('owner gateway unavailable')
    const request = vi.fn(() => Promise.reject(ownerError))

    await expect(requestModelOptions({ profile: 'berry', request, sessionId: 'tile-1' })).rejects.toBe(ownerError)
    expect(getGlobalModelOptions).not.toHaveBeenCalled()
  })

  it('keeps an empty owner-routed catalog instead of replacing it from ambient REST', async () => {
    const ownerPayload = { model: 'berry-local', provider: 'hermes-local', providers: [] }

    const request = vi.fn(() => Promise.resolve(ownerPayload)) as unknown as <T>(
      method: string,
      params?: Record<string, unknown>
    ) => Promise<T>

    await expect(requestModelOptions({ profile: 'berry', request, sessionId: 'tile-1' })).resolves.toBe(ownerPayload)
    expect(getGlobalModelOptions).not.toHaveBeenCalled()
  })
})

describe('modelOptionsQueryKey', () => {
  it('isolates new-chat catalogs by active gateway profile', () => {
    expect(modelOptionsQueryKey('default')).not.toEqual(modelOptionsQueryKey('compass'))
  })

  it('keeps session catalogs inside the owning profile namespace', () => {
    expect(modelOptionsQueryKey(' compass ', 'session-1')).toEqual(modelOptionsQueryKey('compass', 'session-1'))
    expect(modelOptionsQueryKey('compass', 'session-1')).not.toEqual(modelOptionsQueryKey('default', 'session-1'))
  })

  it('isolates identical profile and session names across registry connections', () => {
    const sourceAKey = modelOptionsQueryKey('default', 'session-1', 'source-a')
    const sourceBKey = modelOptionsQueryKey('default', 'session-1', 'source-b')
    const queryClient = new QueryClient()

    expect(sourceAKey).not.toEqual(sourceBKey)
    queryClient.setQueryData(sourceAKey, { providers: [{ models: ['a/model'], slug: 'a' }] })
    queryClient.setQueryData(sourceBKey, { providers: [{ models: ['b/model'], slug: 'b' }] })

    expect(queryClient.getQueryData(sourceAKey)).toMatchObject({ providers: [{ models: ['a/model'] }] })
    expect(queryClient.getQueryData(sourceBKey)).toMatchObject({ providers: [{ models: ['b/model'] }] })
  })
})

describe('catalogProviderMatches', () => {
  const cloudflare = {
    aliases: ['custom:cloudflare', 'cloudflare'],
    models: ['@cf/meta/llama-3.3-70b-instruct-fp8-fast'],
    name: 'Cloudflare',
    slug: 'cloudflare'
  }

  it('matches slug, display name, and custom-provider aliases', () => {
    expect(catalogProviderMatches(cloudflare, 'cloudflare')).toBe(true)
    expect(catalogProviderMatches(cloudflare, 'Cloudflare')).toBe(true)
    expect(catalogProviderMatches(cloudflare, 'custom:cloudflare')).toBe(true)
    expect(catalogProviderMatches(cloudflare, 'openrouter')).toBe(false)
  })
})

describe('pickerModelList', () => {
  const full = ['a-model', 'b-model', 'c-model']

  it('returns server picker_models when present (filtered picker view)', () => {
    const provider = { models: full, picker_models: ['a-model'], name: 'X', slug: 'x' }
    expect(pickerModelList(provider)).toEqual(['a-model'])
  })

  it('falls back to full models when picker_models is absent (older backend / filter off)', () => {
    const provider = { models: full, name: 'X', slug: 'x' }
    expect(pickerModelList(provider)).toEqual(full)
  })

  it('falls back to full models when picker_models is malformed (not a string array)', () => {
    const provider = { models: full, picker_models: [1, 2], name: 'X', slug: 'x' }
    expect(pickerModelList(provider)).toEqual(full)
  })

  it('honors an explicit empty picker_models (server hid every model for this provider)', () => {
    const provider = { models: full, picker_models: [], name: 'X', slug: 'x' }
    expect(pickerModelList(provider)).toEqual([])
  })
})

describe('moaPickRemoved', () => {
  const providers = [
    { models: ['deepseek-v4-pro'], name: 'DeepSeek', slug: 'deepseek' },
    { models: ['default', 'balanced'], name: 'Mixture of Agents', slug: 'moa' }
  ]

  it('flags a manual moa pick when the populated catalog has no moa row (#90244)', () => {
    const noMoa = [providers[0]]
    expect(moaPickRemoved({ providers: noMoa }, 'moa', 'default')).toBe(true)
  })

  it('flags a manual moa pick whose preset the moa row no longer lists', () => {
    expect(moaPickRemoved({ providers }, 'moa', 'retired-preset')).toBe(true)
  })

  it('keeps a manual moa pick while the catalog still offers the preset', () => {
    expect(moaPickRemoved({ providers }, 'moa', 'default')).toBe(false)
    expect(moaPickRemoved({ providers }, 'MOA', 'balanced')).toBe(false)
  })

  it('never clobbers while the catalog is unavailable or loading', () => {
    expect(moaPickRemoved(undefined, 'moa', 'default')).toBe(false)
    expect(moaPickRemoved({ providers: [] }, 'moa', 'default')).toBe(false)
    expect(moaPickRemoved({ providers: undefined }, 'moa', 'default')).toBe(false)
  })

  it('leaves every non-moa provider to the sticky-pick design', () => {
    // A custom slug the catalog lacks is the user's choice, not a removal
    // (d595e636c83: picks are never retargeted from catalog membership).
    expect(moaPickRemoved({ providers: [providers[0]] }, 'deepseek', 'deepseek-v4.1-flash')).toBe(false)
    expect(moaPickRemoved({ providers: [providers[0]] }, 'custom', 'my-own-slug')).toBe(false)
    expect(moaPickRemoved({ providers: [providers[0]] }, '', 'default')).toBe(false)
  })
})

describe('customDefaultSupersedesPick', () => {
  it('flags a bare pick the default has migrated to its custom-provider form (#81922)', () => {
    // The wire payload for `nvidia` builds the NATIVE provider and drops the
    // custom entry's extra_body; `custom:nvidia` is the same endpoint.
    expect(customDefaultSupersedesPick('nvidia', 'custom:nvidia')).toBe(true)
    expect(customDefaultSupersedesPick('  NVIDIA ', 'Custom:NVIDIA')).toBe(true)
  })

  it('keeps a pick that already names the custom entry, or a different provider', () => {
    expect(customDefaultSupersedesPick('custom:nvidia', 'custom:nvidia')).toBe(false)
    expect(customDefaultSupersedesPick('custom:relay', 'custom:nvidia')).toBe(false)
    expect(customDefaultSupersedesPick('anthropic', 'custom:nvidia')).toBe(false)
  })

  it('never fires for a non-custom default or an empty pick', () => {
    expect(customDefaultSupersedesPick('nvidia', 'nvidia')).toBe(false)
    expect(customDefaultSupersedesPick('custom', 'custom')).toBe(false)
    expect(customDefaultSupersedesPick('nvidia', 'openai-codex')).toBe(false)
    expect(customDefaultSupersedesPick('', 'custom:nvidia')).toBe(false)
    expect(customDefaultSupersedesPick('nvidia', 'custom:')).toBe(false)
  })
})

describe('shared picker binding identity', () => {
  const prefs = { version: 1, revision: 0, initialized: true, favorites: [] as string[], visibility: { visible: null, known: null }, custom_models: [] }
  const catalog = { providers: [{ models: ['m'], name: 'P', slug: 'p' }] }

  afterEach(async () => {
    const { setApiRequestConnection } = await import('@/api/client')
    setApiRequestConnection(null)
    delete (window as { hermesDesktop?: unknown }).hermesDesktop
    vi.clearAllMocks()
  })

  it('labels a routed request without an owner id as the ambient connection, never local', async () => {
    const { setApiRequestConnection } = await import('@/api/client')
    const { $sharedPickerStatus } = await import('@/store/shared-picker')
    setApiRequestConnection('remote-b')
    const request = vi.fn(async (method: string) => (method === 'model.options' ? catalog : prefs)) as never
    await requestModelOptions({ request })
    expect($sharedPickerStatus.get()).toEqual({ owner: 'remote-b:default', ready: true, shared: true })
  })

  it('preserves a pending edit when catalog refresh recreates its preference wrapper', async () => {
    const { setFavoriteModels, $favoriteModels } = await import('@/store/favorite-models')
    let state = { ...prefs }

    const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      if (method === 'model.options') {return catalog}

      if (method === 'model.preferences.update') {state = { ...state, ...params, revision: state.revision + 1 } as typeof state}

      return state
    })

    await requestModelOptions({ request: request as never, ownerConnectionId: 'refresh-a' })
    setFavoriteModels(['p::m'])
    await requestModelOptions({ request: request as never, ownerConnectionId: 'refresh-a' })
    expect(request.mock.calls.filter(([method]) => method === 'model.preferences.update')).toHaveLength(1)
    expect($favoriteModels.get()).toEqual(['p::m'])
  })

  it('does not relabel the ambient socket as a different explicit owner', async () => {
    const { $sharedPickerStatus, bindSharedPicker } = await import('@/store/shared-picker')
    await bindSharedPicker('tile-x:default', async () => prefs)
    const gateway = { request: vi.fn(async (method: string) => (method === 'model.options' ? catalog : prefs)) }
    await requestModelOptions({ gateway: gateway as never, ownerConnectionId: 'tile-y' })
    expect(gateway.request.mock.calls.map(([method]) => method)).toEqual(['model.options'])
    expect($sharedPickerStatus.get()).toEqual({ owner: 'tile-x:default', ready: true, shared: true })
  })

  it('names the ambient gateway connection, not local, as the owner', async () => {
    const { setApiRequestConnection } = await import('@/api/client')
    const { $sharedPickerStatus, sharedPickerOwner } = await import('@/store/shared-picker')
    setApiRequestConnection('remote-b')
    const gateway = { request: vi.fn(async (method: string) => (method === 'model.options' ? catalog : prefs)) }
    await requestModelOptions({ gateway: gateway as never })
    expect($sharedPickerStatus.get()).toEqual({ owner: 'remote-b:default', ready: true, shared: true })
    expect(sharedPickerOwner(undefined, undefined)).toBe('remote-b:default')
  })

  it('pins REST preference writes to the connection that hydrated them', async () => {
    const { setApiRequestConnection } = await import('@/api/client')
    const { $sharedPickerStatus } = await import('@/store/shared-picker')
    const { setFavoriteModels } = await import('@/store/favorite-models')

    const api = vi.fn(async (_request: Record<string, unknown>) => prefs)

    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    setApiRequestConnection('remote-b')
    vi.mocked(getGlobalModelOptions).mockResolvedValueOnce(catalog as never)
    await requestModelOptions({})
    expect($sharedPickerStatus.get()).toEqual({ owner: 'remote-b:default', ready: true, shared: true })
    setApiRequestConnection('remote-c')
    setFavoriteModels(['p::m'])
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(api.mock.calls.map(([call]) => [call.method ?? 'GET', call.connectionId])).toEqual([['GET', 'remote-b'], ['POST', 'remote-b']])
  })
})
