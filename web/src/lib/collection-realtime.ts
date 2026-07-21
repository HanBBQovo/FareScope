export interface CollectionRealtimeRun {
  runId: string
  status: 'pending' | 'leased' | 'running' | 'succeeded' | 'failed' | 'canceled'
  updatedAt: string
  scheduledAt: string
  startedAt: string | null
  finishedAt: string | null
  attempt: number
  maxAttempts: number
  errorCode: string | null
}

export type CollectionRealtimeUpdate =
  | { kind: 'snapshot'; runs: CollectionRealtimeRun[] }
  | { kind: 'run'; run: CollectionRealtimeRun }

export interface CollectionRealtimeState {
  status: 'connecting' | 'connected' | 'degraded' | 'disconnected'
  reconnectAttempt: number
  lastEventAt: string | null
}

interface CollectionSnapshotPayload {
  cursor: string
  items: CollectionRealtimeRun[]
}

interface CollectionCheckpointPayload {
  cursor: string
}

interface RealtimeDegradedPayload {
  pollAfterMs?: number
}

interface Listener {
  onUpdate: (update: CollectionRealtimeUpdate) => void
  onState: (state: CollectionRealtimeState) => void
}

const CURSOR_PATTERN = /^[0-9]+-[0-9]+$/
const INITIAL_RECONNECT_MS = 1_000
const MAX_RECONNECT_MS = 30_000

export class CollectionRealtimeManager {
  private readonly listeners = new Set<Listener>()
  private source: EventSource | null = null
  private reconnectTimer: number | null = null
  private cursor: string | null = null
  private reconnectAttempt = 0
  private state: CollectionRealtimeState = {
    status: 'disconnected',
    reconnectAttempt: 0,
    lastEventAt: null,
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener.onState(this.state)
    if (this.listeners.size === 1) this.connect()
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) this.stop()
    }
  }

  private connect(): void {
    if (this.source || this.reconnectTimer !== null || this.listeners.size === 0) return
    this.setState({ status: 'connecting', reconnectAttempt: this.reconnectAttempt })
    const params = new URLSearchParams()
    if (this.cursor) params.set('cursor', this.cursor)
    const suffix = params.size ? `?${params.toString()}` : ''
    const source = new EventSource(`/api/realtime/collection-runs${suffix}`, {
      withCredentials: true,
    })
    this.source = source

    source.onopen = () => {
      if (this.source !== source) return
      this.reconnectAttempt = 0
      this.setState({ status: 'connected', reconnectAttempt: 0 })
    }
    source.onerror = () => {
      if (this.source !== source) return
      this.closeSource(source)
      this.scheduleReconnect()
    }
    source.addEventListener('collection-snapshot', (event) => {
      if (this.source !== source) return
      const message = event as MessageEvent<string>
      const payload = parseJson<CollectionSnapshotPayload>(message.data)
      if (!payload || !CURSOR_PATTERN.test(payload.cursor) || !Array.isArray(payload.items)) return
      this.rememberCursor(message.lastEventId)
      this.rememberCursor(payload.cursor)
      this.emitUpdate({ kind: 'snapshot', runs: payload.items.filter(isCollectionRun) })
    })
    source.addEventListener('collection-run', (event) => {
      if (this.source !== source) return
      const message = event as MessageEvent<string>
      const run = parseJson<CollectionRealtimeRun>(message.data)
      if (!isCollectionRun(run)) return
      this.rememberCursor(message.lastEventId)
      this.emitUpdate({ kind: 'run', run })
    })
    source.addEventListener('collection-checkpoint', (event) => {
      if (this.source !== source) return
      const message = event as MessageEvent<string>
      const payload = parseJson<CollectionCheckpointPayload>(message.data)
      if (!payload || !CURSOR_PATTERN.test(payload.cursor)) return
      this.rememberCursor(message.lastEventId)
      this.rememberCursor(payload.cursor)
    })
    source.addEventListener('realtime-degraded', (event) => {
      if (this.source !== source) return
      const payload = parseJson<RealtimeDegradedPayload>((event as MessageEvent<string>).data)
      this.setState({ status: 'degraded' })
      this.closeSource(source)
      this.scheduleReconnect(payload?.pollAfterMs)
    })
    source.addEventListener('realtime-reconnect', () => {
      if (this.source !== source) return
      this.closeSource(source)
      this.scheduleReconnect()
    })
  }

  private scheduleReconnect(minimumDelayMs = 0): void {
    if (this.listeners.size === 0 || this.reconnectTimer !== null) return
    const exponentialDelay = Math.min(
      MAX_RECONNECT_MS,
      INITIAL_RECONNECT_MS * 2 ** Math.min(this.reconnectAttempt, 5),
    )
    const jitter = Math.round(exponentialDelay * 0.2 * Math.random())
    const delay = Math.max(minimumDelayMs, exponentialDelay + jitter)
    this.reconnectAttempt += 1
    this.setState({
      status: this.state.status === 'degraded' ? 'degraded' : 'connecting',
      reconnectAttempt: this.reconnectAttempt,
    })
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private emitUpdate(update: CollectionRealtimeUpdate): void {
    const lastEventAt = new Date().toISOString()
    this.setState({ lastEventAt })
    for (const listener of this.listeners) listener.onUpdate(update)
  }

  private rememberCursor(value: string): void {
    if (CURSOR_PATTERN.test(value)) this.cursor = value
  }

  private setState(change: Partial<CollectionRealtimeState>): void {
    this.state = { ...this.state, ...change }
    for (const listener of this.listeners) listener.onState(this.state)
  }

  private closeSource(source: EventSource): void {
    source.close()
    if (this.source === source) this.source = null
  }

  private stop(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.source) this.closeSource(this.source)
    this.cursor = null
    this.reconnectAttempt = 0
    this.state = {
      status: 'disconnected',
      reconnectAttempt: 0,
      lastEventAt: this.state.lastEventAt,
    }
  }
}

export const collectionRealtimeManager = new CollectionRealtimeManager()

function parseJson<T>(value: string): T | null {
  try {
    return JSON.parse(value) as T
  } catch {
    return null
  }
}

function isCollectionRun(value: CollectionRealtimeRun | null): value is CollectionRealtimeRun {
  if (!value || typeof value !== 'object') return false
  return typeof value.runId === 'string'
    && ['pending', 'leased', 'running', 'succeeded', 'failed', 'canceled'].includes(value.status)
    && typeof value.updatedAt === 'string'
    && typeof value.scheduledAt === 'string'
    && (value.startedAt === null || typeof value.startedAt === 'string')
    && (value.finishedAt === null || typeof value.finishedAt === 'string')
    && Number.isInteger(value.attempt)
    && value.attempt >= 0
    && Number.isInteger(value.maxAttempts)
    && value.maxAttempts >= 1
    && (value.errorCode === null || typeof value.errorCode === 'string')
}
