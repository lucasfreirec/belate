import { useRoute } from './lib/route'
import { Commute } from './pages/Commute'
import { Insights } from './pages/Insights'
import { Line } from './pages/Line'
import { StationPage } from './pages/StationPage'

const LINKS = [
  { href: '#/', page: 'insights', label: 'Overview' },
  { href: '#/line', page: 'line', label: 'Line' },
  { href: '#/station', page: 'station', label: 'Station' },
  { href: '#/commute', page: 'commute', label: 'My commute' },
] as const

function App() {
  const route = useRoute()
  return (
    <>
      <header className="site-header">
        <a className="brand" href="#/">
          Belate
        </a>
        <nav aria-label="Pages">
          {LINKS.map((link) => (
            <a key={link.page} href={link.href} aria-current={route.page === link.page ? 'page' : undefined}>
              {link.label}
            </a>
          ))}
        </nav>
      </header>
      <main>
        {route.page === 'insights' && <Insights />}
        {route.page === 'line' && <Line />}
        {route.page === 'station' && <StationPage station={route.station} />}
        {route.page === 'commute' && <Commute />}
      </main>
      <footer className="site-footer">
        Real-time data from Île-de-France Mobilités (PRIM). Times are Paris time.
      </footer>
    </>
  )
}

export default App
