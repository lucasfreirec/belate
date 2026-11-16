import type {
  Cancellations,
  Dimension,
  Direction,
  Disruption,
  Live,
  Punctuality,
  Section,
  Segments,
  Station,
  Subscription,
  SubscriptionInput,
  WaitingTime,
} from './types'

// The API is served under /api: by the Vite dev proxy locally, by the ingress in production.
const BASE = '/api'

/** A request the API refused or that never reached it. `message` is safe to show. */
export class ApiError extends Error {
  readonly status: number | null

  constructor(message: string, status: number | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type Params = Record<string, string | number | boolean | string[] | undefined | null>

/** Build a path with its query string, leaving out empty values. */
export function buildUrl(path: string, params: Params = {}): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    for (const item of Array.isArray(value) ? value : [value]) query.append(key, String(item))
  }
  const text = query.toString()
  return `${BASE}${path}${text ? `?${text}` : ''}`
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new ApiError('The server cannot be reached. Check your connection and try again.', null)
  }
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status)
  // 204 No Content has no body to parse.
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 429) return 'Too many requests. Wait a minute and try again.'
  if (response.status === 401) return 'The API token is missing or wrong.'
  try {
    const body: unknown = await response.json()
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    // FastAPI validation errors: a list of {loc, msg}.
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((item: { loc?: unknown[]; msg?: string }) => {
          const field = item.loc?.at(-1)
          const message = (item.msg ?? 'invalid value').replace(/^Value error, /, '')
          return typeof field === 'string' && field !== 'body' ? `${field}: ${message}` : message
        })
        .join('; ')
    }
  } catch {
    // The body is not JSON; fall through to the generic message.
  }
  return `The server answered with an error (${response.status}).`
}

export interface PunctualityQuery {
  window?: string
  groupBy?: Dimension[]
  direction?: Direction
  station?: string
  branch?: Section
}

function withToken(token: string, extra: Record<string, string> = {}): RequestInit['headers'] {
  return { Authorization: `Bearer ${token}`, ...extra }
}

export const api = {
  stations: () => request<Station[]>(buildUrl('/stations')),

  punctuality: (query: PunctualityQuery = {}) =>
    request<Punctuality>(
      buildUrl('/punctuality', {
        window: query.window,
        group_by: query.groupBy,
        direction: query.direction,
        station: query.station,
        branch: query.branch,
      }),
    ),

  segments: (direction: Direction, window?: string) =>
    request<Segments>(buildUrl('/segments', { direction, window })),

  cancellations: (window?: string) => request<Cancellations>(buildUrl('/cancellations', { window })),

  waitingTime: (station: string, direction: Direction, window?: string) =>
    request<WaitingTime>(buildUrl('/waiting-time', { station, direction, window })),

  disruptions: (active = false) => request<Disruption[]>(buildUrl('/disruptions', { active })),

  live: (station: string, direction?: Direction) =>
    request<Live>(buildUrl('/live', { station, direction })),

  subscriptions: (token: string) =>
    request<Subscription[]>(buildUrl('/subscriptions'), { headers: withToken(token) }),

  createSubscription: (token: string, body: SubscriptionInput) =>
    request<Subscription>(buildUrl('/subscriptions'), {
      method: 'POST',
      headers: withToken(token, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }),

  deleteSubscription: (token: string, id: number) =>
    request<void>(buildUrl(`/subscriptions/${id}`), {
      method: 'DELETE',
      headers: withToken(token),
    }),
}
