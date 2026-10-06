import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { ModelVisibilityDialog } from '@/components/model-visibility-dialog'
import type { HermesGateway } from '@/hermes'
import { $modelVisibilityOpen, $modelVisibilityOwner, setModelVisibilityOpen } from '@/store/model-visibility'
import { $activeSessionId, $gatewayState } from '@/store/session'
import { requestForSessionProfile } from '@/store/session-request-router'

interface ModelVisibilityOverlayProps {
  gateway?: HermesGateway
  onOpenProviders: () => void
  ownerConnectionId?: string
  profile: string
}

export function ModelVisibilityOverlay({
  gateway,
  onOpenProviders,
  ownerConnectionId,
  profile
}: ModelVisibilityOverlayProps) {
  const activeSessionId = useStore($activeSessionId)
  const gatewayOpen = useStore($gatewayState) === 'open'
  const open = useStore($modelVisibilityOpen)
  const context = useStore($modelVisibilityOwner)
  const connection = context?.connection ?? ownerConnectionId
  const ownerProfile = context?.profile ?? profile

  const request = useMemo(() => connection ? <T,>(method: string, params?: Record<string, unknown>) =>
    requestForSessionProfile<T>({ connectionId: connection, profile: ownerProfile },
      () => Promise.reject(new Error('Owner gateway is unavailable')), method, params) : undefined,
  [connection, ownerProfile])

  if (!gatewayOpen) {
    return null
  }

  return (
    <ModelVisibilityDialog
      gw={gateway}
      onOpenChange={setModelVisibilityOpen}
      onOpenProviders={onOpenProviders}
      open={open}
      ownerConnectionId={connection}
      profile={ownerProfile}
      request={request}
      sessionId={context ? context.sessionId : connection ? null : activeSessionId}
    />
  )
}
