import { lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'

import { getSession, sessionQueryKey } from '@/api/auth'
import { ChunkLoadBoundary } from '@/components/ChunkLoadBoundary'
import { PageLoader } from '@/components/PageLoader'
import { ErrorState } from '@/components/ui/error-state'
import { BRAND_NAME } from '@/config'

const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Login = lazy(() => import('@/pages/Login'))
const Overview = lazy(() => import('@/pages/Overview'))
const FareExplorer = lazy(() => import('@/pages/FareExplorer'))
const Subscriptions = lazy(() => import('@/pages/Subscriptions'))
const PriceHistory = lazy(() => import('@/pages/PriceHistory'))
const CollectionStatus = lazy(() => import('@/pages/CollectionStatus'))
const NotificationSettings = lazy(() => import('@/pages/NotificationSettings'))

function RequireSession() {
  const location = useLocation()
  const session = useQuery({ queryKey: sessionQueryKey, queryFn: getSession, staleTime: 60_000 })

  if (session.isPending) return <PageLoader />
  if (session.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <ErrorState message={session.error instanceof Error ? session.error.message : '无法确认登录状态'} onRetry={() => session.refetch()} />
      </div>
    )
  }
  if (!session.data.authenticated) return <Navigate to="/login" replace state={{ from: location }} />

  return <Outlet />
}

export default function App() {
  return (
    <BrowserRouter>
      <ChunkLoadBoundary scopeLabel={BRAND_NAME}>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireSession />}>
              <Route element={<Dashboard />}>
                <Route index element={<Navigate to="/overview" replace />} />
                <Route path="overview" element={<Overview />} />
                <Route path="explore" element={<FareExplorer />} />
                <Route path="subscriptions" element={<Subscriptions />} />
                <Route path="history" element={<PriceHistory />} />
                <Route path="collection" element={<CollectionStatus />} />
                <Route path="notifications" element={<NotificationSettings />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </Suspense>
      </ChunkLoadBoundary>
    </BrowserRouter>
  )
}
