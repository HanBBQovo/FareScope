import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, LoaderCircle, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import {
  comparisonQueryKeys,
  createComparisonView,
  deleteComparisonView,
  getComparisonSnapshot,
  getComparisonSubscriptionOptions,
  getComparisonViews,
  updateComparisonView,
  type ComparisonSnapshotRoute,
  type ComparisonSubscriptionOption,
  type ComparisonView,
  type CreateComparisonViewInput,
  type UpdateComparisonViewInput,
} from '@/api/comparisons'
import { ApiError } from '@/api/client'
import { ComparisonRouteSummary } from '@/components/comparison/ComparisonRouteSummary'
import { ComparisonTrendChart } from '@/components/comparison/ComparisonTrendChart'
import {
  ComparisonViewEditor,
  type ComparisonEditorSubmit,
} from '@/components/comparison/ComparisonViewEditor'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/components/ui/use-confirm'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { sortComparisonRoutes, type ComparisonSort } from '@/lib/comparison'
import { formatDateTime } from '@/lib/formatters'
import { useCollectionRealtime } from '@/lib/use-collection-realtime'

type EditorMode = 'closed' | 'create' | 'edit'

function routeToOption(route: ComparisonSnapshotRoute): ComparisonSubscriptionOption {
  return {
    id: route.subscriptionId,
    name: route.name,
    enabled: route.enabled,
    currency: route.currency,
    origin: route.origin,
    destination: route.destination,
    tripType: route.tripType,
    departureDate: route.departureDate,
    returnDate: route.returnDate,
    directOnly: route.directOnly,
  }
}

export default function Comparisons() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [editorMode, setEditorMode] = useState<EditorMode>('closed')
  const [sort, setSort] = useState<ComparisonSort>('position')
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const { showToast } = useGlobalToast()
  const requestedViewId = searchParams.get('view') || ''

  const realtime = useCollectionRealtime((update) => {
    if (update.kind === 'run' && update.run.status === 'succeeded' && requestedViewId) {
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.snapshot(requestedViewId) })
    }
  })

  const views = useInfiniteQuery({
    queryKey: comparisonQueryKeys.views(),
    queryFn: ({ pageParam }) => getComparisonViews(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor || undefined,
  })
  const viewItems = useMemo(
    () => views.data?.pages.flatMap((page) => page.items) || [],
    [views.data?.pages],
  )
  const activeViewId = requestedViewId || viewItems[0]?.id || ''

  useEffect(() => {
    if (!requestedViewId && viewItems[0]) {
      setSearchParams({ view: viewItems[0].id }, { replace: true })
    }
  }, [requestedViewId, setSearchParams, viewItems])

  const snapshot = useQuery({
    queryKey: comparisonQueryKeys.snapshot(activeViewId),
    queryFn: () => getComparisonSnapshot(activeViewId),
    enabled: Boolean(activeViewId),
    refetchInterval: realtime.status === 'connected' ? 60_000 : 20_000,
  })
  const activeView = snapshot.data?.view || viewItems.find((view) => view.id === activeViewId) || null

  useEffect(() => {
    if (!(snapshot.error instanceof ApiError) || snapshot.error.status !== 404) return
    const fallback = viewItems.find((view) => view.id !== requestedViewId)
    if (fallback) setSearchParams({ view: fallback.id }, { replace: true })
  }, [requestedViewId, setSearchParams, snapshot.error, viewItems])

  const subscriptionOptions = useInfiniteQuery({
    queryKey: comparisonQueryKeys.subscriptionOptions(),
    queryFn: ({ pageParam }) => getComparisonSubscriptionOptions(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor || undefined,
    enabled: editorMode !== 'closed',
  })
  const editorOptions = useMemo(() => {
    const optionById = new Map<string, ComparisonSubscriptionOption>()
    for (const page of subscriptionOptions.data?.pages || []) {
      for (const option of page.items) optionById.set(option.id, option)
    }
    for (const route of snapshot.data?.routes || []) {
      if (!optionById.has(route.subscriptionId)) optionById.set(route.subscriptionId, routeToOption(route))
    }
    if (activeView) {
      for (const subscriptionId of activeView.subscriptionIds) {
        if (!optionById.has(subscriptionId)) {
          optionById.set(subscriptionId, {
            id: subscriptionId,
            name: '已删除的订阅',
            enabled: false,
            currency: activeView.currency,
            origin: '',
            destination: '',
            tripType: 'oneway',
            departureDate: '',
            returnDate: null,
            directOnly: false,
            missing: true,
          })
        }
      }
    }
    return [...optionById.values()]
  }, [activeView, snapshot.data?.routes, subscriptionOptions.data?.pages])

  const createMutation = useMutation({
    mutationFn: (input: CreateComparisonViewInput) => createComparisonView(input),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.views() })
      setSearchParams({ view: created.id }, { replace: true })
      setEditorMode('closed')
      showToast('success', '对比视图已创建', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '创建对比视图失败', { translate: false }),
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateComparisonViewInput }) => updateComparisonView(id, input),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.views() })
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.snapshot(updated.id) })
      setEditorMode('closed')
      showToast('success', '对比视图已更新', { translate: false })
    },
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.views() })
      if (activeViewId) queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.snapshot(activeViewId) })
      showToast('error', error instanceof Error ? error.message : '更新对比视图失败', { translate: false })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: deleteComparisonView,
    onSuccess: (_result, deletedId) => {
      queryClient.removeQueries({ queryKey: comparisonQueryKeys.snapshot(deletedId) })
      queryClient.setQueryData(comparisonQueryKeys.views(), (current: unknown) => {
        const data = current as { pages?: Array<{ items: ComparisonView[] }> } | undefined
        if (!data?.pages) return current
        return {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.filter((view) => view.id !== deletedId),
          })),
        }
      })
      queryClient.invalidateQueries({ queryKey: comparisonQueryKeys.views() })
      const next = viewItems.find((view) => view.id !== deletedId)
      setSearchParams(next ? { view: next.id } : {}, { replace: true })
      showToast('success', '对比视图已删除', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '删除对比视图失败', { translate: false }),
  })

  const handleEditorSubmit = (input: ComparisonEditorSubmit) => {
    if (editorMode === 'edit' && activeView && input.expectedVersion !== undefined) {
      updateMutation.mutate({
        id: activeView.id,
        input: {
          name: input.name,
          trendDays: input.trendDays,
          subscriptionIds: input.subscriptionIds,
          expectedVersion: input.expectedVersion,
        },
      })
      return
    }
    createMutation.mutate({
      name: input.name,
      trendDays: input.trendDays,
      subscriptionIds: input.subscriptionIds,
      idempotencyKey: input.idempotencyKey,
    })
  }

  const handleDelete = async (view: ComparisonView) => {
    const accepted = await confirm({
      title: `删除「${view.name}」？`,
      description: '只删除这个对比视图，不会删除订阅或已经保存的机票价格数据。',
      confirmText: '删除视图',
      cancelText: '取消',
      variant: 'destructive',
    })
    if (accepted) deleteMutation.mutate(view.id)
  }

  const sortedRoutes = useMemo(
    () => sortComparisonRoutes(snapshot.data?.routes || [], sort),
    [snapshot.data?.routes, sort],
  )
  const selectableViews = useMemo(() => {
    const byId = new Map(viewItems.map((view) => [view.id, view]))
    if (snapshot.data?.view) byId.set(snapshot.data.view.id, snapshot.data.view)
    return [...byId.values()]
  }, [snapshot.data?.view, viewItems])
  const submitting = createMutation.isPending || updateMutation.isPending

  return (
    <PageShell
      title="航线对比"
      description="把多条已订阅航线放在同一快照中比较，详细报价与订阅日期日历价始终分开。"
      width="7xl"
      actions={(
        <>
          {selectableViews.length ? (
            <Select value={activeViewId} onValueChange={(view) => setSearchParams({ view })}>
              <SelectTrigger className="w-full sm:w-[260px]" aria-label="选择对比视图"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {selectableViews.map((view) => <SelectItem key={view.id} value={view.id}>{view.name}</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
          ) : null}
          {views.hasNextPage ? (
            <Button type="button" variant="outline" onClick={() => views.fetchNextPage()} disabled={views.isFetchingNextPage}>
              {views.isFetchingNextPage ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : null}
              加载更多视图
            </Button>
          ) : null}
          <Button type="button" onClick={() => setEditorMode('create')}>
            <Plus data-icon="inline-start" />新建视图
          </Button>
        </>
      )}
    >
      <QueryState loading={views.isPending} error={views.error} onRetry={() => views.refetch()}>
        {activeViewId ? (
          <QueryState loading={snapshot.isPending} error={snapshot.error} onRetry={() => snapshot.refetch()}>
            {snapshot.data ? (
              <div className="flex flex-col gap-6">
                <DataModeNotice meta={snapshot.data.meta} />
                <div className="flex flex-col gap-4 border-b pb-5 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold">{snapshot.data.view.name}</h2>
                      <Badge variant="outline">{snapshot.data.view.currency}</Badge>
                      <Badge variant="secondary">近 {snapshot.data.view.trendDays} 天</Badge>
                      <Badge variant={snapshot.data.view.comparable ? 'secondary' : 'outline'}>
                        {snapshot.data.view.activeRouteCount}/{snapshot.data.view.configuredRouteCount} 条有效航线
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">快照生成于 {formatDateTime(snapshot.data.meta.generatedAt)}；实时连接 {realtime.status === 'connected' ? '正常' : '降级轮询'}。</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button type="button" variant="outline" size="icon" onClick={() => snapshot.refetch()} disabled={snapshot.isFetching} aria-label="刷新对比快照">
                          <RefreshCw className={snapshot.isFetching ? 'animate-spin' : undefined} aria-hidden="true" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>刷新快照</TooltipContent>
                    </Tooltip>
                    <Button type="button" variant="outline" onClick={() => setEditorMode('edit')}>
                      <Pencil data-icon="inline-start" />编辑
                    </Button>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button type="button" variant="ghost" size="icon" onClick={() => handleDelete(snapshot.data.view)} disabled={deleteMutation.isPending} aria-label={`删除 ${snapshot.data.view.name}`}>
                          <Trash2 aria-hidden="true" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>删除视图</TooltipContent>
                    </Tooltip>
                  </div>
                </div>

                {!snapshot.data.view.comparable || snapshot.data.view.missingSubscriptionCount > 0 ? (
                  <Alert>
                    <AlertTriangle aria-hidden="true" />
                    <AlertTitle>当前视图无法完整比较</AlertTitle>
                    <AlertDescription className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <span>{snapshot.data.view.missingSubscriptionCount > 0
                        ? `缺少 ${snapshot.data.view.missingSubscriptionCount} 条订阅；当前有效航线少于 2 条时只展示残余数据，不得视为有效比较。`
                        : '当前有效航线不足 2 条，只展示残余数据，不得视为有效比较。'}</span>
                      <Button type="button" variant="outline" size="sm" onClick={() => setEditorMode('edit')}>编辑视图</Button>
                    </AlertDescription>
                  </Alert>
                ) : null}

                {snapshot.data.routes.length ? (
                  <>
                    <PageSurface title="价格趋势" description="切换数据轨查看每日真实价格点；图表不会补零或沿用前一天价格。">
                      <ComparisonTrendChart
                        key={snapshot.data.view.id}
                        routes={snapshot.data.routes}
                        currency={snapshot.data.view.currency}
                      />
                    </PageSurface>
                    <PageSurface
                      title="航线价格摘要"
                      description="详细报价、日历价、观测时间和直飞证据分列展示。"
                      actions={(
                        <Select value={sort} onValueChange={(value) => setSort(value as ComparisonSort)}>
                          <SelectTrigger className="w-full sm:w-44" aria-label="航线排序"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectGroup>
                              <SelectItem value="position">保存顺序</SelectItem>
                              <SelectItem value="detailed-low">详细价从低到高</SelectItem>
                              <SelectItem value="detailed-high">详细价从高到低</SelectItem>
                              <SelectItem value="calendar-low">日历价从低到高</SelectItem>
                              <SelectItem value="calendar-high">日历价从高到低</SelectItem>
                              <SelectItem value="period-low">周期最低价优先</SelectItem>
                              <SelectItem value="change-low">降幅优先</SelectItem>
                              <SelectItem value="change-high">涨幅优先</SelectItem>
                              <SelectItem value="freshness">数据新鲜优先</SelectItem>
                            </SelectGroup>
                          </SelectContent>
                        </Select>
                      )}
                      bodyClassName="p-0"
                    >
                      <ComparisonRouteSummary routes={sortedRoutes} />
                    </PageSurface>
                  </>
                ) : <EmptyState title="视图中没有可用航线" description="编辑视图并选择仍然存在的订阅后即可恢复比较。" actions={<Button type="button" onClick={() => setEditorMode('edit')}>编辑视图</Button>} />}
              </div>
            ) : null}
          </QueryState>
        ) : (
          <EmptyState
            title="还没有对比视图"
            description="选择 2 至 8 条同币种订阅，建立第一组可重复查看的航线比较。"
            actions={<Button type="button" onClick={() => setEditorMode('create')}><Plus data-icon="inline-start" />新建视图</Button>}
          />
        )}
      </QueryState>

      {editorMode !== 'closed' ? (
        <ComparisonViewEditor
          open
          view={editorMode === 'edit' ? activeView : null}
          options={editorOptions}
          hasMoreOptions={Boolean(subscriptionOptions.hasNextPage)}
          loadingOptions={subscriptionOptions.isPending}
          loadingMoreOptions={subscriptionOptions.isFetchingNextPage}
          optionsError={subscriptionOptions.error}
          submitting={submitting}
          onOpenChange={(open) => { if (!open) setEditorMode('closed') }}
          onLoadMoreOptions={() => subscriptionOptions.fetchNextPage()}
          onRetryOptions={() => subscriptionOptions.refetch()}
          onSubmit={handleEditorSubmit}
        />
      ) : null}
    </PageShell>
  )
}
