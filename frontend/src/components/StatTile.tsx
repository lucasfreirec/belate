interface Props {
  label: string
  value: string
  hint?: string
}

/** One headline number with its label. */
export function StatTile({ label, value, hint }: Props) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}
