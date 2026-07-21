import { apiRequest } from '@/api/client'

export type TripType = 'oneway' | 'roundtrip'
export type DataMode = 'live' | 'demo'
export type PriceStatus = 'current' | 'stale' | 'unavailable'

export interface ResponseMeta {
  mode: DataMode
  generatedAt: string
}

export interface PricePoint {
  observedAt: string
  priceMinor: number
  lowestPriceMinor?: number | null
  highestPriceMinor?: number | null
  averagePriceMinor?: number | null
  sampleCount?: number
}

export interface WatchedRoute {
  id: string
  origin: string
  destination: string
  originName: string
  destinationName: string
  tripType: TripType
  directOnly: boolean
  latestPriceMinor: number | null
  priceStatus: PriceStatus
  changePercent: number | null
  observedAt: string | null
}

export interface DashboardOverview {
  meta: ResponseMeta
  stats: {
    lowestPriceMinor: number | null
    priceChangePercent: number | null
    activeSubscriptions: number
    routesTracked: number
    collectionSuccessRate: number | null
  }
  trend: PricePoint[]
  routes: WatchedRoute[]
}

export interface FlightSearchParams {
  tripType: TripType
  origin: string
  destination: string
  departureDate: string
  returnDate?: string
  directOnly: boolean
  passengers: number
  airlineCodes?: string
  departureAirports?: string
  arrivalAirports?: string
  maxPriceMinor?: number
  maxStops?: number
  maxDurationMinutes?: number
  departureMinuteStart?: number
  departureMinuteEnd?: number
}

export interface FlightSegment {
  position: number
  flightNumber: string
  operatingFlightNumber: string | null
  airline: string
  airlineName: string | null
  origin: string
  originName: string
  originTerminal: string | null
  destination: string
  destinationName: string
  destinationTerminal: string | null
  departureAt: string
  arrivalAt: string
  departureLocal: string
  arrivalLocal: string
  departureTimezone: string
  arrivalTimezone: string
  durationMinutes: number
  technicalStopCount: number
  aircraftCode: string | null
}

export interface FlightLeg {
  direction: 'outbound' | 'inbound'
  flightNumber: string
  airline: string
  origin: string
  originName: string
  destination: string
  destinationName: string
  departureAt: string
  arrivalAt: string
  stops: number
  durationMinutes: number
  segments: FlightSegment[]
}

export interface FlightOffer {
  id: string
  totalPriceMinor: number
  currency: string
  cabin: string
  legs: FlightLeg[]
  provider: string
  observedAt: string
}

export interface FlightSearchResult {
  meta: ResponseMeta
  query: FlightSearchParams
  offers: FlightOffer[]
  total: number
  collection: CollectionState
  hasMore: boolean
  nextCursor: string | null
}

export type CollectionStateStatus =
  | 'pending'
  | 'leased'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'

export interface CollectionState {
  status: CollectionStateStatus
  runId: string | null
  scheduledAt: string | null
  finishedAt: string | null
  errorCode: string | null
}

export interface FareSubscription {
  id: string
  name: string
  origin: string
  destination: string
  tripType: TripType
  departureDate: string
  returnDate?: string
  directOnly: boolean
  currency: string
  maxPriceMinor: number | null
  targetPriceMinor: number | null
  targetCurrency: string | null
  latestPriceMinor: number | null
  priceStatus: PriceStatus
  latestObservedAt: string | null
  enabled: boolean
  updatedAt: string
  airlineCodes: string[]
  departureAirports: string[]
  arrivalAirports: string[]
  maxStops: number | null
  maxDurationMinutes: number | null
  nextDueAt: string | null
  lastCollectedAt: string | null
}

export interface SubscriptionList {
  meta: ResponseMeta
  items: FareSubscription[]
  nextCursor: string | null
}

interface BackendSubscription {
  id: string
  name: string
  enabled: boolean
  currency: string
  target_price_minor?: number | null
  target_currency?: string | null
  trip_type: 'one_way' | 'round_trip'
  legs: Array<{
    position: number
    origin: string
    destination: string
    departure_date: string
  }>
  filters: {
    direct_only: boolean
    airline_codes: string[]
    departure_airports: string[]
    arrival_airports: string[]
    max_price_minor: number | null
    max_stops: number | null
    max_duration_minutes: number | null
    departure_minute_start: number | null
    departure_minute_end: number | null
  }
  next_due_at: string | null
  last_collected_at: string | null
  updated_at: string
}

interface BackendSubscriptionPage {
  items: BackendSubscription[]
  next_cursor: string | null
  as_of: string
}

export interface CreateSubscriptionInput {
  name: string
  origin: string
  destination: string
  tripType: TripType
  departureDate: string
  returnDate?: string
  directOnly: boolean
  maxPriceMinor: number | null
  targetPriceMinor: number | null
  enabled: boolean
  passengers?: number
  airlineCodes?: string
  departureAirports?: string
  arrivalAirports?: string
  maxStops?: number
  maxDurationMinutes?: number
  departureMinuteStart?: number
  departureMinuteEnd?: number
}

export interface PriceHistoryResult {
  meta: ResponseMeta
  route: WatchedRoute | null
  points: PricePoint[]
  minPriceMinor: number | null
  maxPriceMinor: number | null
  averagePriceMinor: number | null
  sampleCount: number
  resolution: 'raw' | 'hour' | 'day'
  hasMore: boolean
  nextCursor: string | null
}

export type HistoryResolution = 'auto' | 'raw' | 'hour' | 'day'

export interface PriceHistoryOptions {
  days?: number
  resolution?: HistoryResolution
  limit?: number
  cursor?: string
}

export interface CalendarPricePoint {
  departureDate: string
  returnDate: string | null
  currency: string
  lowestPriceMinor: number
  totalPriceMinor: number | null
  observedAt: string
  directVerified: boolean
}

export interface CalendarPriceResult {
  meta: ResponseMeta
  route: WatchedRoute
  points: CalendarPricePoint[]
  hasMore: boolean
  nextCursor: string | null
}

export interface CalendarPriceOptions {
  departureStart?: string
  departureEnd?: string
  returnStart?: string
  returnEnd?: string
  limit?: number
  cursor?: string
}

export type CollectionRunStatus = 'success' | 'running' | 'failed' | 'blocked'

export interface CollectionDiagnostic {
  code: string
  message: string
  severity: 'warning' | 'error'
  path: string | null
  observedType: string | null
  retryable: boolean | null
}

export interface CollectionRun {
  id: string
  queryLabel: string
  provider: string
  status: CollectionRunStatus
  startedAt: string
  finishedAt: string | null
  observations: number
  calendarObservations: number
  itineraries: number
  offers: number
  attempt: number
  maxAttempts: number
  upstreamStatus: string | null
  warningCode: string | null
  schemaFingerprint: string | null
  diagnostics: CollectionDiagnostic[]
  durationMs: number | null
  errorCode: string | null
}

export interface CollectionRunList {
  meta: ResponseMeta
  items: CollectionRun[]
  health: {
    lastSuccessAt: string | null
    successRate24h: number | null
    nextScheduledAt: string | null
  }
  hasMore: boolean
  nextCursor: string | null
}

export interface CollectionOperations {
  meta: ResponseMeta
  runs: {
    ready: number
    retrying: number
    leased: number
    running: number
    failed24h: number
  }
  queues: {
    available: boolean
    collector: number | null
    default: number | null
    analysis: number | null
    notifications: number | null
  }
  schemas: Array<{
    provider: string
    endpoint: string
    schemaFingerprint: string
    topLevelFields: string[]
    firstSeenAt: string
    lastSeenAt: string
    occurrenceCount: number
    state: 'new' | 'current' | 'historical'
  }>
}

export type NotificationChannelType = 'email' | 'webhook' | 'telegram' | 'bark' | 'pushplus'

export interface NotificationChannel {
  id: string
  type: NotificationChannelType
  label: string
  destinationMasked: string
  enabled: boolean
  timezone: string | null
  quietHoursStart: string | null
  quietHoursEnd: string | null
  allowedWeekdays: number[] | null
  verifiedAt: string | null
}

export interface NotificationChannelList {
  meta: ResponseMeta
  items: NotificationChannel[]
}

export type AlertRuleType =
  | 'price_threshold'
  | 'absolute_drop'
  | 'percentage_drop'
  | 'new_low'
  | 'direct_available'
  | 'round_trip_range'

export interface AlertRule {
  id: string
  subscriptionId: string
  name: string
  ruleType: AlertRuleType
  enabled: boolean
  severity: string
  thresholdPriceMinor: number | null
  thresholdCurrency: string | null
  thresholdPercentage: number | null
  comparisonWindowDays: number | null
  cooldownSeconds: number
  channelIds: string[]
  createdAt: string
  updatedAt: string
}

export interface AlertEvent {
  id: string
  alertRuleId: string
  subscriptionId: string
  collectionRunId: string | null
  eventType: string
  severity: string
  title: string
  body: string
  eventPayload: Record<string, unknown>
  suppressedAt: string | null
  createdAt: string
}

export interface NotificationDelivery {
  id: string
  alertEventId: string
  notificationChannelId: string
  status: 'pending' | 'sending' | 'succeeded' | 'failed' | 'suppressed'
  attemptCount: number
  nextAttemptAt: string | null
  sentAt: string | null
  errorCode: string | null
  errorMessage: string | null
  updatedAt: string
}

function queryString(values: object): string {
  const params = new URLSearchParams()
  Object.entries(values as Record<string, string | number | boolean | undefined>).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return params.toString()
}

function mapSubscription(
  subscription: BackendSubscription,
  latest?: Pick<WatchedRoute, 'latestPriceMinor' | 'observedAt' | 'priceStatus'>,
): FareSubscription {
  const legs = [...subscription.legs].sort((left, right) => left.position - right.position)
  const outbound = legs[0]
  const inbound = legs[1]
  return {
    id: subscription.id,
    name: subscription.name,
    origin: outbound?.origin || '',
    destination: outbound?.destination || '',
    tripType: subscription.trip_type === 'round_trip' ? 'roundtrip' : 'oneway',
    departureDate: outbound?.departure_date || '',
    returnDate: inbound?.departure_date,
    directOnly: subscription.filters.direct_only,
    currency: subscription.currency,
    maxPriceMinor: subscription.filters.max_price_minor,
    targetPriceMinor: subscription.target_price_minor ?? null,
    targetCurrency: subscription.target_currency ?? null,
    latestPriceMinor: latest?.latestPriceMinor ?? null,
    priceStatus: latest?.priceStatus ?? 'unavailable',
    latestObservedAt: latest?.observedAt ?? null,
    enabled: subscription.enabled,
    updatedAt: subscription.updated_at,
    airlineCodes: subscription.filters.airline_codes,
    departureAirports: subscription.filters.departure_airports,
    arrivalAirports: subscription.filters.arrival_airports,
    maxStops: subscription.filters.max_stops,
    maxDurationMinutes: subscription.filters.max_duration_minutes,
    nextDueAt: subscription.next_due_at,
    lastCollectedAt: subscription.last_collected_at,
  }
}

export const fareQueryKeys = {
  all: ['fares'] as const,
  overview: () => [...fareQueryKeys.all, 'overview'] as const,
  search: (query: FlightSearchParams) => [...fareQueryKeys.all, 'search', query] as const,
  subscriptions: () => [...fareQueryKeys.all, 'subscriptions'] as const,
  subscriptionOptions: () => [...fareQueryKeys.all, 'subscription-options'] as const,
  history: (routeId: string, options?: PriceHistoryOptions) => (
    [...fareQueryKeys.all, 'history', routeId, options] as const
  ),
  calendar: (routeId: string, options?: CalendarPriceOptions) => (
    [...fareQueryKeys.all, 'calendar', routeId, options] as const
  ),
  collectionRuns: () => [...fareQueryKeys.all, 'collection-runs'] as const,
  collectionOperations: () => [...fareQueryKeys.all, 'collection-operations'] as const,
  notificationChannels: () => [...fareQueryKeys.all, 'notification-channels'] as const,
  alertRules: (subscriptionId?: string) => [...fareQueryKeys.all, 'alert-rules', subscriptionId || 'all'] as const,
  alertEvents: () => [...fareQueryKeys.all, 'alert-events'] as const,
  deliveries: () => [...fareQueryKeys.all, 'deliveries'] as const,
}
export async function getDashboardOverview(): Promise<DashboardOverview> {
  return apiRequest<DashboardOverview>('/dashboard/overview')
}

export async function searchFlights(query: FlightSearchParams, cursor?: string): Promise<FlightSearchResult> {
  const encoded = queryString({ ...query, cursor })
  return apiRequest<FlightSearchResult>(`/fares/search?${encoded}`)
}

export async function getSubscriptions(cursor?: string): Promise<SubscriptionList> {
  const response = await apiRequest<BackendSubscriptionPage>(`/subscriptions?${queryString({ limit: 100, cursor })}`)
  let latestById = new Map<string, WatchedRoute>()
  try {
    const overview = await apiRequest<DashboardOverview>('/dashboard/overview')
    latestById = new Map(overview.routes.map((route) => [route.id, route]))
  } catch {
    // The subscription list is still useful when the overview aggregation is temporarily unavailable.
  }

  return {
    meta: { mode: 'live', generatedAt: response.as_of },
    items: response.items.map((item) => mapSubscription(item, latestById.get(item.id))),
    nextCursor: response.next_cursor,
  }
}

export async function createSubscription(input: CreateSubscriptionInput): Promise<FareSubscription> {
  const legs = [
    {
      origin: input.origin,
      destination: input.destination,
      departure_date: input.departureDate,
    },
  ]
  if (input.tripType === 'roundtrip' && input.returnDate) {
    legs.push({
      origin: input.destination,
      destination: input.origin,
      departure_date: input.returnDate,
    })
  }

  const response = await apiRequest<BackendSubscription>('/subscriptions', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      enabled: input.enabled,
      target_price_minor: input.targetPriceMinor,
      search: {
        trip_type: input.tripType === 'roundtrip' ? 'round_trip' : 'one_way',
        legs,
        passengers: { adults: input.passengers || 1, children: 0, infants: 0 },
        filters: {
          direct_only: input.directOnly,
          airline_codes: input.airlineCodes?.split(',').map((code) => code.trim()).filter(Boolean) || [],
          departure_airports: input.departureAirports?.split(',').map((code) => code.trim()).filter(Boolean) || [],
          arrival_airports: input.arrivalAirports?.split(',').map((code) => code.trim()).filter(Boolean) || [],
          max_price_minor: input.maxPriceMinor,
          max_stops: input.maxStops,
          max_duration_minutes: input.maxDurationMinutes,
          departure_minute_start: input.departureMinuteStart,
          departure_minute_end: input.departureMinuteEnd,
        },
      },
    }),
  })
  return mapSubscription(response)
}

export async function updateSubscription(id: string, input: Partial<FareSubscription>): Promise<FareSubscription> {
  const response = await apiRequest<BackendSubscription>(`/subscriptions/${id}/state`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled: input.enabled }),
  })
  return mapSubscription(response)
}

export async function deleteSubscription(id: string): Promise<void> {
  await apiRequest(`/subscriptions/${id}`, { method: 'DELETE' })
}

export async function getPriceHistory(
  routeId: string,
  options: PriceHistoryOptions = {},
): Promise<PriceHistoryResult> {
  return apiRequest<PriceHistoryResult>(`/prices/history?${queryString({ routeId, ...options })}`)
}

export async function getCalendarPrices(
  routeId: string,
  options: CalendarPriceOptions = {},
): Promise<CalendarPriceResult> {
  return apiRequest<CalendarPriceResult>(`/prices/calendar?${queryString({ routeId, ...options })}`)
}

export async function getCollectionRuns(cursor?: string): Promise<CollectionRunList> {
  return apiRequest<CollectionRunList>(`/collection/runs?${queryString({ limit: 20, cursor })}`)
}

export async function getCollectionOperations(): Promise<CollectionOperations> {
  return apiRequest<CollectionOperations>('/collection/operations')
}

export async function getNotificationChannels(): Promise<NotificationChannelList> {
  return apiRequest<NotificationChannelList>('/notifications/channels')
}

export interface NotificationChannelScheduleInput {
  timezone: string | null
  quietHoursStart: string | null
  quietHoursEnd: string | null
  allowedWeekdays: number[] | null
}

export interface CreateNotificationChannelInput extends NotificationChannelScheduleInput {
  type: NotificationChannelType
  label: string
  destination: string
}

export type UpdateNotificationChannelInput = Partial<NotificationChannelScheduleInput> & {
  enabled?: boolean
}

export async function createNotificationChannel(input: CreateNotificationChannelInput): Promise<NotificationChannel> {
  return apiRequest<NotificationChannel>('/notifications/channels', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateNotificationChannel(id: string, input: UpdateNotificationChannelInput): Promise<NotificationChannel> {
  return apiRequest<NotificationChannel>(`/notifications/channels/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function getAlertRules(subscriptionId?: string): Promise<{ items: AlertRule[] }> {
  const query = queryString({ subscriptionId })
  return apiRequest<{ items: AlertRule[] }>(`/alerts/rules${query ? `?${query}` : ''}`)
}

export interface CreateAlertRuleInput {
  subscriptionId: string
  name: string
  ruleType: AlertRuleType
  enabled: boolean
  thresholdPriceMinor?: number | null
  thresholdCurrency?: string | null
  thresholdPercentage?: number | null
  comparisonWindowDays?: number | null
  cooldownSeconds?: number
  channelIds?: string[]
  ruleConfig?: Record<string, unknown>
}

export async function createAlertRule(input: CreateAlertRuleInput): Promise<AlertRule> {
  return apiRequest<AlertRule>('/alerts/rules', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateAlertRule(
  id: string,
  input: Partial<Omit<CreateAlertRuleInput, 'subscriptionId'>> & { enabled?: boolean },
): Promise<AlertRule> {
  return apiRequest<AlertRule>(`/alerts/rules/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function deleteAlertRule(id: string): Promise<void> {
  await apiRequest(`/alerts/rules/${id}`, { method: 'DELETE' })
}

export async function getAlertEvents(cursor?: string): Promise<{ items: AlertEvent[]; nextCursor: string | null }> {
  return apiRequest<{ items: AlertEvent[]; nextCursor: string | null }>(`/alerts/events?${queryString({ limit: 50, cursor })}`)
}

export async function getNotificationDeliveries(): Promise<{ items: NotificationDelivery[] }> {
  return apiRequest<{ items: NotificationDelivery[] }>('/alerts/deliveries?limit=100')
}
