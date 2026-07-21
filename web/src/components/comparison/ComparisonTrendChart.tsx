import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'

import type { ComparisonSnapshotRoute } from '@/api/comparisons'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import { EmptyState } from '@/components/ui/empty-state'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatCurrency } from '@/lib/formatters'
import {
  alignComparisonTrend,
  preferredComparisonTrack,
  type ComparisonTrack,
} from '@/lib/comparison'
import { cn } from '@/lib/utils'

const COLORS = [
  'hsl(var(--chart-1))',
  'hsl(var(--chart-2))',
  'hsl(var(--chart-3))',
  'hsl(var(--chart-4))',
  'hsl(var(--chart-5))',
]
const DASHES = [undefined, undefined, undefined, undefined, undefined, '8 3', '3 3', '10 3 2 3']

interface ComparisonTrendChartProps {
  routes: ComparisonSnapshotRoute[]
  currency: string
}

export function ComparisonTrendChart({ routes, currency }: ComparisonTrendChartProps) {
  const [track, setTrack] = useState<ComparisonTrack>(() => preferredComparisonTrack(routes))
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(() => new Set())
  const hasDetailed = routes.some((route) => route.detailedTrend.length > 0)
  const hasCalendar = routes.some((route) => route.calendarTrend.length > 0)

  useEffect(() => {
    if (track === 'detailed' && !hasDetailed && hasCalendar) setTrack('calendar')
    if (track === 'calendar' && !hasCalendar && hasDetailed) setTrack('detailed')
  }, [hasCalendar, hasDetailed, track])

  const chartData = useMemo(() => alignComparisonTrend(routes, track), [routes, track])
  const chartConfig = useMemo<ChartConfig>(() => Object.fromEntries(routes.map((route, index) => [
    route.subscriptionId,
    { label: route.name, color: COLORS[index % COLORS.length] },
  ])), [routes])
  const routeById = useMemo(
    () => new Map(routes.map((route) => [route.subscriptionId, route])),
    [routes],
  )

  const toggleRoute = (subscriptionId: string) => {
    setHiddenIds((current) => {
      const next = new Set(current)
      if (next.has(subscriptionId)) next.delete(subscriptionId)
      else next.add(subscriptionId)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <Tabs value={track} onValueChange={(value) => setTrack(value as ComparisonTrack)}>
          <TabsList aria-label="价格数据轨">
            <TabsTrigger value="detailed" disabled={!hasDetailed}>详细报价</TabsTrigger>
            <TabsTrigger value="calendar" disabled={!hasCalendar}>订阅日期日历价</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex flex-wrap items-center gap-2" aria-label="航线图例">
          {routes.map((route, index) => {
            const hidden = hiddenIds.has(route.subscriptionId)
            return (
              <button
                key={route.subscriptionId}
                type="button"
                aria-pressed={!hidden}
                onClick={() => toggleRoute(route.subscriptionId)}
                className={cn(
                  'flex min-h-8 items-center gap-2 rounded-md border px-2.5 py-1 text-xs transition-colors',
                  hidden ? 'text-muted-foreground opacity-60' : 'bg-background text-foreground',
                )}
              >
                <span
                  className="h-0 w-5 border-t-2"
                  style={{
                    borderColor: COLORS[index % COLORS.length],
                    borderTopStyle: index >= 5 ? 'dashed' : 'solid',
                  }}
                  aria-hidden="true"
                />
                <span className="max-w-36 truncate">{route.name}</span>
              </button>
            )
          })}
        </div>
      </div>

      {track === 'calendar' ? (
        <p className="text-xs leading-5 text-muted-foreground">
          “订阅日期日历价”与详细报价分开显示；往返只使用真实总价。未验证直飞的日历点不构成直飞证据。
        </p>
      ) : null}

      {chartData.length ? (
        <ChartContainer config={chartConfig} className="h-[320px] min-h-[320px] w-full sm:h-[380px]">
          <LineChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="observedAt"
              axisLine={false}
              tickLine={false}
              tickMargin={8}
              tickFormatter={(value) => new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(`${value}T00:00:00Z`))}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              width={68}
              tickFormatter={(value) => formatCurrency(Number(value) / 100, currency).replace(/\.00$/, '')}
              domain={['auto', 'auto']}
            />
            <Tooltip
              labelFormatter={(value) => new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date(`${value}T00:00:00Z`))}
              formatter={(value, name) => [
                formatCurrency(Number(value) / 100, currency),
                routeById.get(String(name))?.name || String(name),
              ]}
            />
            {routes.map((route, index) => hiddenIds.has(route.subscriptionId) ? null : (
              <Line
                key={route.subscriptionId}
                type="monotone"
                dataKey={route.subscriptionId}
                stroke={`var(--color-${route.subscriptionId})`}
                strokeWidth={2}
                strokeDasharray={DASHES[index]}
                dot={false}
                activeDot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ChartContainer>
      ) : (
        <EmptyState title="当前数据轨暂无趋势" description="完成至少一次对应类型的价格采集后，趋势会显示在这里。" />
      )}
    </div>
  )
}
