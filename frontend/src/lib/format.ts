// Turning API values into text. Times are shown in Paris time, wherever the reader is.

const PARIS = 'Europe/Paris'

/** A delay in seconds as text: "on time", "2 min", "1 min early", "45 s". */
export function formatDelay(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '–'
  const rounded = Math.round(seconds)
  if (Math.abs(rounded) < 30) return 'on time'
  const size = Math.abs(rounded)
  const text = size < 60 ? `${size} s` : `${formatNumber(size / 60, size < 600 ? 1 : 0)} min`
  return rounded < 0 ? `${text} early` : text
}

/** A duration in seconds as minutes: "4.5 min", "12 min". */
export function formatMinutes(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '–'
  const minutes = seconds / 60
  return `${formatNumber(minutes, minutes < 10 ? 1 : 0)} min`
}

/** A share between 0 and 1 as a percentage: "12.5%", "3%", "0%". */
export function formatPercent(share: number | null | undefined): string {
  if (share === null || share === undefined) return '–'
  const percent = share * 100
  return `${formatNumber(percent, percent > 0 && percent < 10 ? 1 : 0)}%`
}

/** A count with thousands separators: "12,480". */
export function formatCount(count: number | null | undefined): string {
  if (count === null || count === undefined) return '–'
  return new Intl.NumberFormat('en-GB').format(count)
}

function formatNumber(value: number, decimals: number): string {
  // Drop a trailing ".0": "2 min", not "2.0 min".
  return String(Number(value.toFixed(decimals)))
}

/** An hour of the day as a range label: 8 becomes "08:00". */
export function formatHour(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`
}

/** A timestamp as Paris clock time: "08:42". */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '–'
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: PARIS,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(iso))
}

/** A service date ("2026-11-16") as a short day label: "Mon 16 Nov". */
export function formatDay(isoDate: string): string {
  // Noon UTC is the same calendar day everywhere, so the label never shifts by a day.
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
    .format(new Date(`${isoDate}T12:00:00Z`))
    .replace(',', '')
}

/** How long ago a timestamp was, in plain words: "just now", "4 min ago", "3 h ago". */
export function formatAge(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never'
  const seconds = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 90) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 48 * 3600) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} days ago`
}

const BRANCH_NAMES: Record<string, string> = {
  cdg: 'Aéroport CDG',
  mitry: 'Mitry – Claye',
  robinson: 'Robinson',
  saint_remy: 'Saint-Rémy',
  trunk: 'Central section',
}

export function branchName(branch: string | number | null | undefined): string {
  return BRANCH_NAMES[String(branch)] ?? 'Unknown'
}

export function directionName(direction: string | null | undefined): string {
  if (direction === 'northbound') return 'Northbound'
  if (direction === 'southbound') return 'Southbound'
  return 'Unknown'
}
