import { useMemo } from 'react'

import type { CalendarPricePoint } from '@/api/fares'
import { Badge } from '@/components/ui/badge'
import { EmptyState } from '@/components/ui/empty-state'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatCurrency } from '@/lib/formatters'

interface RoundTripMatrixProps {
  points: CalendarPricePoint[]
}

export function RoundTripMatrix({ points }: RoundTripMatrixProps) {
  const matrix = useMemo(() => {
    const rows = new Map<string, Map<string, CalendarPricePoint>>()
    points.filter((point) => point.returnDate).forEach((point) => {
      const returnDate = point.returnDate as string
      const row = rows.get(point.departureDate) || new Map<string, CalendarPricePoint>()
      const current = row.get(returnDate)
      const pointPrice = point.totalPriceMinor ?? point.lowestPriceMinor
      const currentPrice = current ? current.totalPriceMinor ?? current.lowestPriceMinor : Number.POSITIVE_INFINITY
      if (!current || pointPrice < currentPrice) row.set(returnDate, point)
      rows.set(point.departureDate, row)
    })
    const departureDates = Array.from(rows.keys()).sort().slice(0, 12)
    const returnDates = Array.from(new Set(departureDates.flatMap((date) => Array.from(rows.get(date)?.keys() || [])))).sort().slice(0, 12)
    return { rows, departureDates, returnDates }
  }, [points])

  const minimum = useMemo(() => {
    const values = matrix.departureDates.flatMap((departure) => matrix.returnDates.map((returnDate) => {
      const point = matrix.rows.get(departure)?.get(returnDate)
      return point ? point.totalPriceMinor ?? point.lowestPriceMinor : Number.POSITIVE_INFINITY
    }))
    return Math.min(...values)
  }, [matrix])

  if (!matrix.departureDates.length || !matrix.returnDates.length) {
    return <EmptyState title="暂无往返价格矩阵" description="当前查询还没有保存出发日与返程日组合的总价观测。" />
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="secondary">含税往返总价</Badge>
        <span>横向为返程日，纵向为出发日；当前页最多展示 12 × 12 个已观测组合。</span>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <Table className="min-w-max">
          <TableHeader>
            <TableRow>
              <TableHead className="sticky left-0 bg-background">出发 \ 返程</TableHead>
              {matrix.returnDates.map((date) => <TableHead key={date} className="min-w-24 text-center">{date.slice(5)}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {matrix.departureDates.map((departureDate) => (
              <TableRow key={departureDate}>
                <TableHead className="sticky left-0 bg-background font-medium">{departureDate.slice(5)}</TableHead>
                {matrix.returnDates.map((returnDate) => {
                  const point = matrix.rows.get(departureDate)?.get(returnDate)
                  const price = point ? point.totalPriceMinor ?? point.lowestPriceMinor : null
                  return (
                    <TableCell key={returnDate} className={price === minimum ? 'bg-primary/10 text-center font-semibold' : 'text-center'}>
                      {price === null ? <span className="text-muted-foreground">-</span> : formatCurrency(price / 100, point?.currency)}
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
