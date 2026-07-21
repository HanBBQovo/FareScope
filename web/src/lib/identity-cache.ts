import type { QueryClient, QueryKey } from '@tanstack/react-query'

export function clearIdentityCache(queryClient: QueryClient): void {
  queryClient.clear()
}

export function replaceIdentityCache<T>(
  queryClient: QueryClient,
  sessionQueryKey: QueryKey,
  session: T,
): void {
  queryClient.clear()
  queryClient.setQueryData(sessionQueryKey, session)
}
