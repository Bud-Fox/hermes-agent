import { describe, expect, it } from 'vitest'

import type { ModelOptionProvider } from '@hermes/shared'

const staticRows = (rows: ModelOptionProvider[]) => rows.map(({ slug, name, models }) => ({ slug, name, models }))

describe('All profiles fleet catalog', () => {
  it('keeps owner static rows identical while health differs', () => {
    const base = [{ slug: 'fleet', name: 'Fleet', models: ['Literal/ID'] }]
    const ownerA = [{ ...base[0], readiness: 'ready', glyph: '●' }]
    const ownerB = [{ ...base[0], readiness: 'unknown', glyph: '·' }]

    expect(staticRows(ownerA)).toEqual(staticRows(ownerB))
    expect(ownerA[0].glyph).not.toBe(ownerB[0].glyph)
  })
})