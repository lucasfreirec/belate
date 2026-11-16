import { afterEach, describe, expect, test, vi } from 'vitest'
import { ApiError, api, buildUrl } from './client'

function reply(status: number, body?: unknown): Response {
  const text = body === undefined ? null : typeof body === 'string' ? body : JSON.stringify(body)
  return new Response(text, { status })
}

function mockFetch(response: Response | Error) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => {
    if (response instanceof Error) throw response
    return response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('buildUrl', () => {
  test('puts the path under /api', () => {
    expect(buildUrl('/stations')).toBe('/api/stations')
  })

  test('adds parameters and repeats list values', () => {
    expect(buildUrl('/punctuality', { window: '7d', group_by: ['branch', 'hour'] })).toBe(
      '/api/punctuality?window=7d&group_by=branch&group_by=hour',
    )
  })

  test('leaves out empty values but keeps false and zero', () => {
    expect(buildUrl('/x', { a: undefined, b: null, c: '', d: false, e: 0 })).toBe('/api/x?d=false&e=0')
  })

  test('encodes station references', () => {
    expect(buildUrl('/live', { station: 'STIF:StopArea:SP:473843:' })).toBe(
      '/api/live?station=STIF%3AStopArea%3ASP%3A473843%3A',
    )
  })
})

describe('reading', () => {
  test('returns the parsed body', async () => {
    const fetchMock = mockFetch(reply(200, [{ ref: 'a', name: 'Antony', section: 'saint_remy', position: 2 }]))

    const stations = await api.stations()

    expect(stations[0].name).toBe('Antony')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/stations')
  })

  test('sends the punctuality filters', async () => {
    const fetchMock = mockFetch(reply(200, { rows: [] }))

    await api.punctuality({ window: '30d', groupBy: ['station', 'hour'], direction: 'northbound' })

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/punctuality?window=30d&group_by=station&group_by=hour&direction=northbound',
    )
  })

  test('asks for active disruptions only when told to', async () => {
    const fetchMock = mockFetch(reply(200, []))

    await api.disruptions(true)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/disruptions?active=true')
  })
})

describe('errors', () => {
  test('shows the reason the API gives', async () => {
    mockFetch(reply(404, { detail: 'Unknown station. See /stations.' }))

    await expect(api.live('nowhere')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'Unknown station. See /stations.',
    })
  })

  test('names the field of a validation error', async () => {
    mockFetch(
      reply(422, {
        detail: [
          { loc: ['body', 'days'], msg: 'List should have at least 1 item' },
          { loc: ['body'], msg: 'Value error, start_time must be before end_time' },
        ],
      }),
    )

    await expect(api.subscriptions('t')).rejects.toThrow(
      'days: List should have at least 1 item; start_time must be before end_time',
    )
  })

  test('explains rate limiting and a bad token in plain words', async () => {
    mockFetch(reply(429, { detail: 'Too many requests. Try again in a minute.' }))
    await expect(api.stations()).rejects.toThrow('Too many requests. Wait a minute and try again.')

    mockFetch(reply(401, { detail: 'A valid API token is required.' }))
    await expect(api.subscriptions('wrong')).rejects.toThrow('The API token is missing or wrong.')
  })

  test('copes with an error that is not JSON', async () => {
    mockFetch(reply(502, '<html>Bad Gateway</html>'))

    await expect(api.stations()).rejects.toThrow('The server answered with an error (502).')
  })

  test('reports an unreachable server', async () => {
    mockFetch(new TypeError('Failed to fetch'))

    const error = await api.stations().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBeNull()
    expect((error as ApiError).message).toMatch(/cannot be reached/)
  })
})

describe('subscriptions', () => {
  const body = {
    station_ref: 'STIF:StopArea:SP:473843:',
    direction: 'northbound' as const,
    days: [1, 2],
    start_time: '07:30',
    end_time: '09:00',
    channel: 'webhook' as const,
    target: 'https://example.com/hook',
  }

  test('creating sends the token and the body as JSON', async () => {
    const fetchMock = mockFetch(reply(201, { id: 3, ...body }))

    const created = await api.createSubscription('secret', body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/subscriptions')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({ Authorization: 'Bearer secret', 'Content-Type': 'application/json' })
    expect(JSON.parse(init?.body as string)).toEqual(body)
    expect(created.id).toBe(3)
  })

  test('deleting copes with an empty answer', async () => {
    const fetchMock = mockFetch(reply(204))

    await expect(api.deleteSubscription('secret', 3)).resolves.toBeUndefined()

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/subscriptions/3')
    expect(init?.method).toBe('DELETE')
    expect(init?.headers).toEqual({ Authorization: 'Bearer secret' })
  })
})
