import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, MessageSquareMore, Plus, Send, Smartphone, Webhook } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import {
  createNotificationChannel,
  fareQueryKeys,
  getNotificationChannels,
  updateNotificationChannel,
  type NotificationChannelList,
  type NotificationChannelType,
} from '@/api/fares'
import { AlertWorkflow } from '@/components/fare/AlertWorkflow'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { formatDateTime } from '@/lib/formatters'

const channelIcons = { email: Mail, webhook: Webhook, telegram: Send, bark: Smartphone, pushplus: MessageSquareMore }
const channelLabels = { email: '邮箱', webhook: 'Webhook', telegram: 'Telegram', bark: 'Bark', pushplus: 'PushPlus' }

export default function NotificationSettings() {
  const [searchParams] = useSearchParams()
  const [showCreate, setShowCreate] = useState(false)
  const [type, setType] = useState<NotificationChannelType>('webhook')
  const [label, setLabel] = useState('主要通知渠道')
  const [destination, setDestination] = useState('')
  const channels = useQuery({ queryKey: fareQueryKeys.notificationChannels(), queryFn: getNotificationChannels })
  const queryClient = useQueryClient()
  const { showToast } = useGlobalToast()

  const createMutation = useMutation({
    mutationFn: createNotificationChannel,
    onSuccess: (created) => {
      queryClient.setQueryData<NotificationChannelList>(fareQueryKeys.notificationChannels(), (current) => current
        ? { ...current, items: [...current.items, created] }
        : { meta: { mode: 'live', generatedAt: new Date().toISOString() }, items: [created] })
      setDestination('')
      setShowCreate(false)
      showToast('success', '通知渠道已添加', { translate: false })
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '添加通知渠道失败', { translate: false }),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateNotificationChannel(id, { enabled }),
    onSuccess: (updated) => {
      queryClient.setQueryData<NotificationChannelList>(fareQueryKeys.notificationChannels(), (current) => current
        ? { ...current, items: current.items.map((item) => item.id === updated.id ? { ...item, enabled: updated.enabled } : item) }
        : current)
    },
    onError: (error) => showToast('error', error instanceof Error ? error.message : '更新通知渠道失败', { translate: false }),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!destination.trim()) return
    createMutation.mutate({ type, label: label.trim() || channelLabels[type], destination: destination.trim() })
  }

  return (
    <PageShell
      title="通知与告警"
      description="先配置接收渠道，再为航线订阅创建服务端告警规则，并核对事件与实际投递状态。"
      width="6xl"
      actions={<Button type="button" onClick={() => setShowCreate((value) => !value)}><Plus data-icon="inline-start" />添加渠道</Button>}
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
                            {channel.verifiedAt ? <p className="mt-1 text-xs text-muted-foreground">验证于 {formatDateTime(channel.verifiedAt)}</p> : channel.type === 'email' ? <p className="mt-1 text-xs text-destructive">等待服务端 SMTP 配置</p> : <p className="mt-1 text-xs text-muted-foreground">服务端尚未标记为已验证</p>}
                          </div>
                          <Switch checked={channel.enabled} disabled={toggleMutation.isPending} onCheckedChange={(enabled) => toggleMutation.mutate({ id: channel.id, enabled })} aria-label={`${channel.enabled ? '停用' : '启用'} ${channel.label}`} />
                        </article>
                      )
                    })}
                  </div>
                ) : <EmptyState title="没有通知渠道" description="添加 Webhook、Telegram、Bark 或 PushPlus 后，价格提醒才有发送目标。" />}
              </PageSurface>
            </div>
          ) : null}
        </QueryState>
        {!channels.isPending ? <AlertWorkflow channels={channels.data?.items || []} initialSubscriptionId={searchParams.get('subscription') || ''} /> : null}
      </div>
    </PageShell>
  )
}
