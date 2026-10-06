import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ModelSelect } from './model-select'

// Radix Select calls scrollIntoView / pointer-capture APIs jsdom lacks.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

afterEach(() => {
  cleanup()
  delete (window as { hermesDesktop?: unknown }).hermesDesktop
})

const prefs = { version: 1, revision: 4, initialized: true, favorites: ['remote::fav'], visibility: { visible: null, known: null }, custom_models: [] }

describe('Settings custom model before shared hydration', () => {
  it('writes the typed custom row after hydration instead of letting hydration erase it', async () => {
    let finish!: (value: typeof prefs) => void

    const api = vi.fn((request: { method?: string; body?: Record<string, unknown> }) =>
      request.method === 'POST'
        ? Promise.resolve({ ...prefs, ...request.body, revision: prefs.revision + 1 })
        : new Promise<typeof prefs>(resolve => { finish = resolve }))

    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    let value = 'acme/typed-x'

    render(
      <I18nProvider>
        <ModelSelect models={['known']} onValueChange={next => { value = next }} providerSlug="openrouter" value={value} />
      </I18nProvider>
    )
    const trigger = screen.getByRole('combobox')
    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(await screen.findByText(/Custom model/))
    const input = await screen.findByRole('textbox')
    fireEvent.keyDown(input, { key: 'Enter' })
    await act(async () => { await Promise.resolve() })
    expect(api.mock.calls.filter(([request]) => request.method === 'POST')).toEqual([])
    await act(async () => { finish(prefs); await new Promise(resolve => setTimeout(resolve, 0)) })
    const { $customModels } = await import('@/store/custom-models')
    expect($customModels.get()).toContainEqual({ provider: 'openrouter', model: 'acme/typed-x' })
    const posts = api.mock.calls.filter(([request]) => request.method === 'POST').map(([request]) => request.body)
    expect(posts).toHaveLength(1)
    expect(posts[0]).toMatchObject({ expected_revision: 4, custom_models: [{ provider: 'openrouter', model: 'acme/typed-x' }] })
  })
})
