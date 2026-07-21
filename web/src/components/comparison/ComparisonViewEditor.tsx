import { useEffect, useMemo, useState } from 'react'
import { LoaderCircle } from 'lucide-react'

import type {
  ComparisonSubscriptionOption,
  ComparisonTrendDays,
  ComparisonView,
} from '@/api/comparisons'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { MultiSelect } from '@/components/ui/multi-select'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { validateComparisonSelection } from '@/lib/comparison'

export interface ComparisonEditorSubmit {
  name: string
  trendDays: ComparisonTrendDays
  subscriptionIds: string[]
  idempotencyKey: string
  expectedVersion?: number
}

interface ComparisonViewEditorProps {
  open: boolean
  view: ComparisonView | null
  options: ComparisonSubscriptionOption[]
  hasMoreOptions: boolean
  loadingOptions: boolean
  loadingMoreOptions: boolean
  optionsError: unknown
  submitting: boolean
  onOpenChange: (open: boolean) => void
  onLoadMoreOptions: () => void
  onRetryOptions: () => void
  onSubmit: (input: ComparisonEditorSubmit) => void
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `comparison-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function routeDescription(option: ComparisonSubscriptionOption): string {
  const dates = option.returnDate
    ? `${option.departureDate} 至 ${option.returnDate}`
    : option.departureDate
  return `${option.tripType === 'roundtrip' ? '往返' : '单程'} · ${dates} · ${option.directOnly ? '仅直飞' : '不限中转'} · ${option.currency}`
}

export function ComparisonViewEditor({
  open,
  view,
  options,
  hasMoreOptions,
  loadingOptions,
  loadingMoreOptions,
  optionsError,
  submitting,
  onOpenChange,
  onLoadMoreOptions,
  onRetryOptions,
  onSubmit,
}: ComparisonViewEditorProps) {
  const [name, setName] = useState(view?.name || '')
  const [trendDays, setTrendDays] = useState<ComparisonTrendDays>(view?.trendDays || 30)
  const [subscriptionIds, setSubscriptionIds] = useState<string[]>(view?.subscriptionIds || [])
  const [formError, setFormError] = useState('')
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey)
  const viewId = view?.id || ''
  const viewVersion = view?.version || 0
  const viewName = view?.name || ''
  const viewTrendDays = view?.trendDays || 30
  const viewSubscriptionIds = (view?.subscriptionIds || []).join(',')

  useEffect(() => {
    if (!open) return
    setName(viewName)
    setTrendDays(viewTrendDays)
    setSubscriptionIds(viewSubscriptionIds ? viewSubscriptionIds.split(',') : [])
    setFormError('')
    if (!viewId) setIdempotencyKey(createIdempotencyKey())
  }, [open, viewId, viewName, viewSubscriptionIds, viewTrendDays, viewVersion])

  const selectedCurrency = useMemo(
    () => options.find((option) => subscriptionIds.includes(option.id))?.currency,
    [options, subscriptionIds],
  )
  const selectOptions = useMemo(() => options.map((option) => ({
    value: option.id,
    label: option.missing ? '已删除的订阅' : `${option.name} · ${option.origin} → ${option.destination}`,
    description: option.missing ? '这条订阅已不存在，请从视图中移除' : routeDescription(option),
    keywords: [option.name, option.origin, option.destination, option.currency],
    disabled: !subscriptionIds.includes(option.id) && (
      option.missing
      || subscriptionIds.length >= 8
      || (selectedCurrency !== undefined && selectedCurrency !== option.currency)
    ),
  })), [options, selectedCurrency, subscriptionIds])

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedName = name.trim()
    if (!normalizedName) {
      setFormError('请输入视图名称')
      return
    }
    const selectionError = validateComparisonSelection(subscriptionIds, options)
    if (selectionError) {
      setFormError(selectionError)
      return
    }
    setFormError('')
    onSubmit({
      name: normalizedName,
      trendDays,
      subscriptionIds: [...new Set(subscriptionIds)],
      idempotencyKey,
      expectedVersion: view?.version,
    })
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col sm:max-w-xl">
        <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
          <SheetHeader>
            <SheetTitle>{view ? '编辑对比视图' : '新建对比视图'}</SheetTitle>
            <SheetDescription>选择 2 至 8 条同币种订阅；保存后统一读取同一时刻的价格快照。</SheetDescription>
          </SheetHeader>
          <SheetBody className="min-h-0 flex-1 overflow-y-auto border-t">
            <FieldGroup>
              <Field data-invalid={Boolean(formError)}>
                <FieldLabel htmlFor="comparison-view-name">视图名称</FieldLabel>
                <Input
                  id="comparison-view-name"
                  value={name}
                  maxLength={160}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="例如：日本暑期航线"
                  aria-invalid={Boolean(formError)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="comparison-trend-days">趋势范围</FieldLabel>
                <Select value={String(trendDays)} onValueChange={(value) => setTrendDays(Number(value) as ComparisonTrendDays)}>
                  <SelectTrigger id="comparison-trend-days"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="7">近 7 天</SelectItem>
                      <SelectItem value="30">近 30 天</SelectItem>
                      <SelectItem value="90">近 90 天</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <FieldDescription>范围会随视图保存，不会触发逐航线请求。</FieldDescription>
              </Field>
              <Field data-invalid={Boolean(formError)}>
                <FieldLabel>订阅航线</FieldLabel>
                <MultiSelect
                  options={selectOptions}
                  value={subscriptionIds}
                  onValueChange={setSubscriptionIds}
                  placeholder="选择要比较的订阅"
                  searchPlaceholder="按名称、机场代码或币种搜索"
                  emptyText="没有匹配的订阅"
                  maxPreviewItems={4}
                  disabled={loadingOptions}
                />
                <FieldDescription>
                  {loadingOptions ? '正在加载订阅…' : `已选 ${subscriptionIds.length}/8；选定第一条后，只能继续选择相同币种。`}
                </FieldDescription>
                {optionsError ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm text-destructive">
                    <span>{optionsError instanceof Error ? optionsError.message : '订阅加载失败'}</span>
                    <Button type="button" variant="outline" size="sm" onClick={onRetryOptions}>重试</Button>
                  </div>
                ) : null}
                {hasMoreOptions ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="w-fit"
                    onClick={onLoadMoreOptions}
                    disabled={loadingMoreOptions}
                  >
                    {loadingMoreOptions ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : null}
                    {loadingMoreOptions ? '加载中' : '加载更多订阅'}
                  </Button>
                ) : null}
                <FieldError>{formError}</FieldError>
              </Field>
            </FieldGroup>
          </SheetBody>
          <SheetFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>取消</Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : null}
              {submitting ? '保存中' : '保存视图'}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
