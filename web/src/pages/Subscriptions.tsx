import { useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BellRing, ChartNoAxesCombined, Plus, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import {
  createSubscription,
  deleteSubscription,
  fareQueryKeys,
  getSubscriptions,
  updateSubscription,
  type FlightSearchParams,
  type SubscriptionList,
} from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { FlightSearchForm } from '@/components/fare/FlightSearchForm'
import { PriceFreshnessBadge } from '@/components/fare/PriceFreshnessBadge'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/components/ui/use-confirm'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { formatCurrency, formatDateTime } from '@/lib/formatters'

function formatFare(value: number | null, currency = 'CNY'): string {
  return value === null ? '暂无报价' : formatCurrency(value / 100, currency)
}

function formatConfiguredFare(value: number | null, currency = 'CNY'): string {
  return value === null ? '未设置' : formatCurrency(value / 100, currency)
}

export default function Subscriptions() {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('上海到东京价格提醒')
  const [targetPriceYuan, setTargetPriceYuan] = useState('')
  const subscriptions = useInfiniteQuery({
    queryKey: fareQueryKeys.subscriptions(),
    queryFn: ({ pageParam }) => getSubscriptions(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor || undefined,
  })
  const subscriptionPages = useMemo(() => subscriptions.data?.pages || [], [subscriptions.data?.pages])
  const subscriptionItems = useMemo(() => subscriptionPages.flatMap((page) => page.items), [subscriptionPages])
  const latestSubscriptionMeta = subscriptionPages[0]?.meta
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const { showToast } = useGlobalToast()

  const createMutation = useMutation({
    mutationFn: ({ query, targetPriceMinor }: { query: FlightSearchParams; targetPriceMinor: number | null }) => createSubscription({
      name: name.trim() || `${query.origin} → ${query.destination}`,
      origin: query.origin,
      destination: query.destination,
      tripType: query.tripType,
      departureDate: query.departureDate,
      returnDate: query.tripType === 'roundtrip' ? query.returnDate : undefined,
      directOnly: query.directOnly,
      maxPriceMinor: query.maxPriceMinor ?? null,
      targetPriceMinor,
      enabled: true,
      passengers: query.passengers,
      airlineCodes: query.airlineCodes,
      departureAirports: query.departureAirports,
      arrivalAirports: query.arrivalAirports,
      maxStops: query.maxStops,
      maxDurationMinutes: query.maxDurationMinutes,
      departureMinuteStart: query.departureMinuteStart,
      departureMinuteEnd: query.departureMinuteEnd,
    }),
    onSuccess: (_subscription, variables) => {
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.subscriptions() })
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.overview() })
      setCreating(false)
      setTargetPriceYuan('')
      showToast('success', variables.targetPriceMinor === null ? '订阅已创建' : '订阅和目标价告警已创建', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '创建订阅失败', { translate: false }),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateSubscription(id, { enabled }),
    onSuccess: (updated) => {
      queryClient.setQueryData(fareQueryKeys.subscriptions(), (current: unknown) => {
        const pages = (current as { pages?: SubscriptionList[] } | undefined)?.pages
        if (!pages) return current
        return {
          ...(current as object),
          pages: pages.map((page) => ({
            ...page,
            items: page.items.map((item) => item.id === updated.id
              ? { ...item, enabled: updated.enabled, updatedAt: updated.updatedAt }
              : item),
          })),
        }
      })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '更新订阅失败', { translate: false }),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteSubscription,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.subscriptions() })
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.overview() })
      showToast('success', '订阅已删除', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '删除订阅失败', { translate: false }),
  })

  const handleDelete = async (id: string, label: string) => {
    const accepted = await confirm({
      title: `删除「${label}」？`,
      description: '删除后不会再调度新的采集任务，但已经保存的历史观测不会被前端展示。',
      confirmText: '删除订阅',
      cancelText: '取消',
      variant: 'destructive',
    })
    if (accepted) deleteMutation.mutate(id)
  }

  const handleCreate = (query: FlightSearchParams) => {
    const normalizedTargetPrice = targetPriceYuan.trim()
    if (!normalizedTargetPrice) {
      createMutation.mutate({ query, targetPriceMinor: null })
      return
    }

    const targetPrice = Number(normalizedTargetPrice)
    if (!Number.isFinite(targetPrice) || targetPrice <= 0) {
      showToast('error', '提醒目标价必须大于 0 元', { translate: false })
      return
    }
    createMutation.mutate({ query, targetPriceMinor: Math.round(targetPrice * 100) })
  }

  return (
    <PageShell
      title="订阅管理"
      description="当前登录账号拥有自己的订阅和通知配置；相同查询条件由服务端共享采集结果。"
      width="7xl"
      actions={
        <Button type="button" onClick={() => setCreating((value) => !value)}>
          {creating ? <X data-icon="inline-start" /> : <Plus data-icon="inline-start" />}
          {creating ? '取消创建' : '新建订阅'}
        </Button>
      }
    >
      <div className="flex flex-col gap-6">
        {creating ? (
          <PageSurface title="新建价格订阅" description="保存后由采集调度器定期刷新。结果最高价只控制保存哪些报价；填写提醒目标价会同时创建价格阈值告警。">
            <div className="flex flex-col gap-6">
              <FieldGroup className="grid gap-4 md:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor="subscription-name">订阅名称</FieldLabel>
                  <Input id="subscription-name" value={name} onChange={(event) => setName(event.target.value)} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="subscription-target-price">提醒目标价（元）</FieldLabel>
                  <Input
                    id="subscription-target-price"
                    type="number"
                    min="0.01"
                    step="0.01"
                    inputMode="decimal"
                    value={targetPriceYuan}
                    onChange={(event) => setTargetPriceYuan(event.target.value)}
                    placeholder="例如 1800"
                  />
                  <FieldDescription>不参与报价筛选；保存后默认使用所有启用渠道，其他触发条件可在“通知与告警”中配置。</FieldDescription>
                </Field>
              </FieldGroup>
              <FlightSearchForm submitting={createMutation.isPending} onSubmit={handleCreate} />
            </div>
          </PageSurface>
        ) : null}

        <QueryState loading={subscriptions.isPending} error={subscriptions.error} onRetry={() => subscriptions.refetch()}>
          {subscriptions.data ? (
            <div className="flex flex-col gap-4">
              {latestSubscriptionMeta ? <DataModeNotice meta={latestSubscriptionMeta} /> : null}
              <PageSurface title="我的订阅" description={`${subscriptionItems.filter((item) => item.enabled).length} 条启用中`} bodyClassName="p-0">
                {subscriptionItems.length ? (
                  <div className="divide-y">
                    {subscriptionItems.map((subscription) => {
                      const reachedTarget = subscription.priceStatus === 'current' && subscription.targetPriceMinor !== null && subscription.latestPriceMinor !== null && subscription.latestPriceMinor <= subscription.targetPriceMinor
                      return (
                        <article key={subscription.id} className="grid gap-4 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-medium">{subscription.name}</h3>
                              <Badge variant={subscription.enabled ? 'secondary' : 'outline'}>{subscription.enabled ? '监控中' : '已暂停'}</Badge>
                              {reachedTarget ? <Badge variant="outline">达到提醒目标</Badge> : null}
                            </div>
                            <p className="mt-2 text-sm text-muted-foreground">
                              {subscription.origin} → {subscription.destination} · {subscription.tripType === 'roundtrip' ? '往返' : '单程'} · {subscription.departureDate}
                              {subscription.returnDate ? ` 至 ${subscription.returnDate}` : ''} · {subscription.directOnly ? '仅直飞' : '不限中转'}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {subscription.airlineCodes.map((code) => <Badge key={code} variant="outline">{code}</Badge>)}
                              {subscription.maxStops !== null ? <Badge variant="outline">最多 {subscription.maxStops} 次中转</Badge> : null}
                              {subscription.maxDurationMinutes !== null ? <Badge variant="outline">≤ {Math.round(subscription.maxDurationMinutes / 60)} 小时</Badge> : null}
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <span>最后观测 {subscription.latestObservedAt ? formatDateTime(subscription.latestObservedAt) : '尚未采集'}</span>
                              <PriceFreshnessBadge status={subscription.priceStatus} />
                              <span>上次任务 {subscription.lastCollectedAt ? formatDateTime(subscription.lastCollectedAt) : '尚未运行'}</span>
                              <span>下次调度 {subscription.nextDueAt ? formatDateTime(subscription.nextDueAt) : '未安排'}</span>
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center justify-between gap-4 lg:justify-end">
                            <div className="grid grid-cols-3 gap-3 text-left lg:text-right">
                              <div>
                                <p className="text-xs text-muted-foreground">最新报价</p>
                                <p className="mt-1 whitespace-nowrap font-mono text-sm font-medium">{formatFare(subscription.latestPriceMinor, subscription.currency)}</p>
                              </div>
                              <div>
                                <p className="text-xs text-muted-foreground">结果最高价</p>
                                <p className="mt-1 whitespace-nowrap font-mono text-sm font-medium">{formatConfiguredFare(subscription.maxPriceMinor, subscription.currency)}</p>
                              </div>
                              <div>
                                <p className="text-xs text-muted-foreground">提醒目标价</p>
                                <p className="mt-1 whitespace-nowrap font-mono text-sm font-medium">{formatConfiguredFare(subscription.targetPriceMinor, subscription.targetCurrency || subscription.currency)}</p>
                              </div>
                            </div>
                            <Switch checked={subscription.enabled} disabled={toggleMutation.isPending} onCheckedChange={(enabled) => toggleMutation.mutate({ id: subscription.id, enabled })} aria-label={`${subscription.enabled ? '暂停' : '启用'} ${subscription.name}`} />
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button type="button" variant="outline" size="icon" onClick={() => navigate(`/notifications?subscription=${subscription.id}`)} aria-label={`配置 ${subscription.name} 告警`}>
                                  <BellRing aria-hidden="true" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>配置价格告警</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button type="button" variant="outline" size="icon" onClick={() => navigate(`/history?route=${subscription.id}`)} aria-label={`查看 ${subscription.name} 历史`}>
                                  <ChartNoAxesCombined aria-hidden="true" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>查看价格历史</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button type="button" variant="ghost" size="icon" onClick={() => handleDelete(subscription.id, subscription.name)} disabled={deleteMutation.isPending} aria-label={`删除 ${subscription.name}`}>
                                  <Trash2 aria-hidden="true" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>删除订阅</TooltipContent>
                            </Tooltip>
                          </div>
                        </article>
                      )
                    })}
                  </div>
                ) : <EmptyState title="还没有订阅" description="创建第一条航线订阅后，FareScope 才会开始保存价格历史。" />}
              </PageSurface>
              {subscriptions.hasNextPage ? (
                <div className="flex justify-center">
                  <Button type="button" variant="outline" onClick={() => subscriptions.fetchNextPage()} disabled={subscriptions.isFetchingNextPage}>
                    {subscriptions.isFetchingNextPage ? '加载中…' : '加载更多订阅'}
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </QueryState>
      </div>
    </PageShell>
  )
}
