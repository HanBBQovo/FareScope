import assert from 'node:assert/strict'
import test from 'node:test'

import { QueryClient } from '@tanstack/react-query'

const { clearIdentityCache, replaceIdentityCache } = await import('../src/lib/identity-cache.ts')

test('identity replacement removes every previous owner-scoped query', () => {
  const queryClient = new QueryClient()
  queryClient.setQueryData(['auth', 'session'], { user: { id: 'owner-a' } })
  queryClient.setQueryData(['fares', 'collection-runs'], { owner: 'owner-a' })
  queryClient.setQueryData(['fares', 'notification-channels'], { owner: 'owner-a' })

  const nextSession = { user: { id: 'owner-b' } }
  replaceIdentityCache(queryClient, ['auth', 'session'], nextSession)

  assert.deepEqual(queryClient.getQueryData(['auth', 'session']), nextSession)
  assert.equal(queryClient.getQueryData(['fares', 'collection-runs']), undefined)
  assert.equal(queryClient.getQueryData(['fares', 'notification-channels']), undefined)
})

test('logout clears session and owner-scoped queries together', () => {
  const queryClient = new QueryClient()
  queryClient.setQueryData(['auth', 'session'], { user: { id: 'owner-a' } })
  queryClient.setQueryData(['fares', 'subscriptions'], { owner: 'owner-a' })

  clearIdentityCache(queryClient)

  assert.equal(queryClient.getQueryCache().getAll().length, 0)
})
