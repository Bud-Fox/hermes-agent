# Task 1 report — stable Desktop picker snapshot/overlay

## Outcome

Implemented the Desktop model-picker open-session snapshot. While open, provider/model rows retain captured identity, order, and static fields even when later catalog payloads omit them; fresh payloads overlay only readiness/glyph metadata where available. Additions, removals, and reordering appear after close/reopen. Two sequential overlay ticks preserve the selected row, focused command input, and list scroll position.

Static `model.options` queries now use `staleTime: Infinity`. `refetchOnWindowFocus: false` is also explicit locally (it was already the global default). Existing explicit refetch/invalidation paths remain unchanged.

## RED evidence

Command:

```text
cd apps/desktop && npx vitest run --project ui src/components/model-picker.test.tsx
```

Before the review fix: 1 failed, 13 passed. The expanded stable-overlay test's second tick omitted the captured Alpha provider; its row disappeared, proving the open snapshot was reconstructed incorrectly from only the latest payload.

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
