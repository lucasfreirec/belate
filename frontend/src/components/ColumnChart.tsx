import { useEffect, useRef, useState } from 'react'
import { axisTop } from '../lib/axis'

export interface Column {
  label: string
  value: number
  /** Extra line for the tooltip and the table, such as the number of trains. */
  detail?: string
}

interface Props {
  /** What the chart shows; also names the single series, so no legend is needed. */
  title: string
  columns: Column[]
  formatValue: (value: number) => string
  /** Header of the value column in the table view. */
  valueLabel: string
  /** Header of the category column in the table view. */
  categoryLabel: string
}

const HEIGHT = 200
const MARGIN = { top: 20, right: 8, bottom: 26, left: 44 }
const MAX_BAR = 24
const RADIUS = 4

/** A column whose top corners are rounded and whose base sits square on the baseline. */
function columnPath(x: number, y: number, width: number, height: number): string {
  const r = Math.min(RADIUS, height, width / 2)
  const bottom = y + height
  return `M${x},${bottom}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${bottom}Z`
}

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

/** A single-series column chart with a hover and focus tooltip and a table view. */
export function ColumnChart({ title, columns, formatValue, valueLabel, categoryLabel }: Props) {
  const [ref, width] = useWidth()
  const [active, setActive] = useState<number | null>(null)

  const plotWidth = width - MARGIN.left - MARGIN.right
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom
  const { top, step } = axisTop(Math.max(0, ...columns.map((c) => c.value)))
  const band = plotWidth / Math.max(1, columns.length)
  const barWidth = Math.max(2, Math.min(MAX_BAR, band - 2))
  const y = (value: number) => MARGIN.top + plotHeight * (1 - Math.max(0, value) / top)
  const baseline = MARGIN.top + plotHeight

  // Label every column when they fit, otherwise every second, third... one.
  const labelEvery = Math.ceil(46 / band)
  const highest = columns.reduce((best, c, i) => (c.value > columns[best].value ? i : best), 0)
  const hovered = active === null ? null : columns[active]

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <div className="chart-plot" ref={ref}>
        <svg width={width} height={HEIGHT} role="img" aria-label={title}>
          {[0, 1, 2, 3].map((tick) => (
            <g key={tick}>
              <line
                className={tick === 0 ? 'axis-line' : 'grid-line'}
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick * step)}
                y2={y(tick * step)}
              />
              <text className="tick" x={MARGIN.left - 8} y={y(tick * step)} dy="0.32em" textAnchor="end">
                {formatValue(tick * step)}
              </text>
            </g>
          ))}
          {columns.map((column, index) => {
            const x = MARGIN.left + band * index + (band - barWidth) / 2
            const height = baseline - y(column.value)
            return (
              <g key={column.label}>
                {height > 0 && (
                  <path
                    className={index === active ? 'bar bar-active' : 'bar'}
                    d={columnPath(x, y(column.value), barWidth, height)}
                  />
                )}
                {/* The whole band is the target, so thin or empty columns can be reached. */}
                <rect
                  className="hit"
                  x={MARGIN.left + band * index}
                  y={MARGIN.top}
                  width={band}
                  height={plotHeight}
                  tabIndex={0}
                  role="img"
                  aria-label={`${column.label}: ${formatValue(column.value)}${column.detail ? `, ${column.detail}` : ''}`}
                  onPointerEnter={() => setActive(index)}
                  onPointerLeave={() => setActive(null)}
                  onFocus={() => setActive(index)}
                  onBlur={() => setActive(null)}
                />
                {index % labelEvery === 0 && (
                  <text className="tick" x={x + barWidth / 2} y={baseline + 16} textAnchor="middle">
                    {column.label}
                  </text>
                )}
                {index === highest && column.value > 0 && active === null && (
                  <text className="value-label" x={x + barWidth / 2} y={y(column.value) - 6} textAnchor="middle">
                    {formatValue(column.value)}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
        {hovered && active !== null && (
          <div
            className="tooltip"
            style={{
              left: Math.min(width - 150, Math.max(0, MARGIN.left + band * active + band / 2 - 70)),
              top: Math.max(0, y(hovered.value) - 58),
            }}
          >
            <strong>{formatValue(hovered.value)}</strong>
            <span>{hovered.label}</span>
            {hovered.detail && <span>{hovered.detail}</span>}
          </div>
        )}
      </div>
      <details className="table-view">
        <summary>Show as table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">{categoryLabel}</th>
              <th scope="col">{valueLabel}</th>
              {columns.some((c) => c.detail) && <th scope="col">Detail</th>}
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => (
              <tr key={column.label}>
                <th scope="row">{column.label}</th>
                <td>{formatValue(column.value)}</td>
                {columns.some((c) => c.detail) && <td>{column.detail}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}
