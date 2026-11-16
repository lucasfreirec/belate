export interface Bar {
  label: string
  value: number
  detail?: string
}

interface Props {
  title: string
  bars: Bar[]
  formatValue: (value: number) => string
}

/** Horizontal bars on one baseline, one row per item, with the value at the tip. */
export function BarList({ title, bars, formatValue }: Props) {
  const max = Math.max(0, ...bars.map((bar) => bar.value))
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <ul className="bar-list">
        {bars.map((bar) => (
          <li key={bar.label} title={bar.detail}>
            <span className="bar-list-label">{bar.label}</span>
            <span className="bar-list-track">
              <span
                className="bar-list-fill"
                style={{ width: max > 0 ? `${(Math.max(0, bar.value) / max) * 100}%` : 0 }}
              />
            </span>
            <span className="bar-list-value">{formatValue(bar.value)}</span>
            {bar.detail && <span className="bar-list-detail">{bar.detail}</span>}
          </li>
        ))}
      </ul>
    </figure>
  )
}
