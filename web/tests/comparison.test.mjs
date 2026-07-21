import assert from 'node:assert/strict'
import test from 'node:test'

const {
  alignComparisonTrend,
  preferredComparisonTrack,
  sortComparisonRoutes,
  validateComparisonSelection,
} = await import('../src/lib/comparison.ts')

function option(id, currency = 'CNY') {
  return {
    id,
    name: id,
    enabled: true,
    currency,
    origin: 'SHA',
    destination: 'TYO',
    tripType: 'oneway',
    departureDate: '2026-08-01',
    returnDate: null,
    directOnly: false,
  }
}

function route(id, overrides = {}) {
  return {
    subscriptionId: id,
    name: id,
    enabled: true,
    origin: 'SHA',
    destination: 'TYO',
    originName: '上海',
    destinationName: '东京',
    tripType: 'oneway',
    departureDate: '2026-08-01',
    returnDate: null,
    directOnly: false,
    currency: 'CNY',
    latestDetailedPriceMinor: null,
    detailedPriceStatus: 'unavailable',
    detailedObservedAt: null,
    periodMinPriceMinor: null,
    periodMaxPriceMinor: null,
    periodAveragePriceMinor: null,
    periodSampleCount: 0,
    changePercent: null,
    detailedTrend: [],
    latestCalendarPriceMinor: null,
    calendarLowestPriceMinor: null,
    calendarTotalPriceMinor: null,
    calendarPriceBasis: null,
    calendarObservedAt: null,
    calendarDirectVerified: false,
    calendarTrend: [],
    ...overrides,
  }
}

test('selection requires 2-8 loaded subscriptions with one currency', () => {
  const options = [option('a'), option('b'), option('c', 'USD')]
  assert.equal(validateComparisonSelection(['a'], options), '至少选择 2 条订阅航线')
  assert.equal(validateComparisonSelection(['a', 'missing'], options), '部分订阅尚未加载，请加载更多后重试')
  assert.equal(validateComparisonSelection(['a', 'deleted'], [...options, { ...option('deleted'), missing: true }]), '请移除已经删除的订阅后再保存')
  assert.equal(validateComparisonSelection(['a', 'c'], options), '同一个对比视图只能选择相同币种的订阅')
  assert.equal(validateComparisonSelection(['a', 'b'], options), null)
  assert.equal(validateComparisonSelection(Array.from({ length: 9 }, (_, index) => String(index)), []), '最多选择 8 条订阅航线')
})

test('price sorting is stable and keeps null values last in both directions', () => {
  const routes = [
    route('missing'),
    route('same-a', { latestDetailedPriceMinor: 12000 }),
    route('cheap', { latestDetailedPriceMinor: 9000 }),
    route('same-b', { latestDetailedPriceMinor: 12000 }),
  ]
  assert.deepEqual(sortComparisonRoutes(routes, 'detailed-low').map((item) => item.subscriptionId), ['cheap', 'same-a', 'same-b', 'missing'])
  assert.deepEqual(sortComparisonRoutes(routes, 'detailed-high').map((item) => item.subscriptionId), ['same-a', 'same-b', 'cheap', 'missing'])
})

test('trend alignment leaves explicit gaps instead of filling or carrying prices', () => {
  const routes = [
    route('a', { detailedTrend: [
      { observedAt: '2026-07-01T00:00:00Z', priceMinor: 10000 },
      { observedAt: '2026-07-03T00:00:00Z', priceMinor: 9000 },
    ] }),
    route('b', { detailedTrend: [
      { observedAt: '2026-07-02T00:00:00Z', priceMinor: 11000 },
    ] }),
  ]
  assert.deepEqual(alignComparisonTrend(routes, 'detailed'), [
    { observedAt: '2026-07-01', a: 10000, b: null },
    { observedAt: '2026-07-02', a: null, b: 11000 },
    { observedAt: '2026-07-03', a: 9000, b: null },
  ])
})

test('calendar becomes the preferred track only when detailed data is entirely absent', () => {
  assert.equal(preferredComparisonTrack([
    route('a', { calendarTrend: [{ observedAt: '2026-07-01T00:00:00Z', priceMinor: 10000 }] }),
  ]), 'calendar')
  assert.equal(preferredComparisonTrack([
    route('a', { detailedTrend: [{ observedAt: '2026-07-01T00:00:00Z', priceMinor: 10000 }] }),
  ]), 'detailed')
})

test('detailed and calendar tracks never mix their prices', () => {
  const routes = [route('a', {
    detailedTrend: [{ observedAt: '2026-07-01T00:00:00Z', priceMinor: 10000 }],
    calendarTrend: [{ observedAt: '2026-07-01T00:00:00Z', priceMinor: 8000, directVerified: false }],
  })]
  assert.equal(alignComparisonTrend(routes, 'detailed')[0].a, 10000)
  assert.equal(alignComparisonTrend(routes, 'calendar')[0].a, 8000)
})
