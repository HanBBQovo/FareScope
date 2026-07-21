import { useEffect, useRef, useState } from 'react'

import {
  collectionRealtimeManager,
  type CollectionRealtimeState,
  type CollectionRealtimeUpdate,
} from '@/lib/collection-realtime'

const INITIAL_STATE: CollectionRealtimeState = {
  status: 'disconnected',
  reconnectAttempt: 0,
  lastEventAt: null,
}

export function useCollectionRealtime(
  onUpdate: (update: CollectionRealtimeUpdate) => void,
): CollectionRealtimeState {
  const updateRef = useRef(onUpdate)
  const [state, setState] = useState(INITIAL_STATE)
  updateRef.current = onUpdate

  useEffect(() => collectionRealtimeManager.subscribe({
    onUpdate: (update) => updateRef.current(update),
    onState: setState,
  }), [])

  return state
}
