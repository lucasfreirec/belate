import { useState } from 'react'
import { api } from '../api/client'
import type { Direction } from '../api/types'
import { AsyncState } from '../components/AsyncState'
import { ColumnChart } from '../components/ColumnChart'
import { Segmented } from '../components/Segmented'
import { useApi } from '../hooks/useApi'
import { formatAge, formatCount, formatDelay, formatHour, formatMinutes, formatTime } from '../lib/format'
import { stationLink } from '../lib/route'

const DEFAULT_STATION = 'STIF:StopArea:SP:473843:' // Cité Universitaire
const LIVE_REFRESH_SECONDS = 30

interface Props {
  station: string | null
}

/** One station: the next trains, and how long people wait and how late trains run, by hour. */
export function StationPage({ station }: Props) {
  const ref = station ?? DEFAULT_STATION
  const [direction, setDirection] = useState<Direction>('northbound')
  const stations = useApi('stations', api.stations)
  const live = useApi(`live:${ref}:${direction}`, () => api.live(ref, direction), LIVE_REFRESH_SECONDS)
  const waiting = useApi(`waiting:${ref}:${direction}`, () => api.waitingTime(ref, direction, '30d'))
  const delays = useApi(`station-delay:${ref}:${direction}`, () =>
    api.punctuality({ window: '30d', station: ref, direction, groupBy: ['hour'] }),
  )
  const name = stations.data?.find((s) => s.ref === ref)?.name ?? 'Station'

  return (
    <>
      <h1>{name}</h1>
      <div className="filters">
        <label>
          Station{' '}
          <select
            value={ref}
            onChange={(event) => {
              window.location.hash = stationLink(event.target.value)
            }}
          >
            {(stations.data ?? [])
              .slice()
              .sort((a, b) => a.name.localeCompare(b.name, 'fr'))
              .map((s) => (
                <option key={s.ref} value={s.ref}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <Segmented
          label="Direction"
          value={direction}
          onChange={setDirection}
          options={[
            { value: 'northbound', label: 'Northbound' },
            { value: 'southbound', label: 'Southbound' },
          ]}
        />
      </div>

      <section className="card">
        <h2>Next trains</h2>
        <AsyncState
          state={live}
          emptyMessage="No upcoming train is known for this station and direction."
          isEmpty={(data) => data.departures.length === 0}
        >
          {(data) => (
            <>
              <table className="plain board">
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Destination</th>
                    <th scope="col">Train</th>
                    <th scope="col">Platform</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.departures.map((departure, index) => (
                    <tr key={`${departure.train_number}-${index}`}>
                      <td>
                        {formatTime(departure.expected_time)}
                        {departure.delay_seconds !== null && Math.abs(departure.delay_seconds) >= 60 && (
                          <s className="scheduled"> {formatTime(departure.aimed_time)}</s>
                        )}
                      </td>
                      <th scope="row">{departure.destination ?? '–'}</th>
                      <td>{departure.train_number ?? '–'}</td>
                      <td>{departure.platform ?? '–'}</td>
                      <td>
                        {departure.cancelled ? (
                          <span className="status status-critical">✕ Cancelled</span>
                        ) : (departure.delay_seconds ?? 0) >= 300 ? (
                          <span className="status status-serious">
                            ▲ {formatDelay(departure.delay_seconds)} late
                          </span>
                        ) : (
                          formatDelay(departure.delay_seconds)
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </AsyncState>
        {live.data && (
          <p className="as-of">Data from {formatAge(live.data.as_of)}.</p>
        )}
      </section>

      <div className="grid">
        <section className="card">
          <AsyncState
            state={waiting}
            emptyMessage="Not enough trains were recorded here to measure the wait."
            isEmpty={(data) => data.hours.length === 0}
          >
            {(data) => (
              <ColumnChart
                title="Average wait by hour, last 30 days"
                categoryLabel="Hour"
                valueLabel="Average wait"
                formatValue={formatMinutes}
                columns={data.hours.map((hour) => ({
                  label: formatHour(hour.hour).slice(0, 2),
                  value: hour.avg_wait_seconds,
                  detail: `a train every ${formatMinutes(hour.avg_gap_seconds)} on average`,
                }))}
              />
            )}
          </AsyncState>
        </section>
        <section className="card">
          <AsyncState
            state={delays}
            emptyMessage="No trains were recorded here yet."
            isEmpty={(data) => data.rows.every((row) => row.trains === 0)}
          >
            {(data) => (
              <ColumnChart
                title="Average delay by hour, last 30 days"
                categoryLabel="Hour"
                valueLabel="Average delay"
                formatValue={formatMinutes}
                columns={data.rows
                  .filter((row) => row.avg_delay_seconds !== null)
                  .map((row) => ({
                    label: formatHour(Number(row.group.hour)).slice(0, 2),
                    value: row.avg_delay_seconds ?? 0,
                    detail: `${formatCount(row.trains)} departures`,
                  }))}
              />
            )}
          </AsyncState>
        </section>
      </div>
    </>
  )
}
