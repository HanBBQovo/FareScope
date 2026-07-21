import assert from 'node:assert/strict'
import test from 'node:test'

class FakeEventSource {
  static instances = []

  listeners = new Map()
  onopen = null
  onerror = null
  closed = false

  constructor(url) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || []
    listeners.push(listener)
    this.listeners.set(name, listeners)
  }

  emit(name, data, lastEventId = '') {
    for (const listener of this.listeners.get(name) || []) {
      listener({ data: JSON.stringify(data), lastEventId })
    }
  }

  close() {
    this.closed = true
  }
}

globalThis.EventSource = FakeEventSource
let nextTimerId = 1
const timers = new Map()
globalThis.window = {
  setTimeout(callback, delay) {
    const id = nextTimerId
    nextTimerId += 1
    timers.set(id, { callback, delay })
    return id
  },
  clearTimeout(id) {
    timers.delete(id)
  },
}

const { CollectionRealtimeManager } = await import('../src/lib/collection-realtime.ts')

test('manager filters malformed snapshots and clears cursor after its last consumer', () => {
  FakeEventSource.instances.length = 0
  timers.clear()
  const manager = new CollectionRealtimeManager()
  const updates = []
  const unsubscribe = manager.subscribe({
    onUpdate: (update) => updates.push(update),
    onState: () => {},
  })
  const first = FakeEventSource.instances[0]
  assert.equal(first.url, '/api/realtime/collection-runs')

  first.emit('collection-snapshot', {
    cursor: '123-4',
    items: [
      {
        runId: 'valid-run',
        status: 'running',
        updatedAt: '2026-07-21T00:00:00Z',
        scheduledAt: '2026-07-21T00:00:00Z',
        startedAt: '2026-07-21T00:00:00Z',
        finishedAt: null,
        attempt: 1,
        maxAttempts: 3,
        errorCode: null,
      },
      {
        runId: 'malformed-run',
        status: 'provider-payload',
      },
    ],
  }, '123-4')

  assert.equal(updates.length, 1)
  assert.deepEqual(updates[0].runs.map((run) => run.runId), ['valid-run'])
  const unsubscribePeer = manager.subscribe({
    onUpdate: () => {},
    onState: () => {},
  })
  assert.equal(FakeEventSource.instances.length, 1)
  unsubscribe()
  assert.equal(first.closed, false)
  unsubscribePeer()
  assert.equal(first.closed, true)

  const unsubscribeNext = manager.subscribe({
    onUpdate: () => {},
    onState: () => {},
  })
  const second = FakeEventSource.instances[1]
  assert.equal(second.url, '/api/realtime/collection-runs')
  assert.equal(second.url.includes('cursor='), false)
  unsubscribeNext()
})

test('manager consumes snapshot and data-free checkpoint cursors across reconnects', () => {
  FakeEventSource.instances.length = 0
  timers.clear()
  const manager = new CollectionRealtimeManager()
  const updates = []
  const unsubscribe = manager.subscribe({
    onUpdate: (update) => updates.push(update),
    onState: () => {},
  })
  const first = FakeEventSource.instances[0]

  first.emit('collection-snapshot', { cursor: '100-0', items: [] })
  first.onerror()
  const [snapshotTimerId, snapshotTimer] = [...timers.entries()][0]
  timers.delete(snapshotTimerId)
  snapshotTimer.callback()
  const second = FakeEventSource.instances[1]
  assert.equal(second.url, '/api/realtime/collection-runs?cursor=100-0')

  second.emit('collection-checkpoint', { cursor: '125-0' }, '125-0')
  second.onerror()
  const [checkpointTimerId, checkpointTimer] = [...timers.entries()][0]
  timers.delete(checkpointTimerId)
  checkpointTimer.callback()
  const third = FakeEventSource.instances[2]
  assert.equal(third.url, '/api/realtime/collection-runs?cursor=125-0')
  assert.equal(updates.length, 1)

  unsubscribe()
})

test('manager closes failed sources and schedules only one exponential reconnect', () => {
  FakeEventSource.instances.length = 0
  timers.clear()
  const manager = new CollectionRealtimeManager()
  const unsubscribe = manager.subscribe({
    onUpdate: () => {},
    onState: () => {},
  })
  const first = FakeEventSource.instances[0]

  first.onerror()
  first.onerror()
  assert.equal(first.closed, true)
  assert.equal(timers.size, 1)
  const [firstTimerId, firstTimer] = [...timers.entries()][0]
  assert.ok(firstTimer.delay >= 1_000 && firstTimer.delay <= 1_200)
  timers.delete(firstTimerId)
  firstTimer.callback()
  assert.equal(FakeEventSource.instances.length, 2)

  const second = FakeEventSource.instances[1]
  second.onerror()
  assert.equal(timers.size, 1)
  const secondTimer = [...timers.values()][0]
  assert.ok(secondTimer.delay >= 2_000 && secondTimer.delay <= 2_400)

  unsubscribe()
  assert.equal(timers.size, 0)
})
