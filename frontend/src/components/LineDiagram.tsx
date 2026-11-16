import { useState } from 'react'
import type { Section, Station } from '../api/types'
import { delayStep, type StationDelay } from '../lib/aggregate'
import { LABEL_SIDE, type Placed, STEP_LABELS, TOP, WIDTH, placeStations } from '../lib/diagram'
import { formatCount, formatDelay } from '../lib/format'

interface Props {
  stations: Station[]
  delays: Map<string, StationDelay>
}

/** A schematic of the line; each station is coloured by its average delay. */
export function LineDiagram({ stations, delays }: Props) {
  const [active, setActive] = useState<string | null>(null)
  const placed = placeStations(stations)
  const height = TOP * 2 + Math.max(...placed.map((s) => s.y)) - TOP
  const bySection = (section: Section) =>
    placed.filter((s) => s.section === section).sort((a, b) => a.position - b.position)
  const trunk = bySection('trunk')
  const tracks: Placed[][] = [
    // Each branch is drawn down to, or up from, the trunk station it joins.
    [...bySection('cdg'), trunk[0]],
    [...bySection('mitry'), trunk[0]],
    trunk,
    [trunk[trunk.length - 1], ...bySection('robinson')],
    [trunk[trunk.length - 1], ...bySection('saint_remy')],
  ].filter((track) => track.every(Boolean) && track.length > 1)

  const hovered = placed.find((s) => s.ref === active)
  const hoveredDelay = hovered ? delays.get(hovered.ref) : undefined

  return (
    <div className="diagram">
      <ul className="legend" aria-label="Average delay">
        {STEP_LABELS.map((label, step) => (
          <li key={label}>
            <span className={`swatch step-${step}`} />
            {label}
          </li>
        ))}
        <li>
          <span className="swatch step-none" />
          No data
        </li>
      </ul>
      <div className="diagram-scroll">
        <svg width={WIDTH} height={height} role="img" aria-label="RER B line, average delay per station">
          {tracks.map((track, index) => (
            <polyline key={index} className="track" points={track.map((s) => `${s.x},${s.y}`).join(' ')} />
          ))}
          {placed.map((station) => {
            const delay = delays.get(station.ref)
            const step =
              delay?.avgDelaySeconds == null ? 'none' : String(delayStep(delay.avgDelaySeconds))
            const left = LABEL_SIDE[station.section] === 'left'
            return (
              <g key={station.ref}>
                <text
                  className={station.ref === active ? 'station-name station-name-active' : 'station-name'}
                  x={station.x + (left ? -14 : 14)}
                  y={station.y}
                  dy="0.32em"
                  textAnchor={left ? 'end' : 'start'}
                >
                  {station.name}
                </text>
                <circle className={`station step-${step}`} cx={station.x} cy={station.y} r={7} />
                {/* A larger invisible target than the dot itself. */}
                <circle
                  className="hit"
                  cx={station.x}
                  cy={station.y}
                  r={12}
                  tabIndex={0}
                  role="img"
                  aria-label={`${station.name}: ${
                    delay?.avgDelaySeconds == null
                      ? 'no data'
                      : `${formatDelay(delay.avgDelaySeconds)} on average, ${formatCount(delay.trains)} trains`
                  }`}
                  onPointerEnter={() => setActive(station.ref)}
                  onPointerLeave={() => setActive(null)}
                  onFocus={() => setActive(station.ref)}
                  onBlur={() => setActive(null)}
                />
              </g>
            )
          })}
        </svg>
        {hovered && (
          <div
            className="tooltip"
            style={{ left: Math.min(WIDTH - 180, hovered.x + 18), top: Math.max(0, hovered.y - 54) }}
          >
            <strong>
              {hoveredDelay?.avgDelaySeconds == null ? 'No data' : formatDelay(hoveredDelay.avgDelaySeconds)}
            </strong>
            <span>{hovered.name}</span>
            {hoveredDelay && <span>{formatCount(hoveredDelay.trains)} trains</span>}
          </div>
        )}
      </div>
      <details className="table-view">
        <summary>Show as table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Station</th>
              <th scope="col">Average delay</th>
              <th scope="col">Trains</th>
            </tr>
          </thead>
          <tbody>
            {placed
              .slice()
              .sort((a, b) => a.y - b.y || a.x - b.x)
              .map((station) => {
                const delay = delays.get(station.ref)
                return (
                  <tr key={station.ref}>
                    <th scope="row">{station.name}</th>
                    <td>{formatDelay(delay?.avgDelaySeconds)}</td>
                    <td>{formatCount(delay?.trains ?? 0)}</td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </details>
    </div>
  )
}
