import type { ModelOptionsResult } from '@hermes/shared'
import { fuzzyRank, modelSearchText } from '@hermes/shared'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $localModelsEnabled } from '@/store/local-models-flag'
import { localModelsKey, localModelsOwner } from '@/store/local-runtime-jobs'
import { bindSharedPicker } from '@/store/shared-picker'
import { stubMenuDomApis, stubResizeObserver } from '@/test/jsdom'
import type { LocalRuntimeJob } from '@/types/hermes'

import { ModelPickerDialog } from './model-picker'

// The jobs query refetches on mount and would replace a seeded cache entry with
// whatever the backend answers; answering with the seeded jobs keeps the two equal.
const seededJobs: { current: readonly LocalRuntimeJob[] } = vi.hoisted(() => ({ current: [] }))

vi.mock('@/store/local-runtime-jobs', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  $localRuntimeJobs: { get: () => [], listen: () => () => {} },
  watchLocalRuntimeJobs: vi.fn()
}))
vi.mock('@/hermes', () => ({
  getLocalModelsJobs: vi.fn(async () => ({ jobs: [...seededJobs.current] })),
  getLocalModelsStatus: vi.fn().mockResolvedValue({ loading: {} })
}))
vi.mock('@/lib/model-options', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestModelOptions: vi.fn()
}))

import { modelOptionsQueryKey, requestModelOptions } from '@/lib/model-options'

stubResizeObserver()
stubMenuDomApis()

const OPTIONS: ModelOptionsResult = {
  model: 'Qwen3.6-27B-UD-Q4_K_XL',
  provider: 'llamacpp',
  providers: [
    {
      slug: 'llamacpp',
      name: 'Local',
      models: ['Qwen3.6-27B-UD-Q4_K_XL'],
      is_current: true,
      authenticated: true
    },
    {
      slug: 'nous',
      name: 'Nous',
      models: ['Hermes-4.5'],
      authenticated: true
    }
  ]
}

const DOWNLOAD_JOB: LocalRuntimeJob = {
  job_id: 'dl1',
  kind: 'model-download',
  target: 'Qwen3.8 Flash Next (UD-Q4_K_XL)',
  model_id: 'qwen3.8-flash-next',
  status: 'running',
  phase: 'downloading',
  detail: '',
  total_bytes: 100,
  done_bytes: 41,
  percent: 41,
  error: null
}

// One client per test, created in beforeEach: the tests seed jobs BEFORE
// rendering, so the picker must mount against the client that was seeded.
let client: QueryClient = new QueryClient()

function setRuntimeJobs(jobs: readonly LocalRuntimeJob[]): void {
  // The jobs store keeps jobs in react-query, not an atom: seed the cache
  // directly the way production populates it (localModelsKey(owner, 'jobs')).
  seededJobs.current = jobs
  client.setQueryData(localModelsKey(localModelsOwner(), 'jobs'), jobs)
}

function renderPicker(ui?: Partial<Parameters<typeof ModelPickerDialog>[0]>) {
  const element: ReactElement = (
    <QueryClientProvider client={client}>
      <I18nProvider>
        <ModelPickerDialog
          currentModel="Qwen3.6-27B-UD-Q4_K_XL"
          currentProvider="llamacpp"
          onOpenChange={() => undefined}
          onSelect={() => undefined}
          open
          {...ui}
        />
      </I18nProvider>
    </QueryClientProvider>
  )

  return render(element)
}

beforeEach(async () => {
  await bindSharedPicker('local:default', async () => ({ version: 1, revision: 0, initialized: true, favorites: [], visibility: { visible: null, known: null }, custom_models: [] }))
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(requestModelOptions).mockResolvedValue(OPTIONS)
  setRuntimeJobs([])
  // These suites exercise the local-models rows, which ship behind --local.
  $localModelsEnabled.set(true)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ModelPickerDialog shared readiness', () => {
  it('gates real custom controls during hydration and invalidates an incompatible open picker', async () => {
    let finish!: (value: { version: number; revision: number; initialized: boolean; favorites: string[]; visibility: { visible: null; known: null }; custom_models: [] }) => void
    const state = { version: 1, revision: 0, initialized: true, favorites: [] as string[], visibility: { visible: null, known: null }, custom_models: [] as [] }
    let first = true

    const loading = bindSharedPicker('local:default', () => {
      if (!first) {return Promise.resolve(state)}
      first = false

      return new Promise(resolve => { finish = resolve })
    })

    await Promise.resolve()
    renderPicker()
    expect(await screen.findByText('Shared model preferences are loading. Reopen this picker if loading fails.')).toBeTruthy()
    expect(screen.queryByText('Hermes-4.5')).toBeNull()
    await act(async () => { finish(state); await loading })
    expect(await screen.findByText('Hermes-4.5')).toBeTruthy()
    await act(async () => { await bindSharedPicker('installation-b:default', async () => state) })
    expect(await screen.findByText('This picker belongs to another connection. Close and reopen it before editing shared models.')).toBeTruthy()
    expect(screen.queryByText('Hermes-4.5')).toBeNull()
  })
})

describe('ModelPickerDialog download rows', () => {
  it('shows an in-flight download as a disabled progress row in the Local group', async () => {
    setRuntimeJobs([DOWNLOAD_JOB])
    renderPicker()

    expect(await screen.findByText('Qwen3.6-27B-UD-Q4_K_XL')).toBeTruthy()

    const row = screen.getByText('Qwen3.8 Flash Next (UD-Q4_K_XL)')

    expect(row).toBeTruthy()
    expect(screen.getByText('41%')).toBeTruthy()

    // Disabled: cmdk marks the item unselectable.
    const item = row.closest('[cmdk-item]')

    expect(item?.getAttribute('aria-disabled')).toBe('true')
  })

  it('shows a first-ever download under its own Local group when no local provider exists yet', async () => {
    setRuntimeJobs([DOWNLOAD_JOB])
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [OPTIONS.providers![1]]
    })
    renderPicker()

    expect(await screen.findByText('Hermes-4.5')).toBeTruthy()
    expect(screen.getByText('Qwen3.8 Flash Next (UD-Q4_K_XL)')).toBeTruthy()
    expect(screen.getByText('41%')).toBeTruthy()
  })

  it('quickstart shows while downloading but not during later phases', async () => {
    const quickstart: LocalRuntimeJob = { ...DOWNLOAD_JOB, job_id: 'q1', kind: 'quickstart', phase: 'downloading' }

    setRuntimeJobs([quickstart])
    renderPicker()
    expect(await screen.findByText('Qwen3.8 Flash Next (UD-Q4_K_XL)')).toBeTruthy()

    // The model is staged once quickstart moves on to activating it — the
    // placeholder row must leave rather than sit beside the real model.
    setRuntimeJobs([{ ...quickstart, phase: 'starting-server' }])
    await waitFor(() => {
      expect(screen.queryByText('Qwen3.8 Flash Next (UD-Q4_K_XL)')).toBeNull()
    })
  })

  it('refetches the model options when a download it saw running completes', async () => {
    setRuntimeJobs([DOWNLOAD_JOB])
    renderPicker()
    await screen.findByText('Qwen3.6-27B-UD-Q4_K_XL')

    expect(vi.mocked(requestModelOptions).mock.calls.length).toBe(1)

    setRuntimeJobs([{ ...DOWNLOAD_JOB, status: 'done', phase: 'done' }])
    await waitFor(() => {
      expect(vi.mocked(requestModelOptions).mock.calls.length).toBe(2)
    })
  })
})



describe('ModelPickerDialog readiness overlay', () => {
  // The Python health overlay annotates each provider row in place with
  // snake_case keys (glyph/readiness/has_million_ready/preferred_model). The
  // generated ModelOptionProvider carries `[key: string]: unknown`, so a
  // fixture can set them directly. `nous` is the top ready+1M provider and
  // carries a preferred_model that is NOT its first row, so a passing cursor
  // assertion can only come from the wiring (not from the default first-item
  // selection).
  const READY_OPTIONS: ModelOptionsResult = {
    model: 'Qwen3.6-27B-UD-Q4_K_XL',
    provider: 'llamacpp',
    providers: [
      {
        slug: 'nous',
        name: 'Nous',
        models: ['glm-4.6-omni', 'gemini-3.8-flash'],
        authenticated: true,
        glyph: '●',
        readiness: 'ready',
        has_million_ready: true,
        preferred_model: 'gemini-3.8-flash'
      },
      {
        slug: 'llamacpp',
        name: 'Local',
        models: ['Qwen3.6-27B-UD-Q4_K_XL'],
        is_current: true,
        authenticated: true
      }
    ]
  }

  it('does not refetch the static catalog after a minute or window focus', async () => {
    vi.useFakeTimers()

    try {
      renderPicker()
      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(vi.mocked(requestModelOptions)).toHaveBeenCalledTimes(1)

      await act(async () => {
        vi.advanceTimersByTime(60_001)
        window.dispatchEvent(new Event('focus'))
        await Promise.resolve()
      })

      expect(vi.mocked(requestModelOptions)).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders the provider status glyph with an accessible label', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue(READY_OPTIONS)
    renderPicker()

    // The glyph is a bare symbol, so it needs an i18n-sourced accessible name.
    // cmdk marks the group heading aria-hidden (its text feeds the group's
    // aria-labelledby name), so query with hidden:true to reach the node.
    const status = await screen.findByRole('img', { hidden: true, name: /ready/i })

    expect(status.textContent).toBe('●')
  })

  it('updates readiness in place without reordering or disturbing the focused row until close', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const key = modelOptionsQueryKey('default', undefined, undefined)

    const first: ModelOptionsResult = {
      providers: [
        { slug: 'alpha', name: 'Alpha', models: ['one'], glyph: '●', readiness: 'ready' },
        { slug: 'beta', name: 'Beta', models: ['two'], glyph: '○', readiness: 'depleted' }
      ]
    }

    const firstOverlay: ModelOptionsResult = {
      providers: [
        { slug: 'beta', name: 'Beta fresh', models: ['two'], glyph: '●', readiness: 'ready' },
        { slug: 'alpha', name: 'Alpha fresh', models: ['one'], glyph: '○', readiness: 'depleted' }
      ]
    }

    const secondOverlay: ModelOptionsResult = {
      providers: [{ slug: 'beta', name: 'Beta newest', models: ['three'], glyph: '○', readiness: 'depleted' }]
    }

    vi.mocked(requestModelOptions).mockResolvedValue(first)

    const props = {
      currentModel: 'one',
      currentProvider: 'alpha',
      onOpenChange: vi.fn(),
      onSelect: vi.fn(),
      open: true
    }

    const view = render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ModelPickerDialog {...props} />
        </I18nProvider>
      </QueryClientProvider>
    )

    await screen.findByText('one')
    const one = screen.getByText('one').closest('[cmdk-item]') as HTMLElement
    const two = screen.getByText('two').closest('[cmdk-item]') as HTMLElement

    const list = screen.getByRole('listbox')
    Object.defineProperty(list, 'scrollTop', { configurable: true, value: 73, writable: true })
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' })
    await waitFor(() => expect(two.getAttribute('aria-selected')).toBe('true'))
    const focused = document.activeElement
    act(() => client.setQueryData(key, firstOverlay))

    expect(screen.getAllByRole('option')).toEqual([one, two])
    expect(screen.getByText('one').closest('[cmdk-item]')).toBe(one)
    expect(screen.getByText('two').closest('[cmdk-item]')).toBe(two)
    expect(two.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(focused)
    expect(list.scrollTop).toBe(73)
    await waitFor(() => expect(screen.getAllByRole('img', { hidden: true })[0]?.textContent).toBe('○'))

    act(() => client.setQueryData(key, secondOverlay))

    expect(screen.getAllByRole('option')).toEqual([one, two])
    expect(screen.getByText('one').closest('[cmdk-item]')).toBe(one)
    expect(screen.getByText('two').closest('[cmdk-item]')).toBe(two)
    expect(screen.queryByText('three')).toBeNull()
    expect(screen.getByText('Alpha').closest('[cmdk-group]')).not.toBeNull()
    expect(two.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(focused)
    expect(list.scrollTop).toBe(73)

    view.rerender(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ModelPickerDialog {...props} open={false} />
        </I18nProvider>
      </QueryClientProvider>
    )
    view.rerender(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ModelPickerDialog {...props} />
        </I18nProvider>
      </QueryClientProvider>
    )

    await waitFor(() => expect(screen.getAllByRole('option').map(row => row.textContent)).toEqual(['three']))
  })

  it('defaults the initial cursor to the preferred ready-1M model', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue(READY_OPTIONS)
    renderPicker()

    await screen.findByText('gemini-3.8-flash')

    // The preferred row — not the first row (glm-4.6-omni) and not the current
    // model (Qwen…) — is the initially highlighted cmdk item.
    await waitFor(() => {
      const preferred = screen.getByText('gemini-3.8-flash').closest('[cmdk-item]')

      expect(preferred?.getAttribute('aria-selected')).toBe('true')
    })

    const first = screen.getByText('glm-4.6-omni').closest('[cmdk-item]')

    expect(first?.getAttribute('aria-selected')).toBe('false')
  })

  it('fails open with no overlay keys: no glyph, cursor falls back to current', async () => {
    // OPTIONS carries none of the overlay keys — must render exactly as today.
    renderPicker()

    await screen.findByText('Qwen3.6-27B-UD-Q4_K_XL')

    // No status glyph node anywhere.
    expect(screen.queryByRole('img', { hidden: true, name: /ready|partial|depleted|unknown|credential/i })).toBeNull()

    // Today's behavior: the first item — here the current model — is highlighted.
    await waitFor(() => {
      const current = screen.getByText('Qwen3.6-27B-UD-Q4_K_XL').closest('[cmdk-item]')

      expect(current?.getAttribute('aria-selected')).toBe('true')
    })
  })

  it('fail-open cursor is cmdk first-item, not the current model', async () => {
    // Discriminating fixture: NO overlay keys, and the current model (Qwen…) is
    // the SECOND row. True fail-open = cmdk's uncontrolled first-item highlight,
    // which here is a NON-current model — so this proves the fallback is
    // "first item", not "cursor on current". (The default OPTIONS fixture puts
    // the current model first, where the two behaviors are indistinguishable.)
    vi.mocked(requestModelOptions).mockResolvedValue({
      model: 'Qwen3.6-27B-UD-Q4_K_XL',
      provider: 'llamacpp',
      providers: [
        { slug: 'nous', name: 'Nous', models: ['glm-4.6-omni', 'gemini-3.8-flash'], authenticated: true },
        { slug: 'llamacpp', name: 'Local', models: ['Qwen3.6-27B-UD-Q4_K_XL'], is_current: true, authenticated: true }
      ]
    })
    renderPicker()

    await screen.findByText('glm-4.6-omni')

    // No glyph (no overlay keys), first item highlighted, current NOT highlighted.
    expect(screen.queryByRole('img', { hidden: true, name: /ready|partial|depleted|unknown|credential/i })).toBeNull()
    await waitFor(() => {
      const first = screen.getByText('glm-4.6-omni').closest('[cmdk-item]')

      expect(first?.getAttribute('aria-selected')).toBe('true')
    })
    const current = screen.getByText('Qwen3.6-27B-UD-Q4_K_XL').closest('[cmdk-item]')

    expect(current?.getAttribute('aria-selected')).toBe('false')
  })
})

const WINDOW_USAGE = {
  kind: 'usage_window' as const,
  plan: 'Window Plan',
  gauges: [
    { label: 'day', left_fraction: 0.82, scope: 'all', style: 'green' },
    { label: 'week', left_fraction: 0.97, scope: 'all', style: 'green' }
  ],
  top_tier_model_ids: ['example/model-b']
}

// The gateway's per-plan words, passed through by the backend (Hermes never computes them).
const WINDOW_LEVELS = {
  'example/model-d': 'medium',
  'example/model-b': 'top_tier',
  'example/model-free:free': 'free'
}

// What an older backend sent for a credit-grant subscription; the picker no longer draws it.
const CREDIT_GRANT_USAGE = {
  kind: 'subscription_cap',
  plan: 'Credit Plan',
  gauges: [{ label: 'credits', left_fraction: 0.62, style: 'green' }]
}

describe('ModelPickerDialog plan usage', () => {
  it('shows usage words and no prices on a usage-window plan', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d', 'example/model-b', 'example/model-free:free'],
          authenticated: true,
          plan_usage: WINDOW_USAGE,
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('Every model below is included', { exact: false })).toBeTruthy()
    // Gauges read as used and fill up: 0.82 left → 18% used, 0.97 left → 3% used.
    expect(screen.getByText('18% used')).toBeTruthy()
    expect(screen.getByText('3% used')).toBeTruthy()
    // Only Large / Free carry a tag; the footnote shows because a top-tier row is listed.
    expect(screen.getByText('Free')).toBeTruthy()
    expect(screen.queryByText('Medium')).toBeNull()
    expect(screen.getByText('Large')).toBeTruthy()
    expect(screen.getByText('Large models also use your monthly large-model limit.')).toBeTruthy()
    expect(screen.getByText('Window Plan')).toBeTruthy()
    expect(screen.queryByText('★')).toBeNull()
    // No featured picks in the plan: one flat list, no section titles.
    expect(screen.queryByText('Recommended')).toBeNull()
    expect(screen.queryByText(/\$1\.00/)).toBeNull()
    expect(screen.queryByText(/Input \/ Output/)).toBeNull()
    // An older backend sends no month gauge: no month row.
    expect(screen.queryByText('Month')).toBeNull()
  })

  it('shows a month row when the plan sends one', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            gauges: [...WINDOW_USAGE.gauges, { label: 'month', left_fraction: 0.6, scope: 'all', style: 'green' }]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('Month')).toBeTruthy()
    expect(screen.getByText('40% used')).toBeTruthy()
  })

  it('draws a gauge the client has no words for with the server title', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            gauges: [
              { label: 'five_hour', left_fraction: 0.5, scope: 'all', style: 'yellow', title: 'Next 5 hours' },
              ...WINDOW_USAGE.gauges
            ]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('Next 5 hours')).toBeTruthy()
    expect(screen.getByText('50% used')).toBeTruthy()
  })

  it('dims rows a spent allowance stops instead of annotating them, never a free row', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d', 'example/model-b', 'example/model-free:free'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            gauges: [
              { label: 'day', left_fraction: 0.5, scope: 'all', style: 'green' },
              { label: 'top_tier', left_fraction: 0, scope: 'top_tier', style: 'red' }
            ]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    // Only the top-tier row draws on the spent top-tier allowance: it dims; Medium and Free still have room.
    const large = await screen.findByText('example/model-b')
    expect(large.closest('span')?.className).toContain('text-muted-foreground')
    expect(screen.getByText('example/model-free:free').closest('span')?.className).not.toContain(
      'text-muted-foreground'
    )
    expect(screen.queryByText('used up', { exact: false })).toBeNull()
  })

  it('a spent day is one line above the list, not a note on every paid row', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d', 'example/model-b', 'example/model-free:free'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            gauges: [{ label: 'day', left_fraction: 0, scope: 'all', style: 'red' }]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    // A spent all-model allowance is said once, above the list, not on every paid row.
    expect(await screen.findByText('Daily limit reached', { exact: false })).toBeTruthy()
    expect(screen.queryByText('used up')).toBeNull()
  })

  it('says paid models now use credits (pay as you go) when top-up credits will pay', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d', 'example/model-b', 'example/model-free:free'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            credits_pay: true,
            gauges: [{ label: 'week', left_fraction: 0, scope: 'all', style: 'red' }]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('Weekly limit reached', { exact: false })).toBeTruthy()
    expect(screen.getByText('· paid models now use your credits (pay as you go)', { exact: false })).toBeTruthy()
    expect(screen.queryByText('used up')).toBeNull()
    expect(screen.queryByText('uses credits')).toBeNull()
  })

  it('says pay as you go once in the limit line, not on each Large row, when credits pay', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d', 'example/model-b', 'example/model-free:free'],
          authenticated: true,
          plan_usage: {
            ...WINDOW_USAGE,
            credits_pay: true,
            gauges: [
              { label: 'day', left_fraction: 0.5, scope: 'all', style: 'green' },
              { label: 'top_tier', left_fraction: 0, scope: 'top_tier', style: 'red' }
            ]
          },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(
      await screen.findByText('· Large models now use your credits (pay as you go)', { exact: false })
    ).toBeTruthy()
    expect(screen.queryByText('uses credits')).toBeNull()
    expect(screen.queryByText('used up', { exact: false })).toBeNull()
  })

  it('keeps prices and shows no plan chrome for a credit-grant subscription', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d'],
          authenticated: true,
          pricing: {
            'example/model-d': { input: '$1.00', output: '$5.00', free: false }
          },
          plan_usage: CREDIT_GRANT_USAGE
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('$1.00 / $5.00')).toBeTruthy()
    expect(screen.queryByText('Credit Plan', { exact: false })).toBeNull()
    expect(screen.queryByText(/% used/)).toBeNull()
  })

  it('splits Recommended from Other models on a usage-window plan, flat while searching', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-b', 'example/model-d', 'example/model-free:free'],
          authenticated: true,
          plan_usage: { ...WINDOW_USAGE, featured_model_ids: ['example/model-b'] },
          usage_levels: WINDOW_LEVELS
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('Recommended')).toBeTruthy()
    expect(screen.getByText('Other models')).toBeTruthy()
  })

  it('shows no sections or usage words for a credit-grant account', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [
        {
          slug: 'nous',
          name: 'Nous',
          models: ['example/model-d'],
          authenticated: true,
          pricing: { 'example/model-d': { input: '$1.00', output: '$5.00', free: false } },
          plan_usage: { ...CREDIT_GRANT_USAGE, featured_model_ids: ['example/model-d'] },
          usage_levels: { 'example/model-d': 'top_tier' }
        }
      ]
    })
    renderPicker({ currentModel: 'example/model-d', currentProvider: 'nous' })

    expect(await screen.findByText('$1.00 / $5.00')).toBeTruthy()
    expect(screen.queryByText('Recommended')).toBeNull()
    expect(screen.queryByText('Large')).toBeNull()
  })
})
describe('ModelPickerDialog search ranking', () => {
  // Rows must come out in the order the shared fuzzyRank produces — the same
  // helper the web and TUI pickers use — so a query ranks identically on
  // every surface. Curated order puts the scattered match first; the ranked
  // order does not, which is what proves the picker is not substring-filtering.
  const MODELS = ['glm-4.6-omni', 'claude-sonnet-4', 'gpt-4o']

  it('orders model rows exactly as the shared fuzzyRank does', async () => {
    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [{ slug: 'nous', name: 'Nous', models: MODELS, authenticated: true }]
    })
    renderPicker({ currentModel: 'gpt-4o', currentProvider: 'nous' })
    await screen.findByText('gpt-4o')

    const query = 'g4o'
    fireEvent.change(screen.getByRole('combobox'), { target: { value: query } })

    const expected = fuzzyRank(MODELS, query, modelSearchText).map(r => r.item)

    expect(expected).not.toEqual(MODELS.filter(m => expected.includes(m)))
    await waitFor(() => {
      const rows = screen.getAllByRole('option').map(el => el.textContent?.trim())

      expect(rows).toEqual(expected)
    })
  })

  // Regression guard: main folded `[-_.]` on both sides (foldIncludes); the
  // shared ranker must too, or a query typed with the "wrong" separator
  // drops every row while the highlighter (which still folds) disagrees.
  it.each([
    ['gpt.4o', 'gpt-4o'],
    ['claude_3', 'claude-3-opus'],
    ['qwen3-8', 'qwen3.8-flash']
  ])('separator variant %s still lists %s', async (query, expected) => {
    const catalog = ['gpt-4o', 'claude-3-opus', 'qwen3.8-flash']

    vi.mocked(requestModelOptions).mockResolvedValue({
      providers: [{ slug: 'nous', name: 'Nous', models: catalog, authenticated: true }]
    })
    renderPicker({ currentModel: 'gpt-4o', currentProvider: 'nous' })
    await screen.findByText('gpt-4o')

    fireEvent.change(screen.getByRole('combobox'), { target: { value: query } })

    await waitFor(() => {
      const rows = screen.getAllByRole('option').map(el => el.textContent?.trim())

      expect(rows).toContain(expected)
    })
  })
})
