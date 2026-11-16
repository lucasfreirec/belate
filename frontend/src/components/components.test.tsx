import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import App from '../App'
import { api } from '../api/client'
import { ApiError } from '../api/client'
import type { Loaded } from '../hooks/useApi'
import { formatMinutes } from '../lib/format'
import { AsyncState } from './AsyncState'
import { ColumnChart } from './ColumnChart'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.location.hash = ''
})

function state<T>(changes: Partial<Loaded<T>>): Loaded<T> {
  return { data: undefined, error: null, loading: false, reload: () => {}, ...changes }
}

describe('AsyncState', () => {
  const content = (data: string[]) => <ul>{data.map((item) => <li key={item}>{item}</li>)}</ul>

  test('says it is loading before the first data', () => {
    render(<AsyncState state={state<string[]>({ loading: true })} emptyMessage="Nothing">{content}</AsyncState>)

    expect(screen.getByRole('status').textContent).toBe('Loading…')
  })

  test('shows the error and lets the reader try again', () => {
    const reload = vi.fn()
    render(
      <AsyncState state={state<string[]>({ error: 'The server cannot be reached.', reload })} emptyMessage="Nothing">
        {content}
      </AsyncState>,
    )

    expect(screen.getByRole('alert').textContent).toContain('The server cannot be reached.')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(reload).toHaveBeenCalledOnce()
  })

  test('shows the empty message when there is nothing to display', () => {
    render(
      <AsyncState state={state({ data: [] as string[] })} emptyMessage="No trains yet." isEmpty={(d) => d.length === 0}>
        {content}
      </AsyncState>,
    )

    expect(screen.getByText('No trains yet.')).toBeDefined()
    expect(screen.queryByRole('list')).toBeNull()
  })

  test('shows the data', () => {
    render(<AsyncState state={state({ data: ['KALI48'] })} emptyMessage="Nothing">{content}</AsyncState>)

    expect(screen.getByText('KALI48')).toBeDefined()
  })

  test('keeps the last data when a refresh fails', () => {
    render(
      <AsyncState state={state({ data: ['KALI48'], error: 'Too many requests.' })} emptyMessage="Nothing">
        {content}
      </AsyncState>,
    )

    expect(screen.getByText('KALI48')).toBeDefined()
    expect(screen.getByRole('alert').textContent).toContain('Too many requests.')
  })
})

describe('ColumnChart', () => {
  const columns = [
    { label: '07', value: 120, detail: '40 departures' },
    { label: '08', value: 270, detail: '55 departures' },
    { label: '09', value: 0, detail: '12 departures' },
  ]
  const chart = (
    <ColumnChart
      title="Average delay by hour"
      categoryLabel="Hour"
      valueLabel="Average delay"
      formatValue={formatMinutes}
      columns={columns}
    />
  )

  test('every column can be reached and read without a pointer', () => {
    render(chart)

    expect(screen.getByLabelText('08: 4.5 min, 55 departures')).toBeDefined()
    expect(screen.getByLabelText('09: 0 min, 12 departures')).toBeDefined()
  })

  test('focusing a column shows its value in a tooltip', () => {
    const { container } = render(chart)
    expect(container.querySelector('.tooltip')).toBeNull()

    fireEvent.focus(screen.getByLabelText('07: 2 min, 40 departures'))

    const tooltip = container.querySelector('.tooltip')
    expect(tooltip?.textContent).toBe('2 min0740 departures')
    fireEvent.blur(screen.getByLabelText('07: 2 min, 40 departures'))
    expect(container.querySelector('.tooltip')).toBeNull()
  })

  test('only the highest column carries its value on the chart', () => {
    const { container } = render(chart)

    expect([...container.querySelectorAll('.value-label')].map((n) => n.textContent)).toEqual(['4.5 min'])
  })

  test('a column of zero draws no bar', () => {
    const { container } = render(chart)

    expect(container.querySelectorAll('path.bar')).toHaveLength(2)
  })

  test('the same figures are available as a table', () => {
    render(chart)

    const rows = within(screen.getByRole('table')).getAllByRole('row')
    expect(rows.map((r) => r.textContent)).toEqual([
      'HourAverage delayDetail',
      '072 min40 departures',
      '084.5 min55 departures',
      '090 min12 departures',
    ])
  })
})

describe('App', () => {
  const emptyPunctuality = {
    window: { since: '', until: '' },
    group_by: [],
    rows: [
      {
        group: {},
        station_name: null,
        trains: 0,
        cancelled: 0,
        cancellation_rate: null,
        avg_delay_seconds: null,
        p90_delay_seconds: null,
        late_share: null,
      },
    ],
  }

  function stubApi() {
    vi.spyOn(api, 'punctuality').mockResolvedValue(emptyPunctuality)
    vi.spyOn(api, 'cancellations').mockResolvedValue({
      trains: 0,
      cancelled: 0,
      cancellation_rate: null,
      by_date: [],
    })
    vi.spyOn(api, 'segments').mockResolvedValue({ direction: 'southbound', segments: [] })
    vi.spyOn(api, 'disruptions').mockResolvedValue([])
    vi.spyOn(api, 'stations').mockResolvedValue([
      { ref: 'STIF:StopArea:SP:473843:', name: 'Cité Universitaire', section: 'trunk', position: 12 },
    ])
    vi.spyOn(api, 'live').mockResolvedValue({ station: 'x', as_of: null, departures: [] })
    vi.spyOn(api, 'waitingTime').mockResolvedValue({ station: 'x', direction: 'northbound', hours: [] })
  }

  test('opens on the overview and says so when nothing was recorded', async () => {
    stubApi()
    render(<App />)

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('RER B this week')
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page')
    await waitFor(() =>
      expect(screen.getAllByText('No trains were recorded in this period yet.').length).toBeGreaterThan(0),
    )
  })

  test('shows the headline figures', async () => {
    stubApi()
    vi.spyOn(api, 'punctuality').mockResolvedValue({
      ...emptyPunctuality,
      rows: [
        {
          ...emptyPunctuality.rows[0],
          trains: 12480,
          cancelled: 312,
          cancellation_rate: 0.0244,
          avg_delay_seconds: 95,
          p90_delay_seconds: 270,
          late_share: 0.081,
        },
      ],
    })
    const { container } = render(<App />)

    await waitFor(() => expect(container.querySelector('.stat-row')).not.toBeNull())
    const tiles = [...container.querySelectorAll('.stat-tile')].map((tile) => tile.textContent)
    expect(tiles).toEqual([
      'Departures observed12,480',
      'Average delay1.6 min9 in 10 under 4.5 min',
      'More than 5 min late8.1%',
      'Cancelled2.4%312 departures',
    ])
  })

  test('shows an error with a way to retry when the API is down', async () => {
    stubApi()
    vi.spyOn(api, 'punctuality').mockRejectedValue(new ApiError('The server cannot be reached.', null))
    render(<App />)

    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0))
    expect(screen.getAllByRole('button', { name: 'Try again' }).length).toBeGreaterThan(0)
  })

  test('follows the address to the station page', async () => {
    stubApi()
    window.location.hash = '#/station/STIF%3AStopArea%3ASP%3A473843%3A'
    render(<App />)

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Cité Universitaire'),
    )
    expect(api.live).toHaveBeenCalledWith('STIF:StopArea:SP:473843:', 'northbound')
    expect(screen.getByText('Data from never.')).toBeDefined()
  })

  test('the commute form sends what was entered', async () => {
    stubApi()
    const create = vi.spyOn(api, 'createSubscription').mockResolvedValue({
      id: 1,
      station_ref: 'STIF:StopArea:SP:473843:',
      direction: 'northbound',
      days: [1, 2, 3, 4, 5],
      start_time: '07:30:00',
      end_time: '09:00:00',
      channel: 'webhook',
      target: 'https://example.com/hook',
      created_at: '',
    })
    vi.spyOn(api, 'subscriptions').mockResolvedValue([])
    window.location.hash = '#/commute'
    render(<App />)

    fireEvent.change(screen.getByLabelText(/API token/), { target: { value: 'secret' } })
    fireEvent.change(screen.getByLabelText(/Webhook address/), {
      target: { value: 'https://example.com/hook' },
    })
    fireEvent.click(screen.getByLabelText('Fri'))
    fireEvent.click(screen.getByRole('button', { name: 'Save alert' }))

    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Saved/))
    expect(create).toHaveBeenCalledWith('secret', {
      station_ref: 'STIF:StopArea:SP:473843:',
      direction: 'northbound',
      days: [1, 2, 3, 4],
      start_time: '07:30',
      end_time: '09:00',
      channel: 'webhook',
      target: 'https://example.com/hook',
    })
  })

  test('the commute form shows why a request was refused', async () => {
    stubApi()
    vi.spyOn(api, 'createSubscription').mockRejectedValue(
      new ApiError('target refused: the host resolves to a private or reserved address', 422),
    )
    window.location.hash = '#/commute'
    render(<App />)

    fireEvent.change(screen.getByLabelText(/API token/), { target: { value: 'secret' } })
    fireEvent.change(screen.getByLabelText(/Webhook address/), {
      target: { value: 'https://internal.example.com/' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save alert' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/private or reserved/))
  })
})
