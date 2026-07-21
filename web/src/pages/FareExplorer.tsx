import { useMemo, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import {
  ArrowRight,
  CheckCircle2,
  CircleStop,
  Clock3,
  Plane,
  RefreshCw,
  SearchX,
  ShieldAlert,
} from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import {
  fareQueryKeys,
  searchFlights,
  type CollectionStateStatus,
  type FlightLeg,
  type FlightOffer,
  type FlightSearchParams,
  type FlightSegment,
} from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { FlightSearchForm } from '@/components/fare/FlightSearchForm'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageSurface } from '@/components/layout/PageScaffold'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ScrollArea } from '@/components/ui/scroll-area'
import { readableLocation } from '@/lib/airport-directory'
import {
  calculateLayoverMinutes,
  formatDurationMinutes,
  localDateTimeParts,
  terminalChangeLabel,
} from '@/lib/flight-itinerary'
import { formatCurrency, formatDateTime } from '@/lib/formatters'
import { defaultFlightSearchValues } from '@/lib/flight-search'
import { cn } from '@/lib/utils'

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
        <AlertDescription>下面是本次已保存的真实报价，价格旁边会显示实际观测时间。</AlertDescription>
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
      description="按城市或机场搜索单程、往返和直飞报价，并查看每一段航班与中转信息。"
      width="7xl"
      actions={(
        <Button type="button" variant="outline" onClick={() => result.refetch()} disabled={!submittedQuery || result.isFetching}>
          <RefreshCw data-icon="inline-start" className={result.isFetching ? 'animate-spin' : undefined} />
          刷新结果
        </Button>
      )}
    >
      <div className="flex flex-col gap-6">
        <PageSurface title="查询条件" description="输入中文城市或机场名称即可搜索，页面打开时不会自动触发采集。">
          <FlightSearchForm key={JSON.stringify(initialQuery)} initialValues={initialQuery} submitting={result.isFetching} onSubmit={submit} />
        </PageSurface>

        {!submittedQuery && !result.data ? (
          <PageSurface>
            <EmptyState icon={<SearchX />} title="等待查询" description="选择出发地、目的地和日期后，这里会显示采集状态、价格、机场和完整航段。" />
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
                    title={`${readableLocation(firstResult.query.origin)} → ${readableLocation(firstResult.query.destination)}`}
                    description={`${firstResult.query.tripType === 'roundtrip' ? '往返' : '单程'} · ${firstResult.query.departureDate}${firstResult.query.returnDate ? ` 至 ${firstResult.query.returnDate}` : ''} · 共 ${firstResult.total} 个报价，已加载 ${offers.length} 个`}
                    actions={<Badge variant="secondary">价格从低到高</Badge>}
                    bodyClassName="p-0"
                  >
                    {offers.length ? (
                      <ScrollArea className="h-[72vh] max-h-[760px] min-h-[420px]">
                        <div className="flex flex-col gap-3 p-3 sm:p-4">
                          {offers.map((offer) => <OfferCard key={offer.id} offer={offer} />)}
                          {result.hasNextPage ? (
                            <div className="flex justify-center py-2">
                              <Button type="button" variant="outline" onClick={() => result.fetchNextPage()} disabled={result.isFetchingNextPage}>
                                {result.isFetchingNextPage ? '加载中…' : '加载更多报价'}
                              </Button>
                            </div>
                          ) : (
                            <p className="py-2 text-center text-xs text-muted-foreground">已经加载全部符合条件的报价</p>
                          )}
                        </div>
                      </ScrollArea>
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

function normalizedSegments(leg: FlightLeg): FlightSegment[] {
  if (Array.isArray(leg.segments) && leg.segments.length) return leg.segments
  return [{
    position: 0,
    flightNumber: leg.flightNumber,
    operatingFlightNumber: null,
    airline: leg.airline,
    airlineName: null,
    origin: leg.origin,
    originName: leg.originName || readableLocation(leg.origin),
    originTerminal: null,
    destination: leg.destination,
    destinationName: leg.destinationName || readableLocation(leg.destination),
    destinationTerminal: null,
    departureAt: leg.departureAt,
    arrivalAt: leg.arrivalAt,
    departureLocal: leg.departureAt.replace(/Z$/, ''),
    arrivalLocal: leg.arrivalAt.replace(/Z$/, ''),
    departureTimezone: 'UTC',
    arrivalTimezone: 'UTC',
    durationMinutes: leg.durationMinutes,
    technicalStopCount: leg.stops,
    aircraftCode: null,
  }]
}

function offerLabel(offer: FlightOffer): string {
  const flightNumbers = offer.legs
    .flatMap((leg) => normalizedSegments(leg).map((segment) => segment.flightNumber))
  const unique = [...new Set(flightNumbers)]
  if (!unique.length) return '航班行程'
  return unique.length > 2 ? `${unique.slice(0, 2).join(' / ')} 等` : unique.join(' / ')
}

function providerLabel(provider: string): string {
  return provider.toLowerCase() === 'ctrip' ? '携程' : provider
}

function OfferCard({ offer }: { offer: FlightOffer }) {
  const direct = offer.legs.every((leg) => leg.stops === 0)
  const allSegments = offer.legs.flatMap(normalizedSegments)
  const firstSegment = allSegments[0]
  const lastSegment = allSegments[allSegments.length - 1]
  const routeLabel = firstSegment && lastSegment
    ? `${firstSegment.originName} → ${lastSegment.destinationName}`
    : '航班行程'

  return (
    <Card>
      <CardHeader className="gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary"><Plane data-icon="inline-start" />{offerLabel(offer)}</Badge>
              <Badge variant="outline">
                {direct ? '直飞' : <><CircleStop data-icon="inline-start" />含中转</>}
              </Badge>
              <Badge variant="outline">{offer.cabin}</Badge>
            </div>
            <CardTitle className="text-base leading-6">{routeLabel}</CardTitle>
            <CardDescription>{offer.legs.length === 2 ? '完整往返总价' : '单程含税总价'} · {providerLabel(offer.provider)}</CardDescription>
          </div>
          <div className="shrink-0 sm:text-right">
            <p className="text-xs text-muted-foreground">{offer.legs.length === 2 ? '往返总价' : '每位成人'}</p>
            <p className="font-mono text-2xl font-semibold tracking-tight">{formatFare(offer.totalPriceMinor, offer.currency)}</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4 pb-4 sm:px-5 sm:pb-5">
        {offer.legs.map((leg) => (
          <FlightLegDetails key={leg.direction} leg={leg} />
        ))}
      </CardContent>
      <CardFooter className="justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground sm:px-5">
        <span>报价来源：{providerLabel(offer.provider)}</span>
        <span>采集于 {formatDateTime(offer.observedAt)}</span>
      </CardFooter>
    </Card>
  )
}

function FlightLegDetails({ leg }: { leg: FlightLeg }) {
  const segments = normalizedSegments(leg)
  const transferCount = Math.max(0, segments.length - 1)
  return (
    <section className="overflow-hidden rounded-lg border bg-muted/20" aria-label={leg.direction === 'outbound' ? '去程航段' : '返程航段'}>
      <div className="flex flex-col gap-1 border-b px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <Badge>{leg.direction === 'outbound' ? '去程' : '返程'}</Badge>
          <span className="text-sm font-medium">{leg.originName || leg.origin} → {leg.destinationName || leg.destination}</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {segments.length} 个航段 · {transferCount ? `${transferCount} 次换乘` : leg.stops ? `${leg.stops} 次经停` : '直飞'} · 全程 {formatDurationMinutes(leg.durationMinutes)}
        </span>
      </div>
      <div className="flex flex-col gap-2 p-3">
        {segments.map((segment, index) => {
          const next = segments[index + 1]
          return (
            <div key={`${segment.position}-${segment.flightNumber}`} className="flex flex-col gap-2">
              <FlightSegmentDetails segment={segment} />
              {next ? <TransferDetails current={segment} next={next} /> : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function FlightSegmentDetails({ segment }: { segment: FlightSegment }) {
  const departure = localDateTimeParts(segment.departureLocal)
  const arrival = localDateTimeParts(segment.arrivalLocal)
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{segment.airlineName || segment.airline} · {segment.flightNumber}</Badge>
          {segment.operatingFlightNumber && segment.operatingFlightNumber !== segment.flightNumber ? (
            <span className="text-xs text-muted-foreground">实际承运 {segment.operatingFlightNumber}</span>
          ) : null}
        </div>
        <span className="text-xs text-muted-foreground">飞行 {formatDurationMinutes(segment.durationMinutes)}</span>
      </div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
        <AirportEndpoint
          date={departure.date}
          time={departure.time}
          name={segment.originName}
          code={segment.origin}
          terminal={segment.originTerminal}
        />
        <ArrowRight aria-hidden="true" className="text-muted-foreground" />
        <AirportEndpoint
          align="right"
          date={arrival.date}
          time={arrival.time}
          name={segment.destinationName}
          code={segment.destination}
          terminal={segment.destinationTerminal}
        />
      </div>
      {segment.technicalStopCount > 0 ? (
        <Alert className="mt-3">
          <CircleStop data-icon="inline-start" />
          <AlertTitle>本航段经停 {segment.technicalStopCount} 次</AlertTitle>
          <AlertDescription>供应商没有提供经停机场名称，因此不会猜测中途机场。</AlertDescription>
        </Alert>
      ) : null}
    </div>
  )
}

function AirportEndpoint({
  date,
  time,
  name,
  code,
  terminal,
  align = 'left',
}: {
  date: string
  time: string
  name: string
  code: string
  terminal: string | null
  align?: 'left' | 'right'
}) {
  return (
    <div className={cn('min-w-0', align === 'right' && 'text-right')}>
      <p className="font-mono text-xl font-semibold tabular-nums">{time}</p>
      <p className="text-xs text-muted-foreground">{date} · 当地时间</p>
      <p className="mt-2 text-sm font-medium leading-5">{name || code}</p>
      <div className={cn('mt-1 flex flex-wrap gap-1.5', align === 'right' && 'justify-end')}>
        <Badge variant="outline">{code}</Badge>
        {terminal ? <Badge variant="outline">{terminal}</Badge> : null}
      </div>
    </div>
  )
}

function TransferDetails({ current, next }: { current: FlightSegment; next: FlightSegment }) {
  const minutes = calculateLayoverMinutes(current, next)
  const terminal = terminalChangeLabel(current, next)
  return (
    <Alert>
      <Clock3 data-icon="inline-start" />
      <AlertTitle>在 {current.destinationName || next.originName}（{current.destination}）中转</AlertTitle>
      <AlertDescription>
        {minutes === null ? '停留时间未知' : `停留 ${formatDurationMinutes(minutes)}`}
        {terminal ? ` · ${terminal}` : ''}
      </AlertDescription>
    </Alert>
  )
}
