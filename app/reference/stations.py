"""The 47 stations of the RER B, in line order.

The references are the ones used by the PRIM feeds; the numbers come from the Île-de-France
Mobilités open data (`make stations`). The order was checked against real journeys.
"""

from dataclasses import dataclass
from enum import StrEnum


class Section(StrEnum):
    """Part of the line a station belongs to. The line forks at both ends of the trunk."""

    CDG = "cdg"  # north-east branch, to Aéroport Charles de Gaulle 2
    MITRY = "mitry"  # north-east branch, to Mitry - Claye
    TRUNK = "trunk"  # Aulnay-sous-Bois to Bourg-la-Reine, through Paris
    ROBINSON = "robinson"  # south branch, to Robinson
    SAINT_REMY = "saint_remy"  # south branch, to Saint-Rémy-lès-Chevreuse


@dataclass(frozen=True, slots=True)
class Station:
    ref: str
    name: str
    section: Section
    # Rank inside the section, counted from north to south, starting at 0.
    position: int


# Each section is listed from north to south.
_LINE: dict[Section, list[tuple[int, str]]] = {
    Section.CDG: [
        (473364, "Aéroport Charles de Gaulle 2 (Terminal 2)"),
        (462398, "Aéroport CDG 1 (Terminal 3) - RER"),
        (47878, "Parc des Expositions"),
        (58793, "Villepinte"),
        (43193, "Sevran - Beaudottes"),
    ],
    Section.MITRY: [
        (43164, "Mitry - Claye"),
        (46725, "Villeparisis - Mitry-le-Neuf"),
        (46222, "Vert-Galant"),
        (43194, "Sevran - Livry"),
    ],
    Section.TRUNK: [
        (43071, "Aulnay-sous-Bois"),
        (46163, "Le Blanc-Mesnil"),
        (43122, "Drancy"),
        (43231, "Le Bourget"),
        (43140, "La Courneuve - Aubervilliers"),
        (43145, "La Plaine Stade de France"),
        (462394, "Gare du Nord"),
        (45102, "Châtelet - Les Halles"),
        (44877, "Saint-Michel Notre-Dame"),
        (43833, "Luxembourg"),
        (44500, "Port Royal"),
        (473890, "Denfert-Rochereau"),
        (473843, "Cité Universitaire"),
        (45877, "Gentilly"),
        (43607, "Laplace"),
        (43067, "Arcueil - Cachan"),
        (44493, "Bagneux"),
        (43097, "Bourg-la-Reine"),
    ],
    Section.ROBINSON: [
        (59206, "Sceaux"),
        (43125, "Fontenay-aux-Roses"),
        (43186, "Robinson"),
    ],
    Section.SAINT_REMY: [
        (43177, "Parc de Sceaux"),
        (46007, "La Croix de Berny"),
        (43066, "Antony"),
        (43124, "Fontaine Michalon"),
        (43228, "Les Baconnets"),
        (47940, "Massy - Verrières"),
        (58774, "Massy - Palaiseau"),
        (47009, "Palaiseau"),
        (43175, "Palaiseau - Villebon"),
        (474069, "Lozère"),
        (43232, "Le Guichet"),
        (43086, "Orsay - Ville"),
        (43103, "Bures-sur-Yvette"),
        (47046, "La Hacquinière"),
        (47888, "Gif-sur-Yvette"),
        (47052, "Courcelle-sur-Yvette"),
        (47889, "Saint-Rémy-lès-Chevreuse"),
    ],
}

STATIONS: tuple[Station, ...] = tuple(
    Station(f"STIF:StopArea:SP:{number}:", name, section, position)
    for section, stops in _LINE.items()
    for position, (number, name) in enumerate(stops)
)

STATIONS_BY_REF: dict[str, Station] = {station.ref: station for station in STATIONS}
