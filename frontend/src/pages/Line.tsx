import { useState } from 'react'
import { api } from '../api/client'
import type { Direction } from '../api/types'
import { AsyncState } from '../components/AsyncState'
import { LineDiagram } from '../components/LineDiagram'
import { Segmented } from '../components/Segmented'
import { useApi } from '../hooks/useApi'
import { delayByStation, hoursIn } from '../lib/aggregate'
import { formatHour } from '../lib/format'

/** The whole line at a glance: average delay at each station, by direction and hour. */
export function Line() {
  const [direction, setDirection] = useState<Direction>('southbound')
  const [window, setWindow] = useState<'7d' | '30d'>('7d')
  const [hour, setHour] = useState<number | null>(null)
  const stations = useApi('stations', api.stations)
  const delays = useApi(`line:${direction}:${window}`, () =>
    api.punctuality({ window, direction, groupBy: ['station', 'hour'] }),
  )
  const hours = delays.data ? hoursIn(delays.data.rows) : []

  return (
    <>
      <h1>Delay along the line</h1>
      <p className="lead">
        Average delay of trains leaving each station. Darker means later.
      </p>
      <div className="filters">
        <Segmented
          label="Direction"
          value={direction}
          onChange={setDirection}
          options={[
            { value: 'southbound', label: 'Southbound' },
            { value: 'northbound', label: 'Northbound' },
          ]}
        />
        <Segmented
          label="Period"
          value={window}
          onChange={setWindow}
          options={[
            { value: '7d', label: 'Last 7 days' },
            { value: '30d', label: 'Last 30 days' },
          ]}
        />
        <label>
          Hour{' '}
          <select
            value={hour ?? ''}
            onChange={(event) => setHour(event.target.value === '' ? null : Number(event.target.value))}
          >
            <option value="">All day</option>
            {hours.map((value) => (
              <option key={value} value={value}>
                {formatHour(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <section className="card">
        <AsyncState state={stations} emptyMessage="The list of stations is empty.">
          {(stationList) => (
            <AsyncState state={delays} emptyMessage="">
              {(data) => {
                const byStation = delayByStation(data.rows, hour)
                return (
                  <>
                    {byStation.size === 0 && (
                      <p className="notice">No trains were recorded for this selection yet.</p>
                    )}
                    <LineDiagram stations={stationList} delays={byStation} />
                  </>
                )
              }}
            </AsyncState>
          )}
        </AsyncState>
      </section>
    </>
  )
}
