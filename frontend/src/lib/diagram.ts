// Geometry of the line schematic: where each station sits.

import type { Section, Station } from '../api/types'

const ROW = 22
export const TOP = 16
export const WIDTH = 560
// The trunk runs down the middle; each end forks into a left and a right branch.
const X: Record<Section, number> = { mitry: 200, cdg: 280, trunk: 240, robinson: 200, saint_remy: 280 }
export const LABEL_SIDE: Record<Section, 'left' | 'right'> = {
  mitry: 'left',
  cdg: 'right',
  trunk: 'right',
  robinson: 'left',
  saint_remy: 'right',
}

export const STEP_LABELS = ['Under 1 min', '1 to 2 min', '2 to 3 min', '3 to 5 min', 'Over 5 min']

export interface Placed extends Station {
  x: number
  y: number
}

/** Give every station its place on the schematic, from north (top) to south (bottom). */
export function placeStations(stations: Station[]): Placed[] {
  const count = (section: Section) => stations.filter((s) => s.section === section).length
  const northRows = Math.max(count('cdg'), count('mitry'))
  const trunkStart = northRows
  const southStart = trunkStart + count('trunk')
  return stations.map((station) => {
    let row: number
    if (station.section === 'trunk') row = trunkStart + station.position
    else if (station.section === 'cdg' || station.section === 'mitry')
      // Both northern branches end on the row just above the trunk.
      row = northRows - count(station.section) + station.position
    else row = southStart + station.position
    return { ...station, x: X[station.section], y: TOP + row * ROW }
  })
}
