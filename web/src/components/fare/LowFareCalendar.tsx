import { useMemo } from 'react'
import { CheckCircle2 } from 'lucide-react'

import type { CalendarPricePoint } from '@/api/fares'
import { Badge } from '@/components/ui/badge'
import { EmptyState } from '@/components/ui/empty-state'
import { formatCurrency } from '@/lib/formatters'

interface LowFareCalendarProps {
  points: CalendarPricePoint[]
}

function priceFor(point: CalendarPricePoint): number {
  return point.totalPriceMinor ?? point.lowestPriceMinor
}

export function LowFareCalendar({ points }: LowFareCalendarProps) {
  const hasRoundTripPrices = points.some((point) => point.returnDate !== null)
  const months = useMemo(() => {
    const byDate = new Map<string, CalendarPricePoint>()
    points.forEach((point) => {
      const current = byDate.get(point.departureDate)
      if (!current || priceFor(point) < priceFor(current)) byDate.set(point.departureDate, point)
    })
    const grouped = new Map<string, CalendarPricePoint[]>()
    Array.from(byDate.values()).forEach((point) => {
      const month = point.departureDate.slice(0, 7)
      grouped.set(month, [...(grouped.get(month) || []), point])
    })
    return Array.from(grouped.entries()).sort(([left], [right]) => left.localeCompare(right))
  }, [points])

  if (!months.length) {
    return <EmptyState title="暂无低价日历" description="当前查询还没有保存按日期聚合的价格观测。" />
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="secondary">{hasRoundTripPrices ? '按出发日最低往返总价' : '按出发日最低价'}</Badge>
        <span>价格来自已完成采集；空白日期代表当前没有观测，不代表无航班。</span>
      </div>
      {months.map(([month, monthPoints]) => <CalendarMonth key={month} month={month} points={monthPoints} />)}
    </div>
  )
}

function CalendarMonth({ month, points }: { month: string; points: CalendarPricePoint[] }) {
  const [year, monthNumber] = month.split('-').map(Number)
  const firstDay = new Date(year, monthNumber - 1, 1)
  const daysInMonth = new Date(year, monthNumber, 0).getDate()
  const offset = (firstDay.getDay() + 6) % 7
  const pointByDay = new Map(points.map((point) => [Number(point.departureDate.slice(8, 10)), point]))
  const cells = Array.from({ length: offset + daysInMonth }, (_, index) => {
    if (index < offset) return null
    return pointByDay.get(index - offset + 1) || null
  })

  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{year} 年 {monthNumber} 月</h3>
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-md border bg-border">
        {['一', '二', '三', '四', '五', '六', '日'].map((day) => <div key={day} className="bg-muted px-2 py-2 text-center text-xs font-medium text-muted-foreground">{day}</div>)}
        {cells.map((point, index) => {
          const day = index - offset + 1
          return (
            <div key={`${month}-${index}`} className="flex min-h-20 flex-col gap-1 bg-background p-2 text-left sm:min-h-24">
              {day > 0 ? <span className="text-xs text-muted-foreground">{day}</span> : null}
              {point ? (
                <>
                  <span className="font-mono text-xs font-semibold sm:text-sm">{formatCurrency(priceFor(point) / 100, point.currency)}</span>
                  {point.directVerified ? <span className="flex items-center gap-1 text-[10px] text-muted-foreground" title="直飞字段已核验"><CheckCircle2 aria-hidden="true" />直飞</span> : null}
                </>
              ) : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}
