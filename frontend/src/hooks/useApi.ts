import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'

export interface Loaded<T> {
  /** The latest data received. Kept while a newer request is in flight. */
  data: T | undefined
  error: string | null
  /** True while a request is in flight, including a refresh of existing data. */
  loading: boolean
  reload: () => void
}

interface Settled<T> {
  data: T | undefined
  error: string | null
  /** Which request the data or error answers. */
  request: string | null
}

/**
 * Load data from the API and reload it when `key` changes.
 *
 * `key` must identify the request: the same key means the same data. While a new request
 * runs, the previous data stays available so the page does not flash empty.
 */
export function useApi<T>(key: string, load: () => Promise<T>, refreshSeconds?: number): Loaded<T> {
  const [settled, setSettled] = useState<Settled<T>>({ data: undefined, error: null, request: null })
  const [attempt, setAttempt] = useState(0)
  const request = `${key}#${attempt}`

  // Always call the newest `load` without making it a dependency of the request effect.
  const latestLoad = useRef(load)
  useEffect(() => {
    latestLoad.current = load
  })

  useEffect(() => {
    let cancelled = false
    latestLoad
      .current()
      .then((data) => {
        if (!cancelled) setSettled({ data, error: null, request })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const message = error instanceof ApiError ? error.message : 'Something went wrong.'
        setSettled((previous) => ({ data: previous.data, error: message, request }))
      })
    return () => {
      // A newer request started, or the page was left: ignore this answer.
      cancelled = true
    }
  }, [request])

  const reload = useCallback(() => setAttempt((count) => count + 1), [])

  useEffect(() => {
    if (!refreshSeconds) return
    const timer = setInterval(reload, refreshSeconds * 1000)
    return () => clearInterval(timer)
  }, [refreshSeconds, reload])

  // An error belongs to the request it answered; a newer request starts clean.
  const loading = settled.request !== request
  return { data: settled.data, error: loading ? null : settled.error, loading, reload }
}
