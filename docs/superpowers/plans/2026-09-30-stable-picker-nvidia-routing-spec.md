# Stable Picker Overlay + NVIDIA Slow-Model Routing Spec

## Goals

1. The model picker must not reorder or remount rows while it is open.
2. The static provider/model catalog must not be periodically fetched merely to refresh readiness.
3. Readiness glyphs remain live and update in place.
4. NVIDIA GLM/Kimi requests receive model-appropriate first-byte budgets without sweeping every credential for a model-side stall.
5. Strict vendor-local routing remains enforced.

## Picker architecture

- `model.options` is the authoritative catalog snapshot and is fetched on connection/profile startup, explicit catalog invalidation, model-catalog commit, reconnect, or manual refresh.
- It has no periodic refetch and no focus refetch. A one-minute stale timer must not trigger automatic catalog replacement.
- Dynamic readiness is an overlay keyed by stable provider/model identity. Overlay updates may arrive through the existing status event/poll path; they must preserve catalog row identity.
- On menu open, capture the ordered stable row IDs. While open, health updates change only glyph/readiness metadata. New sorting is staged and applied after menu close or successful selection.
- Preserve keyboard focus, hover and scroll position. No full-list key churn.

## NVIDIA routing architecture

- Preserve `https://integrate.api.nvidia.com/v1/chat/completions` and exact model IDs.
- Model policies:
  - `z-ai/glm-5.3-flash`: first-byte timeout 90s, total request budget 120s, at most 2 credential attempts for model stall.
  - `moonshotai/kimi-k3`: first-byte timeout 180s, total request budget 240s, at most 2 credential attempts for model stall.
- Existing NVIDIA models retain current defaults unless tests establish another requirement.
- A timeout before first byte on these models is classified as `MODEL_STALL`: model-scoped cooldown only; never global credential failure.
- Stop after two distinct credentials or exhausted request budget. Return explicit terminal diagnostic; do not sweep all keys.
- Kimi payload adapter preserves supported reasoning/tool history and does not mutate caller body.
- No cross-vendor or cross-model fallback.

## Acceptance

- Picker remains open through at least two health ticks: row order, focus and scroll remain stable; glyph changes render in place.
- Catalog RPC call count does not increase on health ticks or window focus.
- GLM and Kimi tests prove model-specific budgets, two-attempt stall cap, request-budget enforcement and key-health preservation.
- Existing picker glyph tests and router suites remain green.
- Independent review reports no critical/important findings before merge/deploy.
