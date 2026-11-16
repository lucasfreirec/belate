import { type FormEvent, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Direction, Subscription } from '../api/types'
import { useApi } from '../hooks/useApi'
import { directionName } from '../lib/format'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const DEFAULT_STATION = 'STIF:StopArea:SP:473843:' // Cité Universitaire

function message(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Something went wrong.'
}

/** Create and manage commute alerts. Needs the server's API token. */
export function Commute() {
  const stations = useApi('stations', api.stations)
  const [token, setToken] = useState('')
  const [station, setStation] = useState(DEFAULT_STATION)
  const [direction, setDirection] = useState<Direction>('northbound')
  const [days, setDays] = useState([1, 2, 3, 4, 5])
  const [start, setStart] = useState('07:30')
  const [end, setEnd] = useState('09:00')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  // undefined: not loaded yet. The list needs the token, so it is loaded on request.
  const [subscriptions, setSubscriptions] = useState<Subscription[] | undefined>()

  const stationName = (ref: string) => stations.data?.find((s) => s.ref === ref)?.name ?? ref

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await action()
    } catch (failure) {
      setError(message(failure))
    } finally {
      setBusy(false)
    }
  }

  const loadList = () => run(async () => setSubscriptions(await api.subscriptions(token)))

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      await api.createSubscription(token, {
        station_ref: station,
        direction,
        days,
        start_time: start,
        end_time: end,
        channel: 'webhook',
        target,
      })
      setSaved(true)
      setSubscriptions(await api.subscriptions(token))
    })
  }

  const remove = (id: number) =>
    run(async () => {
      await api.deleteSubscription(token, id)
      setSubscriptions(await api.subscriptions(token))
    })

  const toggleDay = (day: number) =>
    setDays((current) =>
      current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort(),
    )

  return (
    <>
      <h1>My commute</h1>
      <p className="lead">
        Get one notification per incident on your usual trip: a disruption, unusual delays, or
        cancelled trains. Notifications are sent to a webhook address of yours.
      </p>

      <form className="card form" onSubmit={submit}>
        <label>
          API token
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="off"
            required
          />
          <small>Set by whoever runs the server. It is not stored in your browser.</small>
        </label>
        <label>
          Station
          <select value={station} onChange={(event) => setStation(event.target.value)}>
            {(stations.data ?? [])
              .slice()
              .sort((a, b) => a.name.localeCompare(b.name, 'fr'))
              .map((s) => (
                <option key={s.ref} value={s.ref}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Direction
          <select value={direction} onChange={(event) => setDirection(event.target.value as Direction)}>
            <option value="northbound">Northbound (towards Aéroport CDG, Mitry – Claye)</option>
            <option value="southbound">Southbound (towards Robinson, Saint-Rémy)</option>
          </select>
        </label>
        <fieldset>
          <legend>Days</legend>
          {DAYS.map((name, index) => (
            <label key={name} className="check">
              <input
                type="checkbox"
                checked={days.includes(index + 1)}
                onChange={() => toggleDay(index + 1)}
              />
              {name}
            </label>
          ))}
        </fieldset>
        <div className="row">
          <label>
            From
            <input type="time" value={start} onChange={(event) => setStart(event.target.value)} required />
          </label>
          <label>
            To
            <input type="time" value={end} onChange={(event) => setEnd(event.target.value)} required />
          </label>
        </div>
        <label>
          Webhook address
          <input
            type="url"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="https://example.com/my-webhook"
            required
          />
          <small>Must start with https:// and be reachable from the internet.</small>
        </label>

        {error && (
          <p className="notice notice-error" role="alert">
            {error}
          </p>
        )}
        {saved && (
          <p className="notice notice-success" role="status">
            Saved. You will be notified during this time window.
          </p>
        )}
        <div className="row">
          <button type="submit" className="primary" disabled={busy || days.length === 0}>
            {busy ? 'Saving…' : 'Save alert'}
          </button>
          <button type="button" disabled={busy || token === ''} onClick={() => void loadList()}>
            Show my alerts
          </button>
        </div>
      </form>

      {subscriptions !== undefined && (
        <section className="card">
          <h2>My alerts</h2>
          {subscriptions.length === 0 ? (
            <p className="notice">No alert yet.</p>
          ) : (
            <ul className="subscriptions">
              {subscriptions.map((subscription) => (
                <li key={subscription.id}>
                  <div>
                    <strong>{stationName(subscription.station_ref)}</strong>,{' '}
                    {directionName(subscription.direction).toLowerCase()}
                    <br />
                    {subscription.days.map((day) => DAYS[day - 1]).join(', ')},{' '}
                    {subscription.start_time.slice(0, 5)} to {subscription.end_time.slice(0, 5)}
                  </div>
                  <button type="button" disabled={busy} onClick={() => void remove(subscription.id)}>
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  )
}
