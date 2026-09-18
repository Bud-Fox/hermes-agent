import type { ModelOptionProvider } from '@hermes/shared'

import { useI18n } from '@/i18n'
import { providerReadiness } from '@/lib/model-options'

import { cn } from '../lib/utils'

// Router readiness → i18n aria-label key. The glyph is a bare symbol, so it
// needs an accessible name; an unrecognized readiness falls back to
// `unknownStatus` so the glyph is never unlabeled.
const READINESS_LABEL: Record<string, 'readyStatus' | 'partialStatus' | 'depletedStatus' | 'credDeadStatus' | 'unknownStatus'> = {
  ready: 'readyStatus',
  partial: 'partialStatus',
  depleted: 'depletedStatus',
  cred_dead: 'credDeadStatus',
  unknown: 'unknownStatus'
}

// Router readiness → semantic color, reusing the emerald/amber/destructive
// palette already used by tier badges and price tags. Depleted and unknown
// stay neutral like the row's own slug·count metadata.
const READINESS_COLOR: Record<string, string> = {
  ready: 'text-emerald-600 dark:text-emerald-400',
  partial: 'text-amber-600 dark:text-amber-400',
  depleted: 'text-muted-foreground',
  cred_dead: 'text-destructive',
  unknown: 'text-muted-foreground'
}

/** One-per-provider readiness glyph (●◐○⚠·) the Python health overlay stamped
 *  on the provider row. Fail-open: renders nothing when the payload carries no
 *  `glyph` (older backend / router unreachable) → the heading looks exactly as
 *  it does today. Shared by every provider-grouped picker surface so the status
 *  legend stays defined in one place. */
export function ProviderStatusGlyph({ provider, className }: { provider: ModelOptionProvider; className?: string }) {
  const { t } = useI18n()
  const copy = t.modelPicker
  const { glyph, readiness } = providerReadiness(provider)

  if (!glyph) {
    return null
  }

  const statusLabel = readiness ? (READINESS_LABEL[readiness] ?? 'unknownStatus') : 'unknownStatus'
  const statusColor = readiness ? (READINESS_COLOR[readiness] ?? 'text-muted-foreground') : 'text-muted-foreground'

  return (
    <span aria-label={copy[statusLabel]} className={cn('shrink-0 leading-none', statusColor, className)} role="img">
      {glyph}
    </span>
  )
}
