import { useState } from 'react'
import { api } from '../api/client'
import { AsyncState } from '../components/AsyncState'
import { BarList } from '../components/BarList'
import { ColumnChart } from '../components/ColumnChart'
import { Segmented } from '../components/Segmented'
import { StatTile } from '../components/StatTile'
import { useApi } from '../hooks/useApi'
import {
  branchName,
  directionName,
  formatCount,
  formatDay,
  formatDelay,
  formatHour,
  formatMinutes,
  formatPercent,
} from '../lib/format'

type Window = '7d' | '30d'
const NO_DATA = 'No trains were recorded in this period yet.'

/** The headline figures of the period: punctuality, where and when it is worst, cancellations. */
export function Insights() {
  const [window, setWindow] = useState<Window>('7d')
  const overall = useApi(`overall:${window}`, () => api.punctuality({ window }))
  const byHour = useApi(`hour:${window}`, () => api.punctuality({ window, groupBy: ['hour'] }))
  const byBranch = useApi(`branch:${window}`, () =>
    api.punctuality({ window, groupBy: ['branch', 'direction'] }),
  )
  const cancellations = useApi(`cancellations:${window}`, () => api.cancellations(window))
  const south = useApi(`segments:south:${window}`, () => api.segments('southbound', window))
  const north = useApi(`segments:north:${window}`, () => api.segments('northbound', window))
  const disruptions = useApi('disruptions', () => api.disruptions(true), 120)

  return (
    <>
      <h1>RER B this {window === '7d' ? 'week' : 'month'}</h1>
      <div className="filters">
        <Segmented
          label="Period"
          value={window}
          onChange={setWindow}
          options={[
            { value: '7d', label: 'Last 7 days' },
            { value: '30d', label: 'Last 30 days' },
          ]}
        />
      </div>

      <AsyncState state={disruptions} emptyMessage="" isEmpty={() => false}>
        {(list) =>
          list.length > 0 && (
            <section className="card disruptions" aria-label="Disruptions right now">
              <h2>Disruptions right now</h2>
              <ul>
                {list.map((disruption) => (
                  <li key={disruption.id}>
                    <strong>{disruption.title}</strong>
                    <span className="tag">
                      {disruption.cause === 'TRAVAUX' ? 'Planned works' : 'Incident'}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )
        }
      </AsyncState>

      <AsyncState
        state={overall}
        emptyMessage={NO_DATA}
        isEmpty={(data) => data.rows[0].trains + data.rows[0].cancelled === 0}
      >
        {(data) => {
          const row = data.rows[0]
          return (
            <div className="stat-row">
              <StatTile label="Departures observed" value={formatCount(row.trains)} />
              <StatTile
                label="Average delay"
                value={formatDelay(row.avg_delay_seconds)}
                hint={`9 in 10 under ${formatMinutes(row.p90_delay_seconds)}`}
              />
              <StatTile label="More than 5 min late" value={formatPercent(row.late_share)} />
              <StatTile
                label="Cancelled"
                value={formatPercent(row.cancellation_rate)}
                hint={`${formatCount(row.cancelled)} departures`}
              />
            </div>
          )
        }}
      </AsyncState>

      <div className="grid">
        <section className="card">
          <AsyncState
            state={byHour}
            emptyMessage={NO_DATA}
            isEmpty={(data) => data.rows.every((row) => row.trains === 0)}
          >
            {(data) => (
              <ColumnChart
                title="Average delay by hour of the day"
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

        <section className="card">
          <AsyncState
            state={byBranch}
            emptyMessage={NO_DATA}
            isEmpty={(data) => data.rows.every((row) => row.trains === 0)}
          >
            {(data) => (
              <BarList
                title="Average delay by destination"
                formatValue={formatMinutes}
                bars={data.rows
                  .filter((row) => row.avg_delay_seconds !== null && row.group.branch !== null)
                  .sort((a, b) => (b.avg_delay_seconds ?? 0) - (a.avg_delay_seconds ?? 0))
                  .map((row) => ({
                    label: `To ${branchName(row.group.branch)}`,
                    value: row.avg_delay_seconds ?? 0,
                    detail: `${formatPercent(row.late_share)} over 5 min late`,
                  }))}
              />
            )}
          </AsyncState>
        </section>

        <section className="card">
          <AsyncState
            state={cancellations}
            emptyMessage={NO_DATA}
            isEmpty={(data) => data.by_date.length === 0}
          >
            {(data) => (
              <ColumnChart
                title="Cancelled departures per day"
                categoryLabel="Day"
                valueLabel="Cancelled"
                formatValue={(value) => formatCount(Math.round(value))}
                columns={data.by_date.map((day) => ({
                  label: formatDay(day.service_date),
                  value: day.cancelled,
                  detail: `${formatPercent(day.cancellation_rate)} of departures`,
                }))}
              />
            )}
          </AsyncState>
        </section>

        <section className="card">
          <h2>Where trains lose the most time</h2>
          {[south, north].map((state, index) => (
            <AsyncState
              key={index}
              state={state}
              emptyMessage={index === 0 ? 'No train was followed between two stations yet.' : ''}
              isEmpty={(data) => data.segments.length === 0}
            >
              {(data) => (
                <table className="plain">
                  <caption>{directionName(data.direction)}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Between</th>
                      <th scope="col">Time lost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.segments
                      .filter((segment) => segment.avg_delay_gained_seconds > 0)
                      .slice(0, 4)
                      .map((segment) => (
                        <tr key={`${segment.from_station}-${segment.to_station}`}>
                          <th scope="row">
                            {segment.from_station_name} → {segment.to_station_name}
                          </th>
                          <td>{Math.round(segment.avg_delay_gained_seconds)} s</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </AsyncState>
          ))}
        </section>
      </div>
    </>
  )
}
