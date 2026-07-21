import { apiRequest } from '@/api/client'
import type { DataMode, PriceStatus, TripType } from '@/api/fares'

export type ComparisonTrendDays = 7 | 30 | 90

export interface ComparisonView {
  id: string
  name: string
  currency: string
  trendDays: ComparisonTrendDays
  version: number
  configuredRouteCount: number
  activeRouteCount: number
  missingSubscriptionCount: number
  comparable: boolean
  subscriptionIds: string[]
  createdAt: string
  updatedAt: string
}

export interface ComparisonTrendPoint {
  observedAt: string
  priceMinor: number
  lowestPriceMinor: number | null
  highestPriceMinor: number | null
  averagePriceMinor: number | null
  sampleCount: number
  directVerified?: boolean
}

export interface ComparisonSnapshotRoute {
  subscriptionId: string
  name: string
  enabled: boolean
  origin: string
  destination: string
  originName: string
  destinationName: string
  tripType: TripType
  departureDate: string
  returnDate: string | null
  directOnly: boolean
  currency: string
  latestDetailedPriceMinor: number | null
  detailedPriceStatus: PriceStatus
  detailedObservedAt: string | null
  periodMinPriceMinor: number | null
  periodMaxPriceMinor: number | null
  periodAveragePriceMinor: number | null
  periodSampleCount: number
  changePercent: number | null
  detailedTrend: ComparisonTrendPoint[]
  latestCalendarPriceMinor: number | null
  calendarLowestPriceMinor: number | null
  calendarTotalPriceMinor: number | null
  calendarPriceBasis: 'one_way_lowest' | 'round_trip_total' | null
  calendarObservedAt: string | null
  calendarDirectVerified: boolean
  calendarTrend: ComparisonTrendPoint[]
}

export interface ComparisonSnapshot {
  meta: {
    mode: DataMode
    generatedAt: string
  }
  view: ComparisonView
  routes: ComparisonSnapshotRoute[]
}

export interface ComparisonViewPage {
  items: ComparisonView[]
  nextCursor: string | null
  asOf: string
}

export interface ComparisonSubscriptionOption {
  id: string
  name: string
  enabled: boolean
  currency: string
  origin: string
  destination: string
  tripType: TripType
  departureDate: string
  returnDate: string | null
  directOnly: boolean
  missing?: boolean
}

export interface ComparisonSubscriptionOptionPage {
  items: ComparisonSubscriptionOption[]
  nextCursor: string | null
  asOf: string
}

export interface CreateComparisonViewInput {
  name: string
  trendDays: ComparisonTrendDays
  subscriptionIds: string[]
  idempotencyKey: string
}

export interface UpdateComparisonViewInput {
  name: string
  trendDays: ComparisonTrendDays
  subscriptionIds: string[]
  expectedVersion: number
}

interface BackendSubscription {
  id: string
  name: string
  enabled: boolean
  currency: string
  trip_type: 'one_way' | 'round_trip'
  legs: Array<{
    position: number
    origin: string
    destination: string
    departure_date: string
  }>
  filters: {
    direct_only: boolean
  }
}

interface BackendSubscriptionPage {
  items: BackendSubscription[]
  next_cursor: string | null
  as_of: string
}

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return params.toString()
}

function mapSubscriptionOption(item: BackendSubscription): ComparisonSubscriptionOption {
  const legs = [...item.legs].sort((left, right) => left.position - right.position)
  return {
    id: item.id,
    name: item.name,
    enabled: item.enabled,
    currency: item.currency,
    origin: legs[0]?.origin || '',
    destination: legs[0]?.destination || '',
    tripType: item.trip_type === 'round_trip' ? 'roundtrip' : 'oneway',
    departureDate: legs[0]?.departure_date || '',
    returnDate: legs[1]?.departure_date || null,
    directOnly: item.filters.direct_only,
  }
}

export const comparisonQueryKeys = {
  all: ['comparisons'] as const,
  views: () => [...comparisonQueryKeys.all, 'views'] as const,
  snapshot: (viewId: string) => [...comparisonQueryKeys.all, 'snapshot', viewId] as const,
  subscriptionOptions: () => [...comparisonQueryKeys.all, 'subscription-options'] as const,
}

export async function getComparisonViews(cursor?: string): Promise<ComparisonViewPage> {
  return apiRequest<ComparisonViewPage>(`/comparisons?${queryString({ cursor })}`)
}

export async function createComparisonView(input: CreateComparisonViewInput): Promise<ComparisonView> {
  return apiRequest<ComparisonView>('/comparisons', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateComparisonView(
  viewId: string,
  input: UpdateComparisonViewInput,
): Promise<ComparisonView> {
  return apiRequest<ComparisonView>(`/comparisons/${viewId}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function deleteComparisonView(viewId: string): Promise<void> {
  await apiRequest(`/comparisons/${viewId}`, { method: 'DELETE' })
}

export async function getComparisonSnapshot(viewId: string): Promise<ComparisonSnapshot> {
  return apiRequest<ComparisonSnapshot>(`/comparisons/${viewId}/snapshot`)
}

export async function getComparisonSubscriptionOptions(
  cursor?: string,
): Promise<ComparisonSubscriptionOptionPage> {
  const response = await apiRequest<BackendSubscriptionPage>(
    `/subscriptions?${queryString({ limit: 100, cursor })}`,
  )
  return {
    items: response.items.map(mapSubscriptionOption),
    nextCursor: response.next_cursor,
    asOf: response.as_of,
  }
}
