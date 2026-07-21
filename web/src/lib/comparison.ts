import type {
  ComparisonSnapshotRoute,
  ComparisonSubscriptionOption,
} from '@/api/comparisons'

export type ComparisonTrack = 'detailed' | 'calendar'
export type ComparisonSort =
  | 'position'
  | 'detailed-low'
  | 'detailed-high'
  | 'calendar-low'
  | 'calendar-high'
  | 'period-low'
  | 'change-low'
  | 'change-high'
  | 'freshness'

export interface ComparisonChartPoint {
  observedAt: string
  [subscriptionId: string]: string | number | null
}

export function validateComparisonSelection(
  subscriptionIds: string[],
  options: ComparisonSubscriptionOption[],
): string | null {
  const uniqueIds = [...new Set(subscriptionIds)]
  if (uniqueIds.length < 2) return '至少选择 2 条订阅航线'
  if (uniqueIds.length > 8) return '最多选择 8 条订阅航线'

  const optionById = new Map(options.map((option) => [option.id, option]))
  const selected = uniqueIds.map((id) => optionById.get(id))
  if (selected.some((option) => !option)) return '部分订阅尚未加载，请加载更多后重试'
  if (selected.some((option) => option?.missing)) return '请移除已经删除的订阅后再保存'
  if (new Set(selected.map((option) => option?.currency)).size > 1) {
    return '同一个对比视图只能选择相同币种的订阅'
  }
  return null
}

function nullableCompare(
  left: number | null,
  right: number | null,
  direction: 'asc' | 'desc',
): number {
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  return direction === 'asc' ? left - right : right - left
}

function freshnessRank(route: ComparisonSnapshotRoute): number {
  if (route.detailedPriceStatus === 'current') return 0
  if (route.detailedPriceStatus === 'stale') return 1
  return 2
}

export function sortComparisonRoutes(
  routes: ComparisonSnapshotRoute[],
  sort: ComparisonSort,
): ComparisonSnapshotRoute[] {
  const indexed = routes.map((route, index) => ({ route, index }))
  indexed.sort((left, right) => {
    let result = 0
    if (sort === 'position') result = 0
    if (sort === 'detailed-low') result = nullableCompare(left.route.latestDetailedPriceMinor, right.route.latestDetailedPriceMinor, 'asc')
    if (sort === 'detailed-high') result = nullableCompare(left.route.latestDetailedPriceMinor, right.route.latestDetailedPriceMinor, 'desc')
    if (sort === 'calendar-low') result = nullableCompare(left.route.latestCalendarPriceMinor, right.route.latestCalendarPriceMinor, 'asc')
    if (sort === 'calendar-high') result = nullableCompare(left.route.latestCalendarPriceMinor, right.route.latestCalendarPriceMinor, 'desc')
    if (sort === 'period-low') result = nullableCompare(left.route.periodMinPriceMinor, right.route.periodMinPriceMinor, 'asc')
    if (sort === 'change-low') result = nullableCompare(left.route.changePercent, right.route.changePercent, 'asc')
    if (sort === 'change-high') result = nullableCompare(left.route.changePercent, right.route.changePercent, 'desc')
    if (sort === 'freshness') result = freshnessRank(left.route) - freshnessRank(right.route)
    return result || left.index - right.index
  })
  return indexed.map(({ route }) => route)
}

function utcDay(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString().slice(0, 10)
}

export function alignComparisonTrend(
  routes: ComparisonSnapshotRoute[],
  track: ComparisonTrack,
): ComparisonChartPoint[] {
  const rows = new Map<string, ComparisonChartPoint>()
  for (const route of routes) {
    const points = track === 'detailed' ? route.detailedTrend : route.calendarTrend
    for (const point of points) {
      const day = utcDay(point.observedAt)
      if (!rows.has(day)) {
        const row: ComparisonChartPoint = { observedAt: day }
        for (const candidate of routes) row[candidate.subscriptionId] = null
        rows.set(day, row)
      }
      rows.get(day)![route.subscriptionId] = point.priceMinor
    }
  }
  return [...rows.values()].sort((left, right) => left.observedAt.localeCompare(right.observedAt))
}

export function preferredComparisonTrack(routes: ComparisonSnapshotRoute[]): ComparisonTrack {
  const hasDetailed = routes.some((route) => route.detailedTrend.length > 0)
  const hasCalendar = routes.some((route) => route.calendarTrend.length > 0)
  return !hasDetailed && hasCalendar ? 'calendar' : 'detailed'
}
