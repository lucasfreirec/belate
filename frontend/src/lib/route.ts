import { useEffect, useState } from 'react'

export type Route =
  | { page: 'insights' }
  | { page: 'line' }
  | { page: 'station'; station: string | null }
  | { page: 'commute' }

/** Read the page from the part of the address after "#". Unknown addresses open the overview. */
export function parseRoute(hash: string): Route {
  const [page, ...rest] = hash.replace(/^#\/?/, '').split('/')
  if (page === 'line') return { page: 'line' }
  if (page === 'commute') return { page: 'commute' }
  if (page === 'station') {
    const station = rest.join('/')
    return { page: 'station', station: station ? decodeURIComponent(station) : null }
  }
  return { page: 'insights' }
}

export function stationLink(ref: string): string {
  return `#/station/${encodeURIComponent(ref)}`
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return route
}
