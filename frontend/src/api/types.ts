// Shapes returned by the API. They mirror app/api/schemas.py.

export type Direction = 'northbound' | 'southbound'
export type Section = 'cdg' | 'mitry' | 'trunk' | 'robinson' | 'saint_remy'
export type Dimension = 'branch' | 'direction' | 'station' | 'day_type' | 'hour' | 'service_date'

export interface Station {
  ref: string
  name: string
  section: Section
  position: number
}

export interface PunctualityRow {
  group: Record<string, string | number | null>
  station_name: string | null
  trains: number
  cancelled: number
  cancellation_rate: number | null
  avg_delay_seconds: number | null
  p90_delay_seconds: number | null
  late_share: number | null
}

export interface Punctuality {
  window: { since: string; until: string }
  group_by: string[]
  rows: PunctualityRow[]
}

export interface Segment {
  from_station: string
  from_station_name: string | null
  to_station: string
  to_station_name: string | null
  trains: number
  avg_delay_gained_seconds: number
}

export interface Segments {
  direction: Direction
  segments: Segment[]
}

export interface Cancellations {
  trains: number
  cancelled: number
  cancellation_rate: number | null
  by_date: { service_date: string; trains: number; cancelled: number; cancellation_rate: number | null }[]
}

export interface WaitingTime {
  station: string
  direction: Direction
  hours: { hour: number; trains: number; avg_wait_seconds: number; avg_gap_seconds: number }[]
}

export interface Disruption {
  id: string
  cause: string
  severity: string
  title: string
  message: string
  last_update: string
  active: boolean
  periods: { begin: string; end: string }[]
  station_refs: string[]
}

export interface LiveDeparture {
  train_number: string | null
  mission: string | null
  direction: Direction | null
  destination: string | null
  platform: string | null
  aimed_time: string | null
  expected_time: string | null
  delay_seconds: number | null
  cancelled: boolean
}

export interface Live {
  station: string
  as_of: string | null
  departures: LiveDeparture[]
}

export interface SubscriptionInput {
  station_ref: string
  direction: Direction
  days: number[]
  start_time: string
  end_time: string
  channel: 'webhook'
  target: string
}

export interface Subscription extends SubscriptionInput {
  id: number
  created_at: string
}
