import { FormEvent, useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  FileDown,
  FileJson,
  FileSpreadsheet,
  LoaderCircle,
  Trash2,
} from 'lucide-react'

import {
  createPriceExport,
  deletePriceExport,
  exportQueryKeys,
  getPriceExports,
  priceExportDownloadUrl,
  type ExportFormat,
  type ExportStatus,
  type PriceExportJob,
} from '@/api/exports'
import { PageSurface } from '@/components/layout/PageScaffold'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet, FieldLegend } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useConfirm } from '@/components/ui/use-confirm'
import { useGlobalToast } from '@/components/ui/use-global-toast'
import { formatDateTime } from '@/lib/formatters'

interface PriceExportPanelProps {
  subscriptionId: string
  defaultDays: number
}

const statusLabels: Record<ExportStatus, string> = {
  pending: '等待处理',
  running: '正在生成',
  succeeded: '可下载',
  failed: '生成失败',
  expired: '已过期',
  deleting: '正在删除',
}

function dateInputValue(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function initialDateRange(days: number): { start: string; end: string } {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - Math.max(0, days - 1))
  return { start: dateInputValue(start), end: dateInputValue(end) }
}

function exclusiveEndIso(value: string): string {
  const date = new Date(`${value}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString()
}

function inclusiveRangeLabel(rangeStart: string, rangeEnd: string): string {
  const inclusiveEnd = new Date(rangeEnd)
  inclusiveEnd.setUTCDate(inclusiveEnd.getUTCDate() - 1)
  return `${rangeStart.slice(0, 10)} – ${inclusiveEnd.toISOString().slice(0, 10)}`
}

function sizeLabel(sizeBytes: number | null): string {
  if (sizeBytes === null) return '—'
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`
}

function statusVariant(status: ExportStatus): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'succeeded') return 'default'
  if (status === 'failed') return 'destructive'
  if (status === 'expired' || status === 'deleting') return 'outline'
  return 'secondary'
}

function stageProgress(status: ExportStatus): number {
  if (status === 'pending') return 15
  if (status === 'running') return 60
  return 100
}

export function PriceExportPanel({ subscriptionId, defaultDays }: PriceExportPanelProps) {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const { showToast } = useGlobalToast()
  const defaults = useMemo(() => initialDateRange(defaultDays), [defaultDays])
  const [open, setOpen] = useState(false)
  const [format, setFormat] = useState<ExportFormat>('csv')
  const [rangeStart, setRangeStart] = useState(defaults.start)
  const [rangeEnd, setRangeEnd] = useState(defaults.end)

  const exportsQuery = useInfiniteQuery({
    queryKey: exportQueryKeys.list(subscriptionId),
    queryFn: ({ pageParam }) => getPriceExports(subscriptionId, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    refetchInterval: (query) => query.state.data?.pages.some((page) => page.items.some(
      (job) => job.status === 'pending' || job.status === 'running' || job.status === 'deleting',
    )) ? 3000 : false,
  })
  const createMutation = useMutation({
    mutationFn: createPriceExport,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: exportQueryKeys.list(subscriptionId) })
      setOpen(false)
      showToast('success', '导出任务已创建', { translate: false })
    },
    onError: (error) => showToast('error', error.message, { translate: false }),
  })
  const deleteMutation = useMutation({
    mutationFn: deletePriceExport,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: exportQueryKeys.list(subscriptionId) })
      showToast('success', '导出记录已删除', { translate: false })
    },
    onError: (error) => showToast('error', error.message, { translate: false }),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!rangeStart || !rangeEnd || rangeStart > rangeEnd) {
      showToast('error', '请选择有效的开始和结束日期', { translate: false })
      return
    }
    createMutation.mutate({
      subscriptionId,
      format,
      rangeStart: `${rangeStart}T00:00:00.000Z`,
      rangeEnd: exclusiveEndIso(rangeEnd),
      idempotencyKey: crypto.randomUUID(),
    })
  }

  const remove = async (job: PriceExportJob) => {
    const approved = await confirm({
      title: '删除导出记录？',
      description: job.downloadReady ? '已生成的文件也会一并删除。' : '这个操作无法撤销。',
      confirmText: '删除',
      variant: 'destructive',
    })
    if (approved) deleteMutation.mutate(job.id)
  }

  const jobs = exportsQuery.data?.pages.flatMap((page) => page.items) ?? []
  return (
    <PageSurface
      title="数据导出"
      description="后台导出该订阅航线的全部标准化价格观测，包括归档历史；航司、价格等结果筛选不会缩小导出范围。"
      actions={(
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button size="sm"><FileDown data-icon="inline-start" />新建导出</Button>
          </DialogTrigger>
          <DialogContent>
            <form className="flex flex-col gap-6" onSubmit={submit}>
              <DialogHeader>
                <DialogTitle>导出价格观测</DialogTitle>
                <DialogDescription>
                  文件由后台生成。开始和结束日期均按 UTC 自然日计算，结束日期包含当天。
                </DialogDescription>
              </DialogHeader>
              <FieldGroup>
                <FieldSet>
                  <FieldLegend variant="label">文件格式</FieldLegend>
                  <RadioGroup value={format} onValueChange={(value) => setFormat(value as ExportFormat)}>
                    <Field orientation="horizontal">
                      <RadioGroupItem value="csv" id="export-csv" />
                      <FieldLabel htmlFor="export-csv"><FileSpreadsheet />CSV</FieldLabel>
                    </Field>
                    <Field orientation="horizontal">
                      <RadioGroupItem value="json" id="export-json" />
                      <FieldLabel htmlFor="export-json"><FileJson />JSON</FieldLabel>
                    </Field>
                  </RadioGroup>
                </FieldSet>
                <Field>
                  <FieldLabel htmlFor="export-start">开始日期</FieldLabel>
                  <Input id="export-start" type="date" value={rangeStart} max={rangeEnd} onChange={(event) => setRangeStart(event.target.value)} required />
                </Field>
                <Field>
                  <FieldLabel htmlFor="export-end">结束日期</FieldLabel>
                  <Input id="export-end" type="date" value={rangeEnd} min={rangeStart} onChange={(event) => setRangeEnd(event.target.value)} required />
                  <FieldDescription>导出范围内所有标准化价格点，金额保持整数最小货币单位。</FieldDescription>
                </Field>
              </FieldGroup>
              <DialogFooter className="gap-2">
                <Button type="button" variant="outline" onClick={() => setOpen(false)}>取消</Button>
                <Button type="submit" disabled={createMutation.isPending}>
                  {createMutation.isPending ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <FileDown data-icon="inline-start" />}
                  创建任务
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      )}
      bodyClassName="p-0"
    >
      {exportsQuery.isPending ? (
        <div className="flex min-h-32 items-center justify-center text-muted-foreground">
          <LoaderCircle className="animate-spin" aria-label="加载导出任务" />
        </div>
      ) : exportsQuery.isError ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-3 p-5 text-center">
          <p className="text-sm text-destructive">{exportsQuery.error.message}</p>
          <Button variant="outline" size="sm" onClick={() => exportsQuery.refetch()}>重试</Button>
        </div>
      ) : jobs.length === 0 ? (
        <div className="p-5"><EmptyState title="暂无导出任务" description="创建任务后可在这里查看进度并下载结果。" /></div>
      ) : (
        <>
          <Table containerClassName="max-h-none">
            <TableHeader>
              <TableRow>
                <TableHead>创建时间</TableHead>
                <TableHead>范围</TableHead>
                <TableHead>格式</TableHead>
                <TableHead>状态 / 进度</TableHead>
                <TableHead>结果</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {jobs.map((job) => (
                <TableRow key={job.id}>
                  <TableCell>{formatDateTime(job.createdAt)}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {inclusiveRangeLabel(job.rangeStart, job.rangeEnd)}
                  </TableCell>
                  <TableCell className="font-mono uppercase">{job.format}</TableCell>
                  <TableCell className="min-w-40">
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={statusVariant(job.status)}>{statusLabels[job.status]}</Badge>
                        {job.status === 'running' ? <span className="text-xs text-muted-foreground">已读取 {job.processedRows.toLocaleString()} 行</span> : null}
                      </div>
                      {(job.status === 'pending' || job.status === 'running') ? <Progress value={stageProgress(job.status)} aria-label={statusLabels[job.status]} /> : null}
                      {job.errorMessage ? <span className="text-xs text-destructive">{job.errorMessage}</span> : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <div>{job.rowCount === null ? '—' : `${job.rowCount.toLocaleString()} 行 · ${sizeLabel(job.sizeBytes)}`}</div>
                    <div>数据截止 {formatDateTime(job.snapshotAt)}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      {job.downloadReady ? (
                        <Button asChild variant="outline" size="icon" title="下载导出文件">
                          <a href={priceExportDownloadUrl(job.id)}><Download /><span className="sr-only">下载</span></a>
                        </Button>
                      ) : null}
                      <Button variant="ghost" size="icon" title="删除导出记录" disabled={job.status === 'running' || job.status === 'deleting' || deleteMutation.isPending} onClick={() => remove(job)}>
                        <Trash2 /><span className="sr-only">删除</span>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {exportsQuery.hasNextPage ? (
            <div className="flex justify-center border-t p-4">
              <Button variant="outline" size="sm" onClick={() => exportsQuery.fetchNextPage()} disabled={exportsQuery.isFetchingNextPage}>
                {exportsQuery.isFetchingNextPage ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : null}
                加载更早记录
              </Button>
            </div>
          ) : null}
        </>
      )}
    </PageSurface>
  )
}
