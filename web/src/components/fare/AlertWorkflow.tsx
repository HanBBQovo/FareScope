import { useMemo, useState, type FormEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, BellRing, Clock3, Plus, RefreshCw, Trash2 } from 'lucide-react'

import {
  createAlertRule,
  deleteAlertRule,
  fareQueryKeys,
  getAlertEvents,
  getAlertRules,
  getNotificationDeliveries,
  getSubscriptions,
  updateAlertRule,
  type AlertRule,
  type AlertRuleType,
  type NotificationChannel,
} from '@/api/fares'
import { QueryState } from '@/components/fare/QueryState'
import { PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { EmptyState } from '@/components/ui/empty-state'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useConfirm } from '@/components/ui/use-confirm'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { formatCurrency, formatDateTime } from '@/lib/formatters'

const ruleLabels: Record<AlertRuleType, string> = {
  price_threshold: '低于目标价',
  absolute_drop: '价格下降指定金额',
  percentage_drop: '价格下降指定比例',
  new_low: '出现阶段新低',
  direct_available: '出现直飞报价',
  round_trip_range: '进入往返价格区间',
}

const creatableRuleTypes: AlertRuleType[] = [
  'price_threshold',
  'absolute_drop',
  'percentage_drop',
  'new_low',
  'direct_available',
]

function priceText(value: number | null, currency: string | null): string {
  return value === null ? '未设置' : formatCurrency(value / 100, currency || 'CNY')
}

function ruleSummary(rule: AlertRule): string {
  if (rule.ruleType === 'percentage_drop') {
    return `下降 ${(rule.thresholdPercentage || 0) / 100}% · ${rule.comparisonWindowDays || 30} 天基准`
  }
  if (rule.ruleType === 'new_low') {
    return `${rule.comparisonWindowDays || 30} 天内新低`
  }
  if (rule.ruleType === 'absolute_drop') {
    return `下降 ${priceText(rule.thresholdPriceMinor, rule.thresholdCurrency)} · ${rule.comparisonWindowDays || 30} 天基准`
  }
  if (rule.ruleType === 'direct_available') {
    return `直飞价格不高于 ${priceText(rule.thresholdPriceMinor, rule.thresholdCurrency)}`
  }
  if (rule.ruleType === 'price_threshold') {
    return `总价不高于 ${priceText(rule.thresholdPriceMinor, rule.thresholdCurrency)}`
  }
  return '往返价格进入设定区间'
}

function deliveryStatus(status: string): { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' } {
  if (status === 'succeeded') return { label: '已发送', variant: 'secondary' }
  if (status === 'failed') return { label: '失败', variant: 'destructive' }
  if (status === 'suppressed') return { label: '已抑制', variant: 'outline' }
  if (status === 'sending') return { label: '发送中', variant: 'default' }
  return { label: '待发送', variant: 'outline' }
}

export function AlertWorkflow({
  channels,
  initialSubscriptionId = '',
}: {
  channels: NotificationChannel[]
  initialSubscriptionId?: string
}) {
  const [showCreate, setShowCreate] = useState(Boolean(initialSubscriptionId))
  const [subscriptionId, setSubscriptionId] = useState(initialSubscriptionId)
  const [name, setName] = useState('目标价格提醒')
  const [ruleType, setRuleType] = useState<AlertRuleType>('price_threshold')
  const [thresholdYuan, setThresholdYuan] = useState('2500')
  const [percentage, setPercentage] = useState('10')
  const [windowDays, setWindowDays] = useState('30')
  const [cooldownHours, setCooldownHours] = useState('6')
  const [channelIds, setChannelIds] = useState<string[]>([])
  const [formError, setFormError] = useState('')
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const { showToast } = useGlobalToast()

  const subscriptions = useQuery({
    queryKey: fareQueryKeys.subscriptionOptions(),
    queryFn: () => getSubscriptions(),
  })
  const rules = useQuery({ queryKey: fareQueryKeys.alertRules(), queryFn: () => getAlertRules() })
  const events = useInfiniteQuery({
    queryKey: fareQueryKeys.alertEvents(),
    queryFn: ({ pageParam }) => getAlertEvents(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor || undefined,
  })
  const deliveries = useQuery({ queryKey: fareQueryKeys.deliveries(), queryFn: getNotificationDeliveries })
  const selectedSubscriptionId = subscriptionId || subscriptions.data?.items[0]?.id || ''
  const subscriptionsById = useMemo(
    () => new Map((subscriptions.data?.items || []).map((item) => [item.id, item])),
    [subscriptions.data?.items],
  )
  const selectedSubscription = subscriptionsById.get(selectedSubscriptionId)
  const eventItems = useMemo(() => events.data?.pages.flatMap((page) => page.items) || [], [events.data?.pages])
  const eventsById = useMemo(() => new Map(eventItems.map((item) => [item.id, item])), [eventItems])
  const channelsById = useMemo(() => new Map(channels.map((channel) => [channel.id, channel])), [channels])

  const refreshWorkflow = () => {
    rules.refetch()
    events.refetch()
    deliveries.refetch()
  }

  const createMutation = useMutation({
    mutationFn: createAlertRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.alertRules() })
      setShowCreate(false)
      setFormError('')
      showToast('success', '告警规则已创建', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '创建告警规则失败', { translate: false }),
  })
  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateAlertRule(id, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: fareQueryKeys.alertRules() }),
    onError: (error) => showToast('error', error instanceof Error ? error.message : '更新告警规则失败', { translate: false }),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteAlertRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: fareQueryKeys.alertRules() })
      showToast('success', '告警规则已删除', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '删除告警规则失败', { translate: false }),
  })

  const submitRule = (event: FormEvent) => {
    event.preventDefault()
    const threshold = Number(thresholdYuan)
    const percentageValue = Number(percentage)
    const comparisonWindowDays = Number(windowDays)
    const cooldownSeconds = Number(cooldownHours) * 3600
    const needsPrice = ['price_threshold', 'absolute_drop', 'direct_available'].includes(ruleType)
    if (!selectedSubscriptionId) {
      setFormError('请先创建并选择一条航线订阅。')
      return
    }
    if (!name.trim()) {
      setFormError('请输入规则名称。')
      return
    }
    if (needsPrice && (!Number.isFinite(threshold) || threshold <= 0)) {
      setFormError('请输入有效的正数价格。')
      return
    }
    if (ruleType === 'percentage_drop' && (!Number.isFinite(percentageValue) || percentageValue <= 0 || percentageValue > 100)) {
      setFormError('降价比例需在 0 到 100 之间。')
      return
    }

    setFormError('')
    createMutation.mutate({
      subscriptionId: selectedSubscriptionId,
      name: name.trim(),
      ruleType,
      enabled: true,
      thresholdPriceMinor: needsPrice ? Math.round(threshold * 100) : null,
      thresholdCurrency: needsPrice ? selectedSubscription?.currency || 'CNY' : null,
      thresholdPercentage: ruleType === 'percentage_drop' ? Math.round(percentageValue * 100) : null,
      comparisonWindowDays: ['absolute_drop', 'percentage_drop', 'new_low'].includes(ruleType)
        ? comparisonWindowDays
        : null,
      cooldownSeconds,
      channelIds,
      ruleConfig: {},
    })
  }

  const removeRule = async (rule: AlertRule) => {
    const accepted = await confirm({
      title: `删除「${rule.name}」？`,
      description: '删除后不再为这条规则生成新告警；已有事件和投递记录仍会保留。',
      confirmText: '删除规则',
      cancelText: '取消',
      variant: 'destructive',
    })
    if (accepted) deleteMutation.mutate(rule.id)
  }

  return (
    <div className="flex flex-col gap-6">
      <PageSurface
        title="价格告警规则"
        description="规则由服务端在采集成功后执行；没有规则时，通知渠道不会自行发送价格提醒。"
        actions={(
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={refreshWorkflow} disabled={rules.isFetching || events.isFetching || deliveries.isFetching}>
              <RefreshCw data-icon="inline-start" className={rules.isFetching || events.isFetching || deliveries.isFetching ? 'animate-spin' : undefined} />
              刷新
            </Button>
            <Button type="button" size="sm" onClick={() => setShowCreate((value) => !value)}>
              <Plus data-icon="inline-start" />{showCreate ? '收起' : '新建规则'}
            </Button>
          </div>
        )}
        bodyClassName="p-0"
      >
        <QueryState loading={rules.isPending || subscriptions.isPending} error={rules.error || subscriptions.error} onRetry={() => { rules.refetch(); subscriptions.refetch() }}>
          {rules.data?.items.length ? (
            <div className="divide-y">
              {rules.data.items.map((rule) => {
                const subscription = subscriptionsById.get(rule.subscriptionId)
                return (
                  <article key={rule.id} className="grid gap-4 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-medium">{rule.name}</h3>
                        <Badge variant={rule.enabled ? 'secondary' : 'outline'}>{rule.enabled ? '已启用' : '已暂停'}</Badge>
                        <Badge variant="outline">{ruleLabels[rule.ruleType]}</Badge>
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">
                        {subscription ? `${subscription.origin} → ${subscription.destination}` : `订阅 ${rule.subscriptionId.slice(0, 8)}`} · {ruleSummary(rule)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {rule.channelIds.length ? `${rule.channelIds.length} 个指定渠道` : '所有启用渠道'} · 冷却 {Math.round(rule.cooldownSeconds / 3600)} 小时 · 更新于 {formatDateTime(rule.updatedAt)}
                      </p>
                    </div>
                    <div className="flex items-center justify-end gap-3">
                      <Switch checked={rule.enabled} disabled={toggleMutation.isPending} onCheckedChange={(enabled) => toggleMutation.mutate({ id: rule.id, enabled })} aria-label={`${rule.enabled ? '暂停' : '启用'} ${rule.name}`} />
                      <Button type="button" variant="ghost" size="icon" onClick={() => removeRule(rule)} disabled={deleteMutation.isPending} aria-label={`删除 ${rule.name}`}>
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </div>
                  </article>
                )
              })}
            </div>
          ) : <EmptyState icon={<BellRing />} title="还没有价格告警" description="创建规则并绑定通知渠道后，采集命中条件才会产生可追踪的告警与投递记录。" />}
        </QueryState>
      </PageSurface>

      {showCreate ? (
        <PageSurface title="新建告警规则" description="阈值按整张行程含税总价计算，往返订阅使用往返总价。">
          <form className="flex flex-col gap-5" onSubmit={submitRule}>
            <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Field>
                <FieldLabel htmlFor="alert-subscription">航线订阅</FieldLabel>
                <Select value={selectedSubscriptionId} onValueChange={setSubscriptionId} disabled={!subscriptions.data?.items.length}>
                  <SelectTrigger id="alert-subscription"><SelectValue placeholder="选择订阅" /></SelectTrigger>
                  <SelectContent><SelectGroup>{subscriptions.data?.items.map((item) => <SelectItem key={item.id} value={item.id}>{item.origin} → {item.destination} · {item.tripType === 'roundtrip' ? '往返' : '单程'}</SelectItem>)}</SelectGroup></SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="alert-name">规则名称</FieldLabel>
                <Input id="alert-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required />
              </Field>
              <Field>
                <FieldLabel htmlFor="alert-type">触发条件</FieldLabel>
                <Select value={ruleType} onValueChange={(value) => setRuleType(value as AlertRuleType)}>
                  <SelectTrigger id="alert-type"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectGroup>{creatableRuleTypes.map((type) => <SelectItem key={type} value={type}>{ruleLabels[type]}</SelectItem>)}</SelectGroup></SelectContent>
                </Select>
              </Field>
            </FieldGroup>

            <FieldGroup className="grid gap-4 md:grid-cols-3">
              {['price_threshold', 'absolute_drop', 'direct_available'].includes(ruleType) ? (
                <Field>
                  <FieldLabel htmlFor="alert-price">{ruleType === 'absolute_drop' ? '下降金额' : '最高含税价'}（{selectedSubscription?.currency || 'CNY'}）</FieldLabel>
                  <Input id="alert-price" type="number" min="0.01" step="0.01" value={thresholdYuan} onChange={(event) => setThresholdYuan(event.target.value)} required />
                </Field>
              ) : null}
              {ruleType === 'percentage_drop' ? (
                <Field>
                  <FieldLabel htmlFor="alert-percentage">下降比例（%）</FieldLabel>
                  <Input id="alert-percentage" type="number" min="0.01" max="100" step="0.01" value={percentage} onChange={(event) => setPercentage(event.target.value)} required />
                </Field>
              ) : null}
              {['absolute_drop', 'percentage_drop', 'new_low'].includes(ruleType) ? (
                <Field>
                  <FieldLabel htmlFor="alert-window">历史基准</FieldLabel>
                  <Select value={windowDays} onValueChange={setWindowDays}>
                    <SelectTrigger id="alert-window"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectGroup><SelectItem value="7">近 7 天</SelectItem><SelectItem value="30">近 30 天</SelectItem><SelectItem value="90">近 90 天</SelectItem><SelectItem value="180">近 180 天</SelectItem></SelectGroup></SelectContent>
                  </Select>
                </Field>
              ) : null}
              <Field>
                <FieldLabel htmlFor="alert-cooldown">重复提醒间隔</FieldLabel>
                <Select value={cooldownHours} onValueChange={setCooldownHours}>
                  <SelectTrigger id="alert-cooldown"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectGroup><SelectItem value="1">1 小时</SelectItem><SelectItem value="6">6 小时</SelectItem><SelectItem value="12">12 小时</SelectItem><SelectItem value="24">24 小时</SelectItem><SelectItem value="72">3 天</SelectItem></SelectGroup></SelectContent>
                </Select>
              </Field>
            </FieldGroup>

            <Field>
              <FieldLabel>通知渠道</FieldLabel>
              <FieldDescription>不勾选时使用所有已启用渠道；勾选后只发送到指定渠道。</FieldDescription>
              {channels.length ? (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {channels.map((channel) => (
                    <label key={channel.id} className="flex min-w-0 items-center gap-3 rounded-md border px-3 py-3 text-sm">
                      <Checkbox
                        checked={channelIds.includes(channel.id)}
                        disabled={!channel.enabled}
                        onCheckedChange={(checked) => setChannelIds((current) => checked === true
                          ? [...current, channel.id]
                          : current.filter((id) => id !== channel.id))}
                      />
                      <span className="min-w-0 flex-1 truncate">{channel.label}</span>
                      {!channel.enabled ? <Badge variant="outline">已停用</Badge> : null}
                    </label>
                  ))}
                </div>
              ) : <p className="text-sm text-muted-foreground">当前没有通知渠道；规则可以保存，但不会产生实际投递。</p>}
            </Field>

            {formError ? (
              <Alert variant="destructive"><AlertCircle data-icon="inline-start" /><AlertTitle>无法创建规则</AlertTitle><AlertDescription>{formError}</AlertDescription></Alert>
            ) : null}
            <div className="flex justify-end"><Button type="submit" disabled={createMutation.isPending || !selectedSubscriptionId}>{createMutation.isPending ? '保存中…' : '保存规则'}</Button></div>
          </form>
        </PageSurface>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-2">
        <PageSurface title="最近告警事件" description="每条事件对应一次服务端规则命中。" bodyClassName="p-0">
          <QueryState loading={events.isPending} error={events.error} onRetry={() => events.refetch()}>
            {eventItems.length ? (
              <div>
                <div className="divide-y">
                  {eventItems.map((event) => (
                    <article key={event.id} className="px-5 py-4">
                      <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{event.title}</h3>{event.suppressedAt ? <Badge variant="outline">已抑制</Badge> : null}</div>
                      <p className="mt-1 text-sm text-muted-foreground">{event.body}</p>
                      <p className="mt-2 text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
                    </article>
                  ))}
                </div>
                {events.hasNextPage ? (
                  <div className="flex justify-center border-t p-4">
                    <Button type="button" variant="outline" size="sm" onClick={() => events.fetchNextPage()} disabled={events.isFetchingNextPage}>
                      {events.isFetchingNextPage ? '加载中…' : '加载更早告警'}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : <EmptyState title="暂无告警事件" description="规则首次命中后，事件内容会显示在这里。" />}
          </QueryState>
        </PageSurface>

        <PageSurface title="最近投递" description="查看渠道发送状态、重试次数和错误码。" bodyClassName="p-0">
          <QueryState loading={deliveries.isPending} error={deliveries.error} onRetry={() => deliveries.refetch()}>
            {deliveries.data?.items.length ? (
              <Table containerClassName="max-h-none">
                <TableHeader><TableRow><TableHead>状态</TableHead><TableHead>渠道</TableHead><TableHead>告警</TableHead><TableHead>更新时间</TableHead></TableRow></TableHeader>
                <TableBody>
                  {deliveries.data.items.slice(0, 20).map((delivery) => {
                    const status = deliveryStatus(delivery.status)
                    return (
                      <TableRow key={delivery.id}>
                        <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
                        <TableCell>{channelsById.get(delivery.notificationChannelId)?.label || delivery.notificationChannelId.slice(0, 8)}</TableCell>
                        <TableCell className="max-w-48 truncate" title={eventsById.get(delivery.alertEventId)?.title}>{eventsById.get(delivery.alertEventId)?.title || delivery.alertEventId.slice(0, 8)}</TableCell>
                        <TableCell>{formatDateTime(delivery.updatedAt)}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            ) : <EmptyState icon={<Clock3 />} title="暂无投递记录" description="告警命中并绑定可用渠道后，发送结果会显示在这里。" />}
          </QueryState>
        </PageSurface>
      </div>
    </div>
  )
}
