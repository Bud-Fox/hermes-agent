import type { ModelCapabilities, ModelOptionProvider, ModelOptionsResult } from '@hermes/shared'

import { getGlobalModelOptions, type HermesGateway } from '@/hermes'

type CatalogProviderIdentity = Pick<ModelOptionProvider, 'aliases' | 'name' | 'slug'>

/** True when `currentProvider` is this catalog row — slug, display name, or
 *  a custom-provider alias (`custom:<key>` vs the bare config key, #87035). */
export function catalogProviderMatches(provider: CatalogProviderIdentity, currentProvider: string): boolean {
  if (!currentProvider) {
    return false
  }

  return (
    provider.slug === currentProvider ||
    provider.name === currentProvider ||
    (provider.aliases?.includes(currentProvider) ?? false)
  )
}

/** The router health overlay the Python picker path stamps onto each provider
 *  row IN PLACE (`_apply_health_overlay` + `_mark_preferred_default`). The
 *  generated `ModelOptionProvider` carries `[key: string]: unknown`, so these
 *  snake_case keys survive to the desktop payload without widening the
 *  contract. Read them here — never by editing the generated type. */
export interface ProviderReadiness {
  /** Status symbol already mapped server-side (●/◐/○/⚠/·). */
  glyph?: string
  /** ready | partial | depleted | cred_dead | unknown. */
  readiness?: string
  /** The top ready/partial+1M provider's model to seed the picker cursor on. */
  preferred_model?: string
  /** Whether this provider has a ready ≥1M-context model. */
  has_million_ready?: boolean
}

/** Read the health-overlay keys off a provider row with runtime typeof guards.
 *  Fail-open: a missing or wrong-typed key is omitted, so an un-annotated
 *  payload (older backend, overlay off) degrades to today's exact behavior —
 *  no glyph, cursor on current. Never throws. */
export function providerReadiness(provider: ModelOptionProvider): ProviderReadiness {
  const out: ProviderReadiness = {}
  const glyph = provider['glyph']

  if (typeof glyph === 'string' && glyph.length > 0) {
    out.glyph = glyph
  }

  const readiness = provider['readiness']

  if (typeof readiness === 'string' && readiness.length > 0) {
    out.readiness = readiness
  }

  const preferred = provider['preferred_model']

  if (typeof preferred === 'string' && preferred.length > 0) {
    out.preferred_model = preferred
  }

  const million = provider['has_million_ready']

  if (typeof million === 'boolean') {
    out.has_million_ready = million
  }

  return out
}

/** The model list the PICKER should DISPLAY for a provider: the server-filtered
 *  `picker_models` (readiness/million/block filter) when present, else the full
 *  `models`. Fail-open: a payload without `picker_models` (older backend, filter
 *  off) shows the complete list exactly like today. Never throws. The full
 *  `models` is always what Edit-Models reads — this accessor is picker-only. */
export function pickerModelList(provider: ModelOptionProvider): readonly string[] {
  const pm = provider['picker_models']

  if (Array.isArray(pm) && pm.every((m): m is string => typeof m === 'string')) {
    return pm
  }

  return provider.models ?? []
}

/** The cmdk item value for a (provider, model) pick — the picker's item
 *  `value` and the seed for the initial highlight share this one shape. */
export function pickerItemValue(providerSlug: string, model: string): string {
  return `${providerSlug}:${model}`
}

/** The value to seed the picker's initial cursor on when it OPENS: the first
 *  provider row that names a `preferred_model` (server ranks ready+1M first),
 *  as a `slug:model` cmdk value. `undefined` when no row is annotated — the
 *  caller then falls back to today's uncontrolled first-item highlight. */
export function preferredCursorValue(providers: readonly ModelOptionProvider[]): string | undefined {
  for (const provider of providers) {
    const { preferred_model } = providerReadiness(provider)

    if (preferred_model && (provider.models ?? []).includes(preferred_model)) {
      return pickerItemValue(provider.slug, preferred_model)
    }
  }

  return undefined
}

/** The catalog's option support for the current pick, or undefined while the
 *  catalog is loading / doesn't say. Callers treat undefined as "assume
 *  reasoning" so controls never flicker away during the fetch. */
export function currentModelCapabilities(
  options: ModelOptionsResult | null | undefined,
  provider: string,
  model: string
): ModelCapabilities | undefined {
  return options?.providers?.find(row => catalogProviderMatches(row, provider))?.capabilities?.[model]
}

// A picked (provider, model) pair is never retargeted from catalog membership.
// Picker rows are hints (discovered / curated / capped lists); a custom endpoint
// or a newer release legitimately serves ids the row lacks, and the backend
// soft-accepts them. Diffing the pick against the catalog silently swapped
// `deepseek-v4.1-flash` for the row's `-0731` sibling. The only authority on a
// pick's validity is the gateway's switch result.

interface ModelOptionsRequest {
  /** When false, include ambient/unconfigured providers (onboarding/setup
   *  surfaces). Chat pickers default to true so only explicitly configured
   *  providers are listed (#56974). */
  explicitOnly?: boolean
  gateway?: HermesGateway
  /** Owner-routed RPC. When set, catalog reads hit this dispatcher instead of
   *  `gateway.request` — a tile's model menu must not query the ambient
   *  chrome socket (#93892). */
  request?: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  /** Profile for the REST recovery path. Must match the catalog owner so a
   *  secondary tile does not fall back to the launch profile's models. */
  profile?: null | string
  refresh?: boolean
  sessionId?: null | string
}

export function modelOptionsQueryKey(
  profile: null | string | undefined,
  sessionId?: null | string,
  ownerConnectionId?: null | string
) {
  const profileKey = (profile ?? '').trim() || 'default'
  const ownerKey = (ownerConnectionId ?? '').trim()

  return ['model-options', profileKey, sessionId || 'global', ...(ownerKey ? ['owner', ownerKey] : [])] as const
}

function hasSelectableModels(options: ModelOptionsResult | null | undefined): boolean {
  return options?.providers?.some(provider => (provider.models?.length ?? 0) > 0) ?? false
}

function restModelOptions(
  explicitOnly: boolean,
  refresh: boolean,
  profile?: null | string
): Promise<ModelOptionsResult> {
  const opts = { explicitOnly, ...(refresh ? { refresh: true } : {}) }
  const profileKey = (profile ?? '').trim()

  return profileKey ? getGlobalModelOptions(opts, profileKey) : getGlobalModelOptions(opts)
}

export async function requestModelOptions({
  explicitOnly = true,
  gateway,
  profile,
  refresh = false,
  request,
  sessionId
}: ModelOptionsRequest): Promise<ModelOptionsResult> {
  const dispatch = request ?? (gateway ? gateway.request.bind(gateway) : null)

  if (dispatch) {
    const params: Record<string, unknown> = {}

    if (sessionId) {
      params.session_id = sessionId
    }

    if (refresh) {
      params.refresh = true
    }

    if (explicitOnly) {
      params.explicit_only = true
    }

    const profileKey = (profile ?? '').trim()

    if (profileKey) {
      params.profile = profileKey
    }

    let gatewayError: unknown
    let gatewayOptions: ModelOptionsResult | undefined

    try {
      gatewayOptions = await dispatch<ModelOptionsResult>('model.options', params)
    } catch (error) {
      gatewayError = error
    }

    if (gatewayOptions && hasSelectableModels(gatewayOptions)) {
      return gatewayOptions
    }

    // An owner-routed dispatcher can name a different registry connection than
    // the ambient REST client. Never recover that request through ambient REST:
    // profile names are not unique across sources, so doing so can cache B's
    // catalog under A's tile. Ambient gateway requests retain the compatibility
    // recovery used by older backends with incomplete model.options responses.
    if (!request) {
      try {
        const restOptions = await restModelOptions(explicitOnly, refresh, profile)

        if (hasSelectableModels(restOptions)) {
          return {
            ...restOptions,
            ...(gatewayOptions?.provider ? { provider: gatewayOptions.provider } : {}),
            ...(gatewayOptions?.model ? { model: gatewayOptions.model } : {})
          }
        }
      } catch {
        // Preserve the gateway result (or its original error) when the recovery
        // path is unavailable.
      }
    }

    if (gatewayOptions) {
      return gatewayOptions
    }

    throw gatewayError
  }

  return restModelOptions(explicitOnly, refresh, profile)
}
