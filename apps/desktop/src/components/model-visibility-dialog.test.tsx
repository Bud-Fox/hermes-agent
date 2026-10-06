import type { ModelOptionsResult } from '@hermes/shared'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection } from '@/api/client'
import { ModelVisibilityOverlay } from '@/app/model-visibility-overlay'
import { ConfirmHost } from '@/components/confirm-host'
import { I18nProvider } from '@/i18n'
import { $confirmRequest } from '@/store/confirm'
import { $customModels, addCustomModel } from '@/store/custom-models'
import { requestGatewayForAgent } from '@/store/gateway'
import { $knownModels, $visibleModels, modelVisibilityKey, setModelVisibilityOpen, setVisibleModels } from '@/store/model-visibility'
import { $activeSessionId, $gatewayState } from '@/store/session'
import { bindSharedPicker } from '@/store/shared-picker'
import { stubResizeObserver } from '@/test/jsdom'

import { ModelVisibilityDialog } from './model-visibility-dialog'

vi.mock('@/store/gateway', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn()
}))

vi.mock('@/lib/model-options', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestModelOptions: vi.fn()
}))

import { requestModelOptions } from '@/lib/model-options'

stubResizeObserver()

const OPTIONS: ModelOptionsResult = {
  providers: [
    { authenticated: true, models: ['gpt-5.5', 'gpt-6'], name: 'OpenAI Codex', slug: 'openai-codex' },
    { authenticated: true, models: ['qwen3-coder'], name: 'Qwen', slug: 'qwen' }
  ]
}

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <ModelVisibilityDialog onOpenChange={() => {}} onOpenProviders={() => {}} open />
        <ConfirmHost />
      </I18nProvider>
    </QueryClientProvider>
  )
}

const isShown = async (label: RegExp) =>
  (await screen.findByRole('switch', { name: label })).getAttribute('aria-checked') === 'true'

beforeEach(async () => {
  let preferences = { version: 1, revision: 0, initialized: true, favorites: [] as string[], visibility: { visible: null, known: null } as { visible: string[] | null; known: string[] | null }, custom_models: [] }
  await bindSharedPicker('local:default', async (_method, params) => {
    if (params) {preferences = { ...preferences, ...params, revision: preferences.revision + 1 } as typeof preferences}

    return preferences
  })
  window.localStorage.clear()
  $customModels.set([])
  $visibleModels.set(null)
  $knownModels.set(null)
  vi.mocked(requestModelOptions).mockResolvedValue(OPTIONS)
})

afterEach(() => {
  cleanup()
  $confirmRequest.set(null)
  vi.clearAllMocks()
})

describe('Edit Models authority', () => {
  it('removes visibility/custom controls while loading and after an owner switch', async () => {
    const state = { version: 1, revision: 0, initialized: true, favorites: [] as string[], visibility: { visible: null, known: null }, custom_models: [] }
    let finish!: (value: typeof state) => void
    const loading = bindSharedPicker('local:default', () => new Promise(resolve => { finish = resolve }))
    await Promise.resolve()
    renderDialog()
    expect(await screen.findByText('Shared model preferences are loading. Reopen this picker if loading fails.')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
    await act(async () => { finish(state); await loading })
    expect(await screen.findByRole('switch', { name: /gpt-6/i })).toBeTruthy()
    await act(async () => { await bindSharedPicker('other:default', async () => state) })
    expect(await screen.findByText('This picker belongs to another connection. Close and reopen it before editing shared models.')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('button', { name: /reset to defaults/i })).toBeNull()
  })
})

describe('Edit Models captured owner catalog', () => {
  const catalogA = { providers: [{ slug: 'p', name: 'Provider A', models: ['A-only'], featured_models: [] }] }
  const catalogB = { providers: [{ slug: 'p', name: 'Provider B', models: ['B-only'], featured_models: [] }] }

  async function renderOwnerOverlay() {
    const { requestModelOptions: actual } = await vi.importActual<{ requestModelOptions: typeof requestModelOptions }>('@/lib/model-options')
    vi.mocked(requestModelOptions).mockImplementation(actual)
    let preferences = { version: 1, revision: 0, initialized: true, favorites: [] as string[], visibility: { visible: ['p::A-only', 'orphan::keep'], known: null } as { visible: string[] | null; known: string[] | null }, custom_models: [] }
    const writes: Record<string, unknown>[] = []

    const transport = async (method: string, params?: Record<string, unknown>) => {
      if (method === 'model.options') {return catalogA as never}

      if (params && method.endsWith('.update')) {
        writes.push(params)
        preferences = { ...preferences, ...params, revision: preferences.revision + 1 } as typeof preferences
      }

      return preferences
    }

    vi.mocked(requestGatewayForAgent).mockImplementation((_connection, _profile, method, params) => transport(method, params) as never)
    await bindSharedPicker('A:default', transport)
    setApiRequestConnection('B')
    $gatewayState.set('open')
    $activeSessionId.set('ambient-B-session')
    setModelVisibilityOpen(true, { connection: 'A', profile: 'default' })
    const ambient = { request: vi.fn(async () => catalogB) }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><I18nProvider><ModelVisibilityOverlay gateway={ambient as never} onOpenProviders={() => {}} profile="default" /><ConfirmHost /></I18nProvider></QueryClientProvider>)
    await screen.findByRole('switch')

    return { ambient, writes }
  }

  afterEach(() => {
    setApiRequestConnection(null)
    setModelVisibilityOpen(false)
  })

  it('seeds known and enables provider families from A, never ambient B', async () => {
    const { ambient, writes } = await renderOwnerOverlay()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('checkbox'))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect($visibleModels.get()).toEqual(new Set(['orphan::keep', 'p::A-only']))
    expect($knownModels.get()).toEqual(new Set(['p::A-only']))
    expect(JSON.stringify(writes)).not.toContain('B-only')
    expect(ambient.request).not.toHaveBeenCalled()
    expect(requestGatewayForAgent).toHaveBeenCalledWith('A', 'default', 'model.options', { explicit_only: true, profile: 'default' })
  })

  it('fails closed before known seeding or edits when an explicit owner has no inventory route', async () => {
    await bindSharedPicker('A:default', async () => ({ version: 1, revision: 0, initialized: true, favorites: [], visibility: { visible: ['orphan::keep'], known: null }, custom_models: [] }))
    vi.mocked(requestModelOptions).mockResolvedValue({ providers: [{ slug: 'p', name: 'Wrong B catalog', models: ['B-only'] }] })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><I18nProvider><ModelVisibilityDialog onOpenChange={() => {}} onOpenProviders={() => {}} open ownerConnectionId="A" /></I18nProvider></QueryClientProvider>)
    await screen.findByText('The model catalog owner route is unavailable. Close and reopen this picker.')
    expect($knownModels.get()).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('button', { name: /reset to defaults/i })).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /typed-model/i })).toBeNull()
  })

  it('keeps the captured owner session instead of the ambient session', async () => {
    const { requestModelOptions: actual } = await vi.importActual<{ requestModelOptions: typeof requestModelOptions }>('@/lib/model-options')
    vi.mocked(requestModelOptions).mockImplementation(actual)
    vi.mocked(requestGatewayForAgent).mockImplementation(async (_connection, _profile, method) => method === 'model.options' ? catalogA as never : { version: 1, revision: 0, initialized: true, favorites: [], visibility: { visible: null, known: null }, custom_models: [] } as never)
    setApiRequestConnection('B')
    $gatewayState.set('open')
    $activeSessionId.set('ambient-B-session')
    setModelVisibilityOpen(true, { connection: 'A', profile: 'default', sessionId: 'captured-A-session' })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><I18nProvider><ModelVisibilityOverlay onOpenProviders={() => {}} profile="default" /></I18nProvider></QueryClientProvider>)
    await screen.findByRole('switch')
    expect(requestGatewayForAgent).toHaveBeenCalledWith('A', 'default', 'model.options', { explicit_only: true, profile: 'default', session_id: 'captured-A-session' })
  })

  it('resets and adds custom models using A catalog without B known/visible keys', async () => {
    const { ambient, writes } = await renderOwnerOverlay()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'typed-model' } })
    fireEvent.click(await screen.findByRole('button', { name: /typed-model.*Provider A/i }))
    await new Promise(resolve => setTimeout(resolve, 0))
    fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^reset$/i }))
    await waitFor(() => expect($confirmRequest.get()).toBeNull())
    await new Promise(resolve => setTimeout(resolve, 0))
    expect($customModels.get()).toEqual([{ provider: 'p', model: 'typed-model' }])
    expect($visibleModels.get()).toBeNull()
    expect($knownModels.get()).toBeNull()
    expect(JSON.stringify(writes)).not.toContain('B-only')
    expect(ambient.request).not.toHaveBeenCalled()
  })
})

describe('Edit Models reset', () => {
  it('restores the default shortlist when a model is stuck hidden by the known snapshot', async () => {
    // A curation saved while the snapshot machinery was live: gpt-6 was listed
    // when the user kept gpt-5.5 and switched it off, so the snapshot counts it
    // as judged and the default rule never re-admits it — the dialog shows it
    // switched off with no per-model way back that survives a catalog change.
    setVisibleModels(new Set([modelVisibilityKey('openai-codex', 'gpt-5.5')]), OPTIONS.providers!)

    renderDialog()

    expect(await isShown(/gpt-6/i)).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^reset$/i }))

    await waitFor(async () => expect(await isShown(/gpt-6/i)).toBe(true))
    expect(await isShown(/gpt-5\.5/i)).toBe(true)
    expect(window.localStorage.getItem('hermes.desktop.visible-models')).toBeNull()
    expect(window.localStorage.getItem('hermes.desktop.known-models')).toBeNull()
  })

  it('keeps the user’s choices when the reset is cancelled', async () => {
    setVisibleModels(new Set([modelVisibilityKey('openai-codex', 'gpt-5.5')]), OPTIONS.providers!)

    renderDialog()
    await isShown(/gpt-6/i)

    fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
    fireEvent.click(await screen.findByRole('button', { name: /cancel/i }))

    await waitFor(() => expect($confirmRequest.get()).toBeNull())
    expect(await isShown(/gpt-6/i)).toBe(false)
  })

  it('keeps a custom model stored and switched on where the defaults would hide it', async () => {
    // An aggregator row defaults to its featured shortlist; the typed id is
    // appended after the catalog, so the bare default rule would hide it.
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          authenticated: true,
          featured_models: ['openai/gpt-6'],
          models: ['openai/gpt-5', 'openai/gpt-6', 'anthropic/claude-x'],
          name: 'OpenRouter',
          slug: 'openrouter'
        }
      ]
    })
    addCustomModel('openrouter', 'acme/model-x')
    setVisibleModels(new Set(['openrouter::acme/model-x']), [{ models: ['openai/gpt-5', 'openai/gpt-6', 'anthropic/claude-x', 'acme/model-x'], featured_models: ['openai/gpt-6'], name: 'OpenRouter', slug: 'openrouter' }])
    await new Promise(resolve => setTimeout(resolve, 0))

    renderDialog()

    expect(await isShown(/gpt-6/i)).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^reset$/i }))

    await waitFor(async () => expect(await isShown(/gpt-6/i)).toBe(true))
    // The custom row's <label> also wraps its remove button, so its switch has
    // no accessible name of its own; find it through the row instead.
    const customRow = screen.getByText('Model X').closest('label')!
    expect(within(customRow).getByRole('switch').getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('button', { name: /remove custom model/i })).toBeTruthy()
    expect($customModels.get()).toEqual([{ model: 'acme/model-x', provider: 'openrouter' }])
  })

  it('offers no reset while the list is still the default', async () => {
    renderDialog()

    expect(await isShown(/gpt-6/i)).toBe(true)
    expect(screen.queryByRole('button', { name: /reset to defaults/i })).toBeNull()
  })

  it('does not offer reset before the provider catalog has loaded', () => {
    $visibleModels.set(new Set([modelVisibilityKey('openai-codex', 'gpt-5.5')]))
    vi.mocked(requestModelOptions).mockReturnValue(new Promise(() => {}))

    renderDialog()

    expect(screen.queryByRole('button', { name: /reset to defaults/i })).toBeNull()
  })
})
