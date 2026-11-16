import type { PunctualityRow } from '../api/types'

export interface StationDelay {
  station: string
  name: string | null
  trains: number
  avgDelaySeconds: number | null
}

/**
 * Average delay per station from rows grouped by station and hour.
 *
 * With `hour` set, only that hour is kept. Otherwise the hours are combined, each weighted
 * by its number of trains: a busy hour counts for more than a quiet one.
 */
export function delayByStation(rows: PunctualityRow[], hour: number | null): Map<string, StationDelay> {
  const result = new Map<string, StationDelay & { weighted: number }>()
  for (const row of rows) {
    const station = row.group.station
    if (typeof station !== 'string') continue
    if (hour !== null && row.group.hour !== hour) continue
    if (row.avg_delay_seconds === null || row.trains === 0) continue

    const entry = result.get(station) ?? {
      station,
      name: row.station_name,
      trains: 0,
      avgDelaySeconds: null,
      weighted: 0,
    }
    entry.trains += row.trains
    entry.weighted += row.avg_delay_seconds * row.trains
    entry.avgDelaySeconds = entry.weighted / entry.trains
    result.set(station, entry)
  }
  return result
}

/** The hours present in rows grouped by hour, in clock order. */
export function hoursIn(rows: PunctualityRow[]): number[] {
  const hours = new Set<number>()
  for (const row of rows) if (typeof row.group.hour === 'number') hours.add(row.group.hour)
  return [...hours].sort((a, b) => a - b)
}

// Upper bounds, in seconds, of the delay classes shown on the line diagram.
export const DELAY_STEPS = [60, 120, 180, 300] as const

/** Which of the five delay classes a delay falls in: 0 (under 1 min) to 4 (over 5 min). */
export function delayStep(seconds: number): number {
  const index = DELAY_STEPS.findIndex((limit) => seconds < limit)
  return index === -1 ? DELAY_STEPS.length : index
}
