import assert from 'node:assert/strict'
import test from 'node:test'

const {
  AIRPORTS,
  findFlightLocation,
  locationSearchTerms,
  readableLocation,
} = await import('../src/lib/airport-directory.ts')
const {
  calculateLayoverMinutes,
  formatDurationMinutes,
  localDateTimeParts,
  terminalChangeLabel,
} = await import('../src/lib/flight-itinerary.ts')

test('airport directory exposes readable Shanghai and Tokyo choices', () => {
  assert.equal(findFlightLocation('SHA')?.name, '上海（虹桥 / 浦东）')
  assert.equal(readableLocation('TYO'), '东京（羽田 / 成田） · TYO')
  assert.equal(readableLocation('PVG'), '上海 · 浦东国际机场（PVG）')
  assert.equal(AIRPORTS.find((airport) => airport.code === 'NRT')?.name, '成田国际机场')
  assert.equal(AIRPORTS.find((airport) => airport.code === 'HND')?.name, '羽田机场')
})

test('airport search terms contain Chinese, English, aliases, and IATA codes', () => {
  const narita = AIRPORTS.find((airport) => airport.code === 'NRT')
  assert.ok(narita)
  const terms = locationSearchTerms(narita)
  assert.ok(terms.includes('NRT'))
  assert.ok(terms.includes('东京'))
  assert.ok(terms.includes('Tokyo'))
  assert.ok(terms.includes('narita'))
  assert.ok(terms.includes('成田'))
})

test('local clock formatting does not shift airport time through browser timezone', () => {
  assert.deepEqual(localDateTimeParts('2026-08-20T17:20:00'), {
    date: '2026年8月20日',
    time: '17:20',
  })
  assert.equal(formatDurationMinutes(465), '7 小时 45 分钟')
})

test('transfer details calculate layover and terminal change from adjacent segments', () => {
  const current = {
    arrivalAt: '2026-08-20T00:35:00Z',
    destination: 'KIX',
    destinationName: '关西国际机场',
    destinationTerminal: 'T2',
  }
  const next = {
    departureAt: '2026-08-20T08:20:00Z',
    origin: 'KIX',
    originName: '关西国际机场',
    originTerminal: 'T1',
  }

  assert.equal(calculateLayoverMinutes(current, next), 465)
  assert.equal(terminalChangeLabel(current, next), 'T2 → T1，需要换航站楼')
})
