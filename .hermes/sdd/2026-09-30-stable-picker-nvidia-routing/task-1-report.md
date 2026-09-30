# Task 1 report — stable Desktop picker snapshot/overlay

## Outcome

Implemented the Desktop model-picker open-session snapshot. While open, provider/model rows retain their captured `provider::model` ordering and React DOM identity; fresh catalog data overlays readiness/glyph metadata onto those rows. Closing and reopening applies the staged catalog order. The selected row and focused command input remain undisturbed.

Static `model.options` queries now use `staleTime: Infinity` and `refetchOnWindowFocus: false`. Existing explicit refetch/invalidation paths remain unchanged.

## RED evidence

Command:

```text
cd apps/desktop && npx vitest run --project ui src/components/model-picker.test.tsx
```

Before implementation: 1 failed, 13 passed. The new stable-overlay test showed the selected row changed during a live reorder (`expected aria-selected true, received false`).

## GREEN evidence

```text
npx vitest run --project ui src/components/model-picker.test.tsx src/lib/model-options.test.ts src/app/session/hooks/use-model-controls.test.tsx
```

Result: 3 files passed, 58 tests passed.

```text
npm run typecheck
```

Result: passed.

```text
npm run test:ui
```

Result: 889 files passed, 8,234 tests passed.

## Files

- `apps/desktop/src/components/model-picker.tsx`
- `apps/desktop/src/components/model-picker.test.tsx`
- `.hermes/sdd/2026-09-30-stable-picker-nvidia-routing/task-1-report.md`

## Commit

Pending at report creation; recorded by the commit containing this report.
