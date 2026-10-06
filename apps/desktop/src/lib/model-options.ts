import type { ModelCapabilities, ModelOptionProvider, ModelOptionsResult } from '@hermes/shared'

import { capabilityScoped } from '@/api/client'
import { getGlobalModelOptions, type HermesGateway } from '@/hermes'
import { addCustomModel } from '@/store/custom-models'
import { notifyError } from '@/store/notifications'
import { $sharedPickerStatus, bindSharedPicker, sharedPickerConnection, sharedPickerOwner } from '@/store/shared-picker'
import type { PickerPreferences } from '@/store/shared-picker-sync'

type CatalogProviderIdentity = Partial<Pick<ModelOptionProvider, 'aliases' | 'name'>> &
  Pick<ModelOptionProvider, 'slug'>

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

/** The catalog row for `currentProvider`, matched the same way as
 *  `catalogProviderMatches` (so a saved `custom:<key>` finds its row). */
export function findCatalogProvider<T extends CatalogProviderIdentity>(
  providers: readonly T[],
  currentProvider: string
): T | undefined {
  return providers.find(row => catalogProviderMatches(row, currentProvider))
}

/** The catalog's option support for the current pick, or undefined while the
 *  catalog is loading / doesn't say. Callers treat undefined as "assume
 *  reasoning" so controls never flicker away during the fetch. */
export function currentModelCapabilities(
  options: ModelOptionsResult | null | undefined,
  provider: string,
  model: string
): ModelCapabilities | undefined {
  return findCatalogProvider(options?.providers ?? [], provider)?.capabilities?.[model]
}

// A picked (provider, model) pair is never retargeted from catalog membership.
// Picker rows are hints (discovered / curated / capped lists); a custom endpoint
// or a newer release legitimately serves ids the row lacks, and the backend
// soft-accepts them. Diffing the pick against the catalog silently swapped
// `deepseek-v4.1-flash` for the row's `-0731` sibling. The only authority on a
// pick's validity is the gateway's switch result.

/** The single, deliberate exception to the sticky-pick rule above: the virtual
 *  `moa` provider. Its catalog row vanishes entirely once no MoA preset is
 *  enabled (`hermes_cli/inventory.py` filters it out of explicit-only
 *  catalogs), so a persisted manual pick pointing at it leaves the composer
 *  pill reading `Model · moa: default` forever (#90244). For this one provider
 *  — and only with a populated catalog in hand — row absence is authoritative:
 *  the pick reseeds from the profile default. Every other provider keeps the
 *  sticky behavior; an unloaded/empty catalog never clobbers anything. */
export function moaPickRemoved(
  options: { providers?: ModelOptionProvider[] | null } | null | undefined,
  provider: string,
  model: string
): boolean {
  if (!model.trim() || provider.trim().toLowerCase() !== 'moa') {
    return false
  }

  const providers = options?.providers

  if (!providers || providers.length === 0) {
    return false
  }

  const row = providers.find(p => (p.slug || p.name || '').toLowerCase() === 'moa')

  return !(row?.models ?? []).includes(model)
}

/** A bare provider slug is the pre-migration spelling of a custom entry. The
 *  catalog aliases `custom:<key>` with the bare config key (#87035), so a pick
 *  still carrying `nvidia` and a profile default of `custom:nvidia` name the
 *  SAME endpoint — the pick's spelling is simply stale, not a distinct choice.
 *  Shipping the bare slug resolves the NATIVE provider instead of the custom
 *  entry, silently dropping the entry's `extra_body` (e.g.
 *  `thinking: {type: adaptive}`) that the user configured (#81922).
 *
 *  Only a bare slug can be superseded: a pick that already names a provider
 *  class (`custom:<other>`, `moa`, `openai-codex`) is a different endpoint and
 *  keeps the sticky behavior. The bare slug must be the default's own key, so
 *  an unrelated manual pick (`anthropic` while the default is `custom:nvidia`)
 *  is never clobbered. */
export function customDefaultSupersedesPick(pickProvider: string, defaultProvider: string): boolean {
  const pick = (pickProvider || '').trim().toLowerCase()
  const fallback = (defaultProvider || '').trim().toLowerCase()

  if (!pick || pick === fallback || !fallback.startsWith('custom:')) {
    return false
  }

  const key = fallback.slice('custom:'.length).trim()

  return key.length > 0 && pick === key
}

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
  ownerConnectionId?: null | string
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

/** Bind shared preferences over the ambient REST route. The scope is captured by the caller
 *  BEFORE any await, so later writes stay on the installation that hydrated them. */
async function bindRestSharedPicker(scope: ReturnType<typeof capabilityScoped>, owner: string): Promise<void> {
  if (!window.hermesDesktop?.api) {return}

  try {
    await bindSharedPicker(owner, (method, payload) => window.hermesDesktop.api<PickerPreferences>({
      ...scope, path: '/api/model/preferences',
      ...(method.endsWith('.update') ? { method: 'POST' as const, body: payload } : {})
    }), window.hermesDesktop.api)
  } catch (error) {
    notifyError(error, 'Shared picker preferences could not be loaded.')
  }
}

function restModelOptions(
  explicitOnly: boolean,
  refresh: boolean,
  profile?: null | string
): Promise<ModelOptionsResult> {
  const opts = { explicitOnly, ...(refresh ? { refresh: true } : {}) }
  const profileKey = (profile ?? '').trim()
  const scope = capabilityScoped(profileKey || undefined)
  const owner = sharedPickerOwner(null, profileKey)

  return (profileKey ? getGlobalModelOptions(opts, profileKey) : getGlobalModelOptions(opts)).then(async options => {
    await bindRestSharedPicker(scope, owner)

    return options
  })
}

/** Settings custom-model entry for the ambient installation. Preferences are installation-wide,
 *  so any ready binding of that connection accepts the edit; otherwise hydrate FIRST, then
 *  apply — an edit made before hydration would be overwritten by it. */
export async function rememberSharedCustomModel(
  providerSlug: string,
  slug: string,
  provider?: ModelOptionProvider
): Promise<boolean> {
  const connection = sharedPickerConnection(null)

  const readyHere = () => {
    const status = $sharedPickerStatus.get()

    return status.ready && status.owner.startsWith(connection + ':')
  }

  if (!readyHere()) {
    await bindRestSharedPicker(capabilityScoped(undefined), sharedPickerOwner(null, null))
  }

  if (!readyHere() || sharedPickerConnection(null) !== connection) {
    notifyError(new Error('Shared model preferences are unavailable'), 'Custom model was applied but not remembered; retry once preferences load.')

    return false
  }

  addCustomModel(providerSlug, slug, provider)

  return true
}

export async function requestModelOptions({
  explicitOnly = true,
  gateway,
  ownerConnectionId,
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

      // A routed `request` belongs to its owner connection (absent = ambient, the app-wide
      // convention). The ambient socket must never be relabeled as a different explicit owner.
      const connection = sharedPickerConnection(ownerConnectionId)

      if (gatewayOptions?.providers && (request || connection === sharedPickerConnection(null))) {
        try {
          await bindSharedPicker(sharedPickerOwner(connection, profileKey), (method, payload = {}) =>
            dispatch<PickerPreferences>(method, { ...payload, ...(profileKey ? { profile: profileKey } : {}) }), request ?? gateway)
        } catch (error) {
          notifyError(error, 'Shared picker preferences could not be loaded.')
        }
      }
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
