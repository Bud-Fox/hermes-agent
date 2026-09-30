import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ProviderStatusGlyph } from './provider-status-glyph'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: { modelPicker: {
      readyStatus: 'Ready', partialStatus: 'Degraded', depletedStatus: 'Not ready',
      credDeadStatus: 'Credentials unavailable', unknownStatus: 'Unknown'
    } }
  })
}))

describe('ProviderStatusGlyph fleet payload parity', () => {
  it.each([
    ['ready', '●', 'Ready'],
    ['partial', '◐', 'Degraded'],
    ['unknown', '·', 'Unknown']
  ])('preserves %s glyph %s', (readiness, glyph, label) => {
    render(<ProviderStatusGlyph provider={{ slug: 'fleet', name: 'Fleet', models: [], readiness, glyph }} />)
    expect(screen.getByRole('img', { name: label }).textContent).toBe(glyph)
  })
})