import { useMemo, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { CalendarDays, CheckCircle2, CircleStop, Clock3, Plane, RefreshCw, SearchX, ShieldAlert } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { fareQueryKeys, searchFlights, type CollectionStateStatus, type FlightOffer, type FlightSearchParams } from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { FlightSearchForm } from '@/components/fare/FlightSearchForm'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { formatCurrency, formatDateTime } from '@/lib/formatters'
import { defaultFlightSearchValues } from '@/lib/flight-search'

function formatFare(value: number, currency: string): string {
  return formatCurrency(value / 100, currency)
}

function readQuery(searchParams: URLSearchParams): FlightSearchParams {
  const defaults = defaultFlightSearchValues()
  const optionalNumber = (key: string): number | undefined => {
    const value = searchParams.get(key)
    if (value === null || value === '') return undefined
    const number = Number(value)
    return Number.isFinite(number) ? number : undefined
  }
  return {
    tripType: searchParams.get('tripType') === 'roundtrip' ? 'roundtrip' : defaults.tripType,
    origin: searchParams.get('origin') || defaults.origin,
    destination: searchParams.get('destination') || defaults.destination,
    departureDate: searchParams.get('departureDate') || defaults.departureDate,
    returnDate: searchParams.get('tripType') === 'roundtrip' ? searchParams.get('returnDate') || defaults.returnDate : undefined,
    directOnly: searchParams.get('directOnly') === 'true',
    passengers: Number(searchParams.get('passengers') || defaults.passengers),
    airlineCodes: searchParams.get('airlineCodes') || undefined,
    departureAirports: searchParams.get('departureAirports') || undefined,
    arrivalAirports: searchParams.get('arrivalAirports') || undefined,
    maxPriceMinor: optionalNumber('maxPriceMinor'),
    maxStops: optionalNumber('maxStops'),
    maxDurationMinutes: optionalNumber('maxDurationMinutes'),
    departureMinuteStart: optionalNumber('departureMinuteStart'),
    departureMinuteEnd: optionalNumber('departureMinuteEnd'),
  }
}

function statusLabel(status: CollectionStateStatus): string {
  return {
    pending: '已排队',
    leased: '等待采集器',
    running: '采集中',
    succeeded: '采集完成',
    failed: '采集失败',
    canceled: '已取消',
  }[status]
}

function CollectionStateNotice({ status, errorCode }: { status: CollectionStateStatus; errorCode: string | null }) {
  if (status === 'succeeded') {
    return (
      <Alert>
        <CheckCircle2 data-icon="inline-start" />
        <AlertTitle>采集完成</AlertTitle>
        <AlertDescription>下面是本次已保存的报价快照，价格旁边会显示实际观测时间。</AlertDescription>
      </Alert>
    )
  }
  if (status === 'failed' || status === 'canceled') {
    return (
      <Alert variant="destructive">
        <ShieldAlert data-icon="inline-start" />
        <AlertTitle>{statusLabel(status)}</AlertTitle>
        <AlertDescription>{errorCode ? `错误码：${errorCode}` : '本次采集没有完成，稍后可以重新提交。'}</AlertDescription>
      </Alert>
    )
  }
  return (
    <Alert>
      <Clock3 data-icon="inline-start" />
      <AlertTitle>{statusLabel(status)}</AlertTitle>
      <AlertDescription>采集器正在处理这条查询，页面会自动刷新状态。</AlertDescription>
    </Alert>
  )
}

export default function FareExplorer() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialQuery = useMemo(() => readQuery(searchParams), [searchParams])
  const [submittedQuery, setSubmittedQuery] = useState<FlightSearchParams | null>(null)
  const result = useInfiniteQuery({
    queryKey: fareQueryKeys.search(submittedQuery || initialQuery),
    queryFn: ({ pageParam }) => searchFlights(
      submittedQuery as FlightSearchParams,
      typeof pageParam === 'string' ? pageParam : undefined,
    ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    enabled: Boolean(submittedQuery),
    refetchInterval: (query) => {
      const status = query.state.data?.pages[0]?.collection.status
      if (status !== 'pending' && status !== 'leased' && status !== 'running') return false
      return 10_000
    },
  })
  const resultPages = useMemo(() => result.data?.pages || [], [result.data?.pages])
  const firstResult = resultPages[0]
  const offers = useMemo(() => {
    const seen = new Set<string>()
    return resultPages.flatMap((page) => page.offers).filter((offer) => {
      if (seen.has(offer.id)) return false
      seen.add(offer.id)
      return true
    })
  }, [resultPages])

  const submit = (nextQuery: FlightSearchParams) => {
    setSubmittedQuery(nextQuery)
    const params: Record<string, string> = {
      tripType: nextQuery.tripType,
      origin: nextQuery.origin,
      destination: nextQuery.destination,
      departureDate: nextQuery.departureDate,
      directOnly: String(nextQuery.directOnly),
      passengers: String(nextQuery.passengers),
    }
    if (nextQuery.returnDate) params.returnDate = nextQuery.returnDate
    if (nextQuery.airlineCodes) params.airlineCodes = nextQuery.airlineCodes
    if (nextQuery.departureAirports) params.departureAirports = nextQuery.departureAirports
    if (nextQuery.arrivalAirports) params.arrivalAirports = nextQuery.arrivalAirports
    if (nextQuery.maxPriceMinor !== undefined) params.maxPriceMinor = String(nextQuery.maxPriceMinor)
    if (nextQuery.maxStops !== undefined) params.maxStops = String(nextQuery.maxStops)
    if (nextQuery.maxDurationMinutes !== undefined) params.maxDurationMinutes = String(nextQuery.maxDurationMinutes)
    if (nextQuery.departureMinuteStart !== undefined) params.departureMinuteStart = String(nextQuery.departureMinuteStart)
    if (nextQuery.departureMinuteEnd !== undefined) params.departureMinuteEnd = String(nextQuery.departureMinuteEnd)
    setSearchParams(params)
  }

  return (
    <PageShell
      title="航线探索"
      description="按单程或往返、直飞、价格、航空公司、中转和时间条件查询已采集的航班报价。"
      width="7xl"
      actions={<Button type="button" variant="outline" onClick={() => result.refetch()} disabled={!submittedQuery || result.isFetching}><RefreshCw data-icon="inline-start" className={result.isFetching ? 'animate-spin' : undefined} />刷新结果</Button>}
    >
      <div className="flex flex-col gap-6">
        <PageSurface title="查询条件" description="修改条件后点击查询，页面打开时不会自动触发上游采集。">
          <FlightSearchForm key={JSON.stringify(initialQuery)} initialValues={initialQuery} submitting={result.isFetching} onSubmit={submit} />
        </PageSurface>

        {!submittedQuery && !result.data ? (
          <PageSurface>
            <EmptyState icon={<SearchX />} title="等待查询" description="提交一组出发地、目的地和日期后，这里会显示实时采集状态与航班明细。" />
          </PageSurface>
        ) : null}

        {submittedQuery ? (
          <QueryState loading={result.isPending} error={result.error} onRetry={() => result.refetch()}>
            {firstResult ? (
              <div className="flex flex-col gap-4">
                <DataModeNotice meta={firstResult.meta} />
                <CollectionStateNotice status={firstResult.collection.status} errorCode={firstResult.collection.errorCode} />
                {firstResult.collection.status === 'succeeded' ? (
                  <PageSurface
                    title={`${firstResult.query.origin} → ${firstResult.query.destination}`}
                    description={`${firstResult.query.tripType === 'roundtrip' ? '往返' : '单程'} · ${firstResult.query.departureDate}${firstResult.query.returnDate ? ` 至 ${firstResult.query.returnDate}` : ''} · 已加载 ${offers.length} 个结果`}
                    bodyClassName="p-0"
                  >
                    {offers.length ? (
                      <>
                        <div className="divide-y">
                          {offers.map((offer) => <OfferRow key={offer.id} offer={offer} />)}
                        </div>
                        {result.hasNextPage ? (
                          <div className="flex justify-center border-t p-4">
                            <Button type="button" variant="outline" onClick={() => result.fetchNextPage()} disabled={result.isFetchingNextPage}>
                              {result.isFetchingNextPage ? '加载中…' : '加载更多报价'}
                            </Button>
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <EmptyState title="采集完成但没有符合条件的报价" description="这是真实空结果，不会被当作零价。可以放宽直飞、价格、航司或中转条件后重新查询。" />
                    )}
                  </PageSurface>
                ) : null}
              </div>
            ) : null}
          </QueryState>
        ) : null}
      </div>
    </PageShell>
  )
}

function offerLabel(offer: FlightOffer): string {
  const outbound = offer.legs.find((leg) => leg.direction === 'outbound')
  return outbound ? `${outbound.airline} · ${outbound.flightNumber}` : '航班行程'
}

function OfferRow({ offer }: { offer: FlightOffer }) {
  const [outbound, inbound] = [
    offer.legs.find((leg) => leg.direction === 'outbound'),
    offer.legs.find((leg) => leg.direction === 'inbound'),
  ]

  return (
    <article className="grid gap-5 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary"><Plane data-icon="inline-start" />{offerLabel(offer)}</Badge>
          {offer.legs.every((leg) => leg.stops === 0) ? <Badge variant="outline">直飞</Badge> : <Badge variant="outline"><CircleStop data-icon="inline-start" />含中转</Badge>}
          <span className="text-xs text-muted-foreground">{offer.provider} · 采集于 {formatDateTime(offer.observedAt)}</span>
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <LegSummary label="去程" leg={outbound} />
          {inbound ? <LegSummary label="返程" leg={inbound} /> : null}
        </div>
      </div>
      <div className="flex items-end justify-between gap-4 border-t pt-4 lg:flex-col lg:items-end lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
        <div>
          <p className="text-xs text-muted-foreground">含税总价 · {offer.cabin}</p>
          <p className="mt-1 font-mono text-xl font-semibold">{formatFare(offer.totalPriceMinor, offer.currency)}</p>
        </div>
        <p className="max-w-40 text-right text-xs text-muted-foreground">报价来源：{offer.provider}</p>
      </div>
    </article>
  )
}

function LegSummary({ label, leg }: { label: string; leg?: FlightOffer['legs'][number] }) {
  if (!leg) return null
  return (
    <div className="min-w-0 rounded-md border bg-muted/20 p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground"><CalendarDays aria-hidden="true" />{label} · {leg.flightNumber}</div>
      <div className="mt-2 flex items-center gap-2 text-sm font-medium">
        <span>{new Date(leg.departureAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
        <span className="text-muted-foreground">{leg.origin}</span>
        <span className="h-px flex-1 bg-border" />
        <span className="text-muted-foreground">{leg.destination}</span>
        <span>{new Date(leg.arrivalAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {formatDateTime(leg.departureAt)} 出发 · {Math.floor(leg.durationMinutes / 60)}h {leg.durationMinutes % 60}m · {leg.stops === 0 ? '直飞' : `${leg.stops} 次中转`}
      </p>
    </div>
  )
}
