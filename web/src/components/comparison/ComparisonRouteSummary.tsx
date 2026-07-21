import { ChartNoAxesCombined } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import type { ComparisonSnapshotRoute } from '@/api/comparisons'
import { PriceFreshnessBadge } from '@/components/fare/PriceFreshnessBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatCurrency, formatDateTime, formatPercent } from '@/lib/formatters'

function fare(value: number | null, currency: string): string {
  return value === null ? '暂无报价' : formatCurrency(value / 100, currency)
}

function dates(route: ComparisonSnapshotRoute): string {
  return route.returnDate ? `${route.departureDate} 至 ${route.returnDate}` : route.departureDate
}

function airportLabel(name: string, code: string): string {
  return name && name !== code ? `${name} (${code})` : code
}

function CalendarEvidence({ route }: { route: ComparisonSnapshotRoute }) {
  if (!route.calendarObservedAt) return <Badge variant="outline">暂无日历证据</Badge>
  if (route.calendarDirectVerified) return <Badge variant="secondary">直飞已验证</Badge>
  return <Badge variant="outline">不构成直飞证据</Badge>
}

function HistoryButton({ route }: { route: ComparisonSnapshotRoute }) {
  const navigate = useNavigate()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`查看 ${route.name} 价格历史`}
          onClick={() => navigate(`/history?route=${route.subscriptionId}`)}
        >
          <ChartNoAxesCombined aria-hidden="true" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>查看价格历史</TooltipContent>
    </Tooltip>
  )
}

export function ComparisonRouteSummary({ routes }: { routes: ComparisonSnapshotRoute[] }) {
  return (
    <>
      <div className="hidden lg:block">
        <Table containerClassName="max-h-none">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[22%]">航线</TableHead>
              <TableHead className="w-[16%]">行程</TableHead>
              <TableHead className="w-[18%]">详细报价</TableHead>
              <TableHead className="w-[18%]">订阅日期日历价</TableHead>
              <TableHead className="w-[18%]">详细报价周期统计</TableHead>
              <TableHead className="w-[8%] text-right">历史</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {routes.map((route) => (
              <TableRow key={route.subscriptionId}>
                <TableCell className="whitespace-normal align-top">
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{route.name}</span>
                      {!route.enabled ? <Badge variant="outline">已暂停</Badge> : null}
                    </div>
                    <span className="text-xs text-muted-foreground">{airportLabel(route.originName, route.origin)} → {airportLabel(route.destinationName, route.destination)}</span>
                  </div>
                </TableCell>
                <TableCell className="whitespace-normal align-top text-xs">
                  <div className="flex flex-col gap-1">
                    <span>{route.tripType === 'roundtrip' ? '往返' : '单程'} · {route.directOnly ? '仅直飞' : '不限中转'}</span>
                    <span className="text-muted-foreground">{dates(route)}</span>
                  </div>
                </TableCell>
                <TableCell className="whitespace-normal align-top">
                  <div className="flex flex-col gap-1.5">
                    <span className="font-mono font-medium">{fare(route.latestDetailedPriceMinor, route.currency)}</span>
                    <PriceFreshnessBadge status={route.detailedPriceStatus} />
                    <span className="text-xs text-muted-foreground">{route.detailedObservedAt ? formatDateTime(route.detailedObservedAt) : '尚未观测'}</span>
                  </div>
                </TableCell>
                <TableCell className="whitespace-normal align-top">
                  <div className="flex flex-col gap-1.5">
                    <span className="font-mono font-medium">{fare(route.latestCalendarPriceMinor, route.currency)}</span>
                    <span className="text-xs text-muted-foreground">{route.tripType === 'roundtrip' ? '往返总价' : '单程最低价'}</span>
                    <CalendarEvidence route={route} />
                    <span className="text-xs text-muted-foreground">{route.calendarObservedAt ? formatDateTime(route.calendarObservedAt) : '尚未观测'}</span>
                  </div>
                </TableCell>
                <TableCell className="whitespace-normal align-top text-xs">
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                    <span className="text-muted-foreground">最低</span><span className="font-mono">{fare(route.periodMinPriceMinor, route.currency)}</span>
                    <span className="text-muted-foreground">最高</span><span className="font-mono">{fare(route.periodMaxPriceMinor, route.currency)}</span>
                    <span className="text-muted-foreground">平均</span><span className="font-mono">{fare(route.periodAveragePriceMinor, route.currency)}</span>
                    <span className="text-muted-foreground">变化</span><span>{route.changePercent === null ? '暂无' : formatPercent(route.changePercent)}</span>
                    <span className="text-muted-foreground">样本</span><span>{route.periodSampleCount}</span>
                  </div>
                </TableCell>
                <TableCell className="text-right align-top"><HistoryButton route={route} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="divide-y lg:hidden">
        {routes.map((route) => (
          <article key={route.subscriptionId} className="flex flex-col gap-4 px-4 py-5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-sm font-medium">{route.name}</h3>
                  {!route.enabled ? <Badge variant="outline">已暂停</Badge> : null}
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{route.origin} → {route.destination} · {route.tripType === 'roundtrip' ? '往返' : '单程'} · {route.directOnly ? '仅直飞' : '不限中转'}</p>
                <p className="text-xs leading-5 text-muted-foreground">{dates(route)}</p>
              </div>
              <HistoryButton route={route} />
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">详细报价</p>
                <p className="mt-1 truncate font-mono font-medium">{fare(route.latestDetailedPriceMinor, route.currency)}</p>
                <div className="mt-1"><PriceFreshnessBadge status={route.detailedPriceStatus} /></div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {route.detailedObservedAt ? formatDateTime(route.detailedObservedAt) : '尚未观测'}
                </p>
              </div>
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">订阅日期日历价</p>
                <p className="mt-1 truncate font-mono font-medium">{fare(route.latestCalendarPriceMinor, route.currency)}</p>
                <div className="mt-1"><CalendarEvidence route={route} /></div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {route.calendarObservedAt ? formatDateTime(route.calendarObservedAt) : '尚未观测'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">详细报价周期最低 / 平均</p>
                <p className="mt-1 font-mono text-xs">{fare(route.periodMinPriceMinor, route.currency)} / {fare(route.periodAveragePriceMinor, route.currency)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">详细报价周期变化 / 样本</p>
                <p className="mt-1 text-xs">{route.changePercent === null ? '暂无' : formatPercent(route.changePercent)} / {route.periodSampleCount}</p>
              </div>
            </div>
          </article>
        ))}
      </div>
    </>
  )
}
