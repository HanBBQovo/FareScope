import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock3, Mail, MessageSquareMore, Plus, Send, Smartphone, Webhook, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import {
  createNotificationChannel,
  fareQueryKeys,
  getNotificationChannels,
  updateNotificationChannel,
  type NotificationChannel,
  type NotificationChannelList,
  type NotificationChannelScheduleInput,
  type NotificationChannelType,
  type UpdateNotificationChannelInput,
} from '@/api/fares'
import { AlertWorkflow } from '@/components/fare/AlertWorkflow'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Combobox } from '@/components/ui/combobox'
import { EmptyState } from '@/components/ui/empty-state'
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { formatDateTime } from '@/lib/formatters'

const channelIcons = { email: Mail, webhook: Webhook, telegram: Send, bark: Smartphone, pushplus: MessageSquareMore }
const channelLabels = { email: '邮箱', webhook: 'Webhook', telegram: 'Telegram', bark: 'Bark', pushplus: 'PushPlus' }
const weekdays = [
  { value: 0, label: '周一' },
  { value: 1, label: '周二' },
  { value: 2, label: '周三' },
  { value: 3, label: '周四' },
  { value: 4, label: '周五' },
  { value: 5, label: '周六' },
  { value: 6, label: '周日' },
]
const allWeekdays = weekdays.map((day) => day.value)
const fallbackTimezones = ['Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'UTC', 'Europe/London', 'America/New_York', 'America/Los_Angeles']
const supportedTimezones = (() => {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: 'timeZone') => string[] }
  return intl.supportedValuesOf?.('timeZone') || fallbackTimezones
})()
const timezoneOptions = Array.from(new Set(['Asia/Shanghai', 'Asia/Tokyo', 'UTC', ...supportedTimezones]))
  .map((timezone) => ({ value: timezone, label: timezone }))

interface DeliveryScheduleDraft {
  constrained: boolean
  timezone: string
  quietEnabled: boolean
  quietHoursStart: string
  quietHoursEnd: string
  allowedWeekdays: number[]
}

function browserTimezone(): string {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return timezoneOptions.some((option) => option.value === timezone) ? timezone : 'Asia/Shanghai'
}

function defaultSchedule(): DeliveryScheduleDraft {
  return {
    constrained: false,
    timezone: browserTimezone(),
    quietEnabled: true,
    quietHoursStart: '22:00',
    quietHoursEnd: '08:00',
    allowedWeekdays: [...allWeekdays],
  }
}

function scheduleFromChannel(channel: NotificationChannel): DeliveryScheduleDraft {
  return {
    constrained: channel.quietHoursStart !== null || channel.allowedWeekdays !== null,
    timezone: channel.timezone || browserTimezone(),
    quietEnabled: channel.quietHoursStart !== null && channel.quietHoursEnd !== null,
    quietHoursStart: channel.quietHoursStart?.slice(0, 5) || '22:00',
    quietHoursEnd: channel.quietHoursEnd?.slice(0, 5) || '08:00',
    allowedWeekdays: channel.allowedWeekdays ? [...channel.allowedWeekdays] : [...allWeekdays],
  }
}

function schedulePayload(schedule: DeliveryScheduleDraft): NotificationChannelScheduleInput {
  if (!schedule.constrained) {
    return { timezone: null, quietHoursStart: null, quietHoursEnd: null, allowedWeekdays: null }
  }
  return {
    timezone: schedule.timezone,
    quietHoursStart: schedule.quietEnabled ? schedule.quietHoursStart : null,
    quietHoursEnd: schedule.quietEnabled ? schedule.quietHoursEnd : null,
    allowedWeekdays: [...schedule.allowedWeekdays].sort((left, right) => left - right),
  }
}

function scheduleError(schedule: DeliveryScheduleDraft): string | null {
  if (!schedule.constrained) return null
  if (!schedule.timezone) return '请选择投递时区。'
  if (!schedule.allowedWeekdays.length) return '至少选择一个允许投递的星期。'
  if (schedule.quietEnabled && (!schedule.quietHoursStart || !schedule.quietHoursEnd)) return '请填写完整的安静时段。'
  if (schedule.quietEnabled && schedule.quietHoursStart === schedule.quietHoursEnd) return '安静时段的开始和结束时间不能相同。'
  return null
}

function scheduleSummary(channel: NotificationChannel): string {
  if (channel.quietHoursStart === null && channel.allowedWeekdays === null) return '随时投递'
  const parts = [channel.timezone || '未设置时区']
  if (channel.quietHoursStart && channel.quietHoursEnd) {
    parts.push(`静默 ${channel.quietHoursStart.slice(0, 5)}–${channel.quietHoursEnd.slice(0, 5)}`)
  }
  if (channel.allowedWeekdays && channel.allowedWeekdays.length < 7) {
    const labels = weekdays.filter((day) => channel.allowedWeekdays?.includes(day.value)).map((day) => day.label)
    parts.push(labels.join('、'))
  } else {
    parts.push('每天')
  }
  return parts.join(' · ')
}

function DeliveryScheduleFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string
  value: DeliveryScheduleDraft
  onChange: (next: DeliveryScheduleDraft) => void
}) {
  const update = <Key extends keyof DeliveryScheduleDraft>(key: Key, next: DeliveryScheduleDraft[Key]) => {
    onChange({ ...value, [key]: next })
  }
  const toggleWeekday = (weekday: number, checked: boolean) => {
    update('allowedWeekdays', checked
      ? Array.from(new Set([...value.allowedWeekdays, weekday]))
      : value.allowedWeekdays.filter((item) => item !== weekday))
  }

  return (
    <div className="flex flex-col gap-5 border-t pt-5">
      <Field orientation="horizontal" className="rounded-md border bg-muted/20 px-3 py-3">
        <FieldContent>
          <FieldLabel htmlFor={`${idPrefix}-constrained`}>限制投递时间</FieldLabel>
          <FieldDescription>关闭时，待发送消息会按现有重试策略立即投递。</FieldDescription>
        </FieldContent>
        <Switch id={`${idPrefix}-constrained`} checked={value.constrained} onCheckedChange={(checked) => update('constrained', checked)} />
      </Field>
      {value.constrained ? (
        <>
          <FieldGroup className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>时区</FieldLabel>
              <Combobox options={timezoneOptions} value={value.timezone} onValueChange={(timezone) => update('timezone', timezone)} searchPlaceholder="搜索 IANA 时区..." />
            </Field>
            <Field orientation="horizontal" className="rounded-md border px-3 py-3">
              <FieldContent>
                <FieldLabel htmlFor={`${idPrefix}-quiet-enabled`}>安静时段</FieldLabel>
                <FieldDescription>跨午夜时段会自动延续到次日。</FieldDescription>
              </FieldContent>
              <Switch id={`${idPrefix}-quiet-enabled`} checked={value.quietEnabled} onCheckedChange={(checked) => update('quietEnabled', checked)} />
            </Field>
          </FieldGroup>
          {value.quietEnabled ? (
            <FieldGroup className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={`${idPrefix}-quiet-start`}>开始</FieldLabel>
                <Input id={`${idPrefix}-quiet-start`} type="time" step="60" value={value.quietHoursStart} onChange={(event) => update('quietHoursStart', event.target.value)} required />
              </Field>
              <Field>
                <FieldLabel htmlFor={`${idPrefix}-quiet-end`}>结束</FieldLabel>
                <Input id={`${idPrefix}-quiet-end`} type="time" step="60" value={value.quietHoursEnd} onChange={(event) => update('quietHoursEnd', event.target.value)} required />
              </Field>
            </FieldGroup>
          ) : null}
          <Field>
            <FieldLabel>允许投递的星期</FieldLabel>
            <div className="flex flex-wrap gap-2">
              {weekdays.map((day) => (
                <label key={day.value} className="flex min-w-20 items-center gap-2 rounded-md border px-3 py-2 text-sm">
                  <Checkbox checked={value.allowedWeekdays.includes(day.value)} onCheckedChange={(checked) => toggleWeekday(day.value, checked === true)} />
                  <span>{day.label}</span>
                </label>
              ))}
            </div>
          </Field>
        </>
      ) : null}
    </div>
  )
}

export default function NotificationSettings() {
  const [searchParams] = useSearchParams()
  const [showCreate, setShowCreate] = useState(false)
  const [type, setType] = useState<NotificationChannelType>('webhook')
  const [label, setLabel] = useState('主要通知渠道')
  const [destination, setDestination] = useState('')
  const [createSchedule, setCreateSchedule] = useState<DeliveryScheduleDraft>(defaultSchedule)
  const [editingChannelId, setEditingChannelId] = useState<string | null>(null)
  const [editSchedule, setEditSchedule] = useState<DeliveryScheduleDraft | null>(null)
  const channels = useQuery({ queryKey: fareQueryKeys.notificationChannels(), queryFn: getNotificationChannels })
  const editingChannel = channels.data?.items.find((channel) => channel.id === editingChannelId) || null
  const queryClient = useQueryClient()
  const { showToast } = useGlobalToast()

  const createMutation = useMutation({
    mutationFn: createNotificationChannel,
    onSuccess: (created) => {
      queryClient.setQueryData<NotificationChannelList>(fareQueryKeys.notificationChannels(), (current) => current
        ? { ...current, items: [...current.items, created] }
        : { meta: { mode: 'live', generatedAt: new Date().toISOString() }, items: [created] })
      setDestination('')
      setCreateSchedule(defaultSchedule())
      setShowCreate(false)
      showToast('success', '通知渠道已添加', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '添加通知渠道失败', { translate: false }),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateNotificationChannelInput; closeEditor?: boolean }) => updateNotificationChannel(id, input),
    onSuccess: (updated, variables) => {
      queryClient.setQueryData<NotificationChannelList>(fareQueryKeys.notificationChannels(), (current) => current
        ? { ...current, items: current.items.map((item) => item.id === updated.id ? updated : item) }
        : current)
      if (variables.closeEditor) {
        setEditingChannelId(null)
        setEditSchedule(null)
        showToast('success', '投递时间已更新', { translate: false })
      }
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '更新通知渠道失败', { translate: false }),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!destination.trim()) return
    const validationError = scheduleError(createSchedule)
    if (validationError) {
      showToast('error', validationError, { translate: false })
      return
    }
    createMutation.mutate({
      type,
      label: label.trim() || channelLabels[type],
      destination: destination.trim(),
      ...schedulePayload(createSchedule),
    })
  }

  const beginScheduleEdit = (channel: NotificationChannel) => {
    setEditingChannelId(channel.id)
    setEditSchedule(scheduleFromChannel(channel))
  }

  const saveSchedule = () => {
    if (!editingChannel || !editSchedule) return
    const validationError = scheduleError(editSchedule)
    if (validationError) {
      showToast('error', validationError, { translate: false })
      return
    }
    updateMutation.mutate({
      id: editingChannel.id,
      input: schedulePayload(editSchedule),
      closeEditor: true,
    })
  }

  return (
    <PageShell
      title="通知与告警"
      description="先配置接收渠道，再为航线订阅创建服务端告警规则，并核对事件与实际投递状态。"
      width="6xl"
      actions={<Button type="button" onClick={() => setShowCreate((value) => !value)}>{showCreate ? <X data-icon="inline-start" /> : <Plus data-icon="inline-start" />}{showCreate ? '取消添加' : '添加渠道'}</Button>}
    >
      <div className="flex flex-col gap-6">
        {showCreate ? (
          <PageSurface title="添加通知渠道" description="服务端保存时应加密敏感配置，界面只回显脱敏地址。">
            <form onSubmit={submit} className="flex flex-col gap-5">
              <FieldGroup className="grid gap-4 md:grid-cols-3">
                <Field>
                  <FieldLabel htmlFor="channel-type">类型</FieldLabel>
                  <Select value={type} onValueChange={(value) => setType(value as NotificationChannelType)}>
                    <SelectTrigger id="channel-type"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectGroup><SelectItem value="webhook">Webhook</SelectItem><SelectItem value="telegram">Telegram</SelectItem><SelectItem value="bark">Bark</SelectItem><SelectItem value="pushplus">PushPlus</SelectItem></SelectGroup></SelectContent>
                  </Select>
                </Field>
                <Field>
                  <FieldLabel htmlFor="channel-label">名称</FieldLabel>
                  <Input id="channel-label" value={label} onChange={(event) => setLabel(event.target.value)} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="channel-destination">接收地址</FieldLabel>
                  <Input id="channel-destination" type="text" value={destination} onChange={(event) => setDestination(event.target.value)} required />
                  <FieldDescription>{type === 'webhook' ? 'HTTPS Webhook URL' : type === 'telegram' ? 'Telegram Bot Token|Chat ID' : type === 'bark' ? 'Bark 推送 URL 或设备标识' : 'PushPlus token'}</FieldDescription>
                </Field>
              </FieldGroup>
              <DeliveryScheduleFields idPrefix="new-channel" value={createSchedule} onChange={setCreateSchedule} />
              <div className="flex justify-end"><Button type="submit" disabled={!destination.trim() || createMutation.isPending}>保存渠道</Button></div>
            </form>
          </PageSurface>
        ) : null}

        <QueryState loading={channels.isPending} error={channels.error} onRetry={() => channels.refetch()}>
          {channels.data ? (
            <div className="flex flex-col gap-4">
              <DataModeNotice meta={channels.data.meta} />
              {channels.data.items.some((channel) => channel.type === 'email' && !channel.verifiedAt) ? (
                <Alert>
                  <AlertTitle>邮件渠道尚未可用</AlertTitle>
                  <AlertDescription>服务端当前未配置 SMTP。邮箱渠道会保留在列表中，但不会产生实际邮件投递。</AlertDescription>
                </Alert>
              ) : null}
              <PageSurface title="我的通知渠道" description="状态来自服务端；未验证渠道可能被投递器拒绝。" bodyClassName="p-0">
                {channels.data.items.length ? (
                  <div className="divide-y">
                    {channels.data.items.map((channel) => {
                      const Icon = channelIcons[channel.type]
                      return (
                        <article key={channel.id} className="flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center">
                          <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-secondary text-secondary-foreground"><Icon className="size-5" aria-hidden="true" /></div>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-medium">{channel.label}</h3>
                              <Badge variant="outline">{channelLabels[channel.type]}</Badge>
                              <Badge variant={channel.verifiedAt ? 'secondary' : channel.type === 'email' ? 'destructive' : 'outline'}>
                                {channel.verifiedAt ? '已验证' : channel.type === 'email' ? '邮件未配置' : '未验证'}
                              </Badge>
                            </div>
                            <p className="mt-1 truncate font-mono text-sm text-muted-foreground">{channel.destinationMasked}</p>
                            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"><Clock3 className="size-3.5" aria-hidden="true" />{scheduleSummary(channel)}</p>
                            {channel.verifiedAt ? <p className="mt-1 text-xs text-muted-foreground">验证于 {formatDateTime(channel.verifiedAt)}</p> : channel.type === 'email' ? <p className="mt-1 text-xs text-destructive">等待服务端 SMTP 配置</p> : <p className="mt-1 text-xs text-muted-foreground">服务端尚未标记为已验证</p>}
                          </div>
                          <div className="flex items-center justify-between gap-3 sm:justify-end">
                            <Button type="button" variant="outline" size="sm" onClick={() => beginScheduleEdit(channel)}><Clock3 data-icon="inline-start" />投递时段</Button>
                            <Switch checked={channel.enabled} disabled={updateMutation.isPending} onCheckedChange={(enabled) => updateMutation.mutate({ id: channel.id, input: { enabled } })} aria-label={`${channel.enabled ? '停用' : '启用'} ${channel.label}`} />
                          </div>
                        </article>
                      )
                    })}
                  </div>
                ) : <EmptyState title="没有通知渠道" description="添加 Webhook、Telegram、Bark 或 PushPlus 后，价格提醒才有发送目标。" />}
              </PageSurface>
              {editingChannel && editSchedule ? (
                <PageSurface
                  title={`${editingChannel.label} · 投递时段`}
                  description="安静时段和允许星期均按所选时区计算，等待中的消息会顺延到下一个可投递时刻。"
                  actions={<Button type="button" variant="ghost" size="icon" onClick={() => { setEditingChannelId(null); setEditSchedule(null) }} aria-label="关闭投递时段编辑"><X aria-hidden="true" /></Button>}
                >
                  <div className="flex flex-col gap-5">
                    <DeliveryScheduleFields idPrefix={`channel-${editingChannel.id}`} value={editSchedule} onChange={setEditSchedule} />
                    <div className="flex justify-end"><Button type="button" onClick={saveSchedule} disabled={updateMutation.isPending}>{updateMutation.isPending ? '保存中…' : '保存投递时间'}</Button></div>
                  </div>
                </PageSurface>
              ) : null}
            </div>
          ) : null}
        </QueryState>
        {!channels.isPending ? <AlertWorkflow channels={channels.data?.items || []} initialSubscriptionId={searchParams.get('subscription') || ''} /> : null}
      </div>
    </PageShell>
  )
}
