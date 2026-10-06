import { useStore } from '@nanostores/react'

import { $apiRequestScope } from '@/api/client'
import { $sharedPickerStatus, sharedPickerOwner } from '@/store/shared-picker'

/** Global legacy atoms have one authority at a time. Incompatible open surfaces
 * must expose no controls or rows, rather than sending A's edits to B. */
export function useSharedPickerGate(connection?: string | null, profile?: string | null): string | null {
  const status = useStore($sharedPickerStatus)
  // Re-evaluate when the ambient connection changes: an unowned surface follows it.
  useStore($apiRequestScope)
  const expected = sharedPickerOwner(connection, profile)

  if (status.owner !== expected) {
    return 'This picker belongs to another connection. Close and reopen it before editing shared models.'
  }

  if (!status.ready) {
    return 'Shared model preferences are loading. Reopen this picker if loading fails.'
  }

  return null
}
