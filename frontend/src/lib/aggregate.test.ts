import { describe, expect, test } from 'vitest'
import type { PunctualityRow, Station } from '../api/types'
import { delayByStation, delayStep, hoursIn } from './aggregate'
import { axisTop } from './axis'
import { placeStations } from './diagram'
import { parseRoute, stationLink } from './route'

function row(station: string, hour: number, trains: number, delay: number | null): PunctualityRow {
  return {
    group: { station, hour },
    station_name: station.toUpperCase(),
    trains,
    cancelled: 0,
    cancellation_rate: 0,
    avg_delay_seconds: delay,
    p90_delay_seconds: delay,
    late_share: 0,
  }
}

describe('delayByStation', () => {
  const rows = [row('a', 8, 30, 240), row('a', 14, 10, 40), row('b', 8, 5, 100), row('b', 14, 0, null)]

  test('combines hours weighted by their number of trains', () => {
    const delays = delayByStation(rows, null)

    // (30 × 240 + 10 × 40) / 40 = 190, not the plain average of 140.
    expect(delays.get('a')).toMatchObject({ trains: 40, avgDelaySeconds: 190, name: 'A' })
    expect(delays.get('b')).toMatchObject({ trains: 5, avgDelaySeconds: 100 })
  })

  test('keeps one hour when asked', () => {
    const delays = delayByStation(rows, 14)

    expect(delays.get('a')).toMatchObject({ trains: 10, avgDelaySeconds: 40 })
    // No train ran at b at 14:00, so it has no figure.
    expect(delays.has('b')).toBe(false)
  })

  test('ignores rows that are not about a station', () => {
    const other: PunctualityRow = { ...row('a', 8, 3, 10), group: { hour: 8 } }

    expect(delayByStation([other], null).size).toBe(0)
  })
})

test('hoursIn lists the hours in clock order', () => {
  expect(hoursIn([row('a', 17, 1, 0), row('a', 6, 1, 0), row('b', 17, 1, 0)])).toEqual([6, 17])
})

test('delayStep sorts delays into five classes', () => {
  expect([-30, 0, 59, 60, 119, 120, 179, 180, 299, 300, 900].map(delayStep)).toEqual([
    0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4,
  ])
})

describe('axisTop', () => {
  test.each([
    [0, 3, 1],
    [2.4, 3, 1],
    [47, 60, 20],
    [230, 300, 100],
    [600, 600, 200],
    [601, 750, 250],
    [1400, 1500, 500],
  ])('a maximum of %s gives a top of %s in steps of %s', (max, top, step) => {
    expect(axisTop(max)).toEqual({ top, step })
  })

  test('the top is never below the maximum', () => {
    for (const max of [1, 7, 13, 99, 101, 499, 2750]) expect(axisTop(max).top).toBeGreaterThanOrEqual(max)
  })
})

describe('placeStations', () => {
  const stations: Station[] = [
    { ref: 'cdg0', name: 'CDG 2', section: 'cdg', position: 0 },
    { ref: 'cdg1', name: 'CDG 1', section: 'cdg', position: 1 },
    { ref: 'cdg2', name: 'Parc', section: 'cdg', position: 2 },
    { ref: 'mit0', name: 'Mitry', section: 'mitry', position: 0 },
    { ref: 'mit1', name: 'Vert-Galant', section: 'mitry', position: 1 },
    { ref: 'tr0', name: 'Aulnay', section: 'trunk', position: 0 },
    { ref: 'tr1', name: 'Bourg-la-Reine', section: 'trunk', position: 1 },
    { ref: 'rob0', name: 'Sceaux', section: 'robinson', position: 0 },
    { ref: 'sr0', name: 'Parc de Sceaux', section: 'saint_remy', position: 0 },
    { ref: 'sr1', name: 'Antony', section: 'saint_remy', position: 1 },
  ]
  const placed = new Map(placeStations(stations).map((s) => [s.ref, s]))
  const y = (ref: string) => placed.get(ref)!.y
  const x = (ref: string) => placed.get(ref)!.x

  test('runs from north at the top to south at the bottom', () => {
    expect(y('cdg0')).toBeLessThan(y('cdg1'))
    expect(y('cdg2')).toBeLessThan(y('tr0'))
    expect(y('tr0')).toBeLessThan(y('tr1'))
    expect(y('tr1')).toBeLessThan(y('rob0'))
    expect(y('sr0')).toBeLessThan(y('sr1'))
  })

  test('both northern branches end on the row above the trunk', () => {
    expect(y('cdg2')).toBe(y('mit1'))
    expect(y('tr0') - y('cdg2')).toBe(y('cdg2') - y('cdg1'))
  })

  test('both southern branches start on the row below the trunk', () => {
    expect(y('rob0')).toBe(y('sr0'))
  })

  test('the branches sit on either side of the trunk', () => {
    expect(x('mit0')).toBeLessThan(x('tr0'))
    expect(x('cdg0')).toBeGreaterThan(x('tr0'))
    expect(x('rob0')).toBeLessThan(x('tr1'))
    expect(x('sr0')).toBeGreaterThan(x('tr1'))
  })
})

describe('routes', () => {
  test.each([
    ['', { page: 'insights' }],
    ['#/', { page: 'insights' }],
    ['#/line', { page: 'line' }],
    ['#/commute', { page: 'commute' }],
    ['#/station', { page: 'station', station: null }],
    ['#/nonsense', { page: 'insights' }],
  ])('"%s" opens %o', (hash, route) => {
    expect(parseRoute(hash)).toEqual(route)
  })

  test('a station link leads back to the same station', () => {
    const ref = 'STIF:StopArea:SP:473843:'

    expect(parseRoute(stationLink(ref))).toEqual({ page: 'station', station: ref })
  })
})
