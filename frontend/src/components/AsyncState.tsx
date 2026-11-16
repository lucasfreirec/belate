import type { ReactNode } from 'react'
import type { Loaded } from '../hooks/useApi'

interface Props<T> {
  state: Loaded<T>
  /** Shown instead of the content when the data holds nothing to display. May be empty. */
  emptyMessage: string
  isEmpty?: (data: T) => boolean
  children: (data: T) => ReactNode
}

/** Render loaded data, or the loading, error or empty state that stands in for it. */
export function AsyncState<T>({ state, emptyMessage, isEmpty, children }: Props<T>) {
  const { data, error, loading, reload } = state

  if (data === undefined) {
    if (error) {
      return (
        <div className="notice notice-error" role="alert">
          <p>{error}</p>
          <button type="button" onClick={reload}>
            Try again
          </button>
        </div>
      )
    }
    return (
      <p className="notice" role="status">
        Loading…
      </p>
    )
  }

  return (
    <>
      {error && (
        <p className="notice notice-error" role="alert">
          Could not refresh: {error} Showing the last data received.
        </p>
      )}
      {isEmpty?.(data) ? (
        // An empty message means the section simply stays out of the way.
        emptyMessage && <p className="notice">{emptyMessage}</p>
      ) : (
        // While newer data loads, the previous render stays in place, dimmed.
        <div className={loading ? 'refreshing' : undefined}>{children(data)}</div>
      )}
    </>
  )
}
