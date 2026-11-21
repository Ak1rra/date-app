"""Deterministic places for DEMO_MODE and tests: 30 demo venues, no network.

Venues sit at fixed offsets from the demo centre (the same point FakeGeocoder is centred on),
so a place id always resolves to the same venue wherever it is looked up from. `search` then
keeps the ones within `radius_m` of the point it is given.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from app.providers.geocoding.fake import DEMO_LAT, DEMO_LNG
from app.providers.types import NOT_FOUND, LatLng, Place, ProviderUnavailable, is_open_at

ATTRIBUTION = "Demo data (not real venues)"
DEMO_CENTER = LatLng(DEMO_LAT, DEMO_LNG)
SPREAD_M = 2500  # venues sit within this many metres north/south and east/west of the centre

_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "best",
    "for",
    "good",
    "in",
    "near",
    "nearby",
    "of",
    "or",
    "place",
    "places",
    "the",
    "to",
    "with",
    "date",
    "night",
    "around",
    "me",
    "my",
    "our",
}

Hours = list[tuple[int, tuple[int, int], tuple[int, int]]]  # (day Sun=0, (h, m) open, (h, m) close)

ALL = [0, 1, 2, 3, 4, 5, 6]
TUE_SUN = [0, 2, 3, 4, 5, 6]
WED_SUN = [0, 3, 4, 5, 6]
ALWAYS_OPEN = [{"open": {"day": 0, "hour": 0, "minute": 0}}]


def _days(days: list[int], opens: tuple[int, int], closes: tuple[int, int]) -> Hours:
    return [(d, opens, closes) for d in days]


def _periods(hours: Hours) -> list[dict[str, dict[str, int]]]:
    """Google-shaped periods; a closing time of 24:00 or later falls on the next day."""
    periods = []
    for day, (open_h, open_m), (close_h, close_m) in hours:
        periods.append(
            {
                "open": {"day": day, "hour": open_h, "minute": open_m},
                "close": {
                    "day": (day + close_h // 24) % 7,
                    "hour": close_h % 24,
                    "minute": close_m,
                },
            }
        )
    return periods


@dataclass(frozen=True)
class _Venue:
    slug: str
    name: str
    types: tuple[str, ...]
    price: int
    rating: float
    keywords: str
    hours: Hours | None  # None = always open

    @property
    def place_id(self) -> str:
        return f"demo-place-{self.slug}"


RESTAURANT = _days(TUE_SUN, (11, 0), (22, 0)) + _days([5, 6], (11, 0), (23, 0))
DINNER = _days(ALL, (17, 0), (22, 0)) + _days([5, 6], (17, 0), (23, 0))
BAR = _days(ALL, (16, 0), (26, 0))  # closes at 02:00
CAFE = _days(ALL, (7, 0), (18, 0))
DAYTIME = _days(TUE_SUN, (10, 0), (17, 0))
EVENING = _days(ALL, (12, 0), (22, 0))
GARDEN = _days(ALL, (8, 0), (18, 0))
STUDIO = _days(WED_SUN, (10, 0), (20, 0))
CINEMA = _days(ALL, (12, 0), (24, 0))
SHOWS = _days([0, 4, 5, 6], (19, 0), (24, 0))
SHOPS = _days(ALL, (9, 0), (21, 0))

VENUES: tuple[_Venue, ...] = (
    _Venue(
        "la-trattoria",
        "La Trattoria",
        ("italian_restaurant", "restaurant"),
        2,
        4.6,
        "dinner pasta pizza italian romantic wine",
        RESTAURANT,
    ),
    _Venue(
        "taqueria-sol",
        "Taqueria Sol",
        ("mexican_restaurant", "restaurant"),
        1,
        4.4,
        "lunch dinner tacos mexican casual cheap",
        _days(ALL, (10, 0), (21, 0)),
    ),
    _Venue(
        "sakura-sushi",
        "Sakura Sushi",
        ("japanese_restaurant", "restaurant"),
        3,
        4.7,
        "dinner sushi japanese romantic",
        DINNER,
    ),
    _Venue(
        "the-smoke-pit",
        "The Smoke Pit",
        ("barbecue_restaurant", "restaurant"),
        2,
        4.5,
        "lunch dinner bbq barbecue casual",
        RESTAURANT,
    ),
    _Venue(
        "ember-and-oak",
        "Ember & Oak",
        ("steak_house", "restaurant"),
        4,
        4.8,
        "dinner steak special occasion romantic fancy",
        DINNER,
    ),
    _Venue(
        "green-fork",
        "The Green Fork",
        ("vegetarian_restaurant", "restaurant"),
        2,
        4.5,
        "lunch dinner vegetarian vegan healthy",
        RESTAURANT,
    ),
    _Venue(
        "pho-corner",
        "Pho Corner",
        ("vietnamese_restaurant", "restaurant"),
        1,
        4.3,
        "lunch dinner pho vietnamese noodles cheap",
        _days(ALL, (10, 30), (21, 30)),
    ),
    _Venue(
        "copper-still",
        "The Copper Still",
        ("bar", "cocktail_bar"),
        2,
        4.5,
        "drinks cocktails evening nightlife",
        BAR,
    ),
    _Venue(
        "skyline-lounge",
        "Skyline Rooftop Lounge",
        ("bar", "lounge"),
        3,
        4.4,
        "drinks cocktails rooftop view romantic evening nightlife",
        BAR,
    ),
    _Venue(
        "vinyl-wine-bar",
        "Vinyl Wine Bar",
        ("wine_bar", "bar"),
        3,
        4.6,
        "drinks wine music cozy romantic evening",
        BAR,
    ),
    _Venue(
        "daybreak-coffee",
        "Daybreak Coffee",
        ("cafe", "coffee_shop"),
        1,
        4.6,
        "coffee breakfast brunch morning cozy",
        CAFE,
    ),
    _Venue(
        "sweet-spot-bakery",
        "Sweet Spot Bakery",
        ("bakery", "cafe"),
        1,
        4.7,
        "dessert pastry breakfast coffee sweet",
        CAFE,
    ),
    _Venue(
        "tea-and-honey",
        "Tea & Honey",
        ("tea_house", "cafe"),
        1,
        4.5,
        "tea quiet cozy afternoon",
        _days(ALL, (9, 0), (19, 0)),
    ),
    _Venue(
        "gelato-lane",
        "Gelato Lane",
        ("ice_cream_shop",),
        1,
        4.6,
        "dessert gelato ice cream sweet walk",
        EVENING,
    ),
    _Venue(
        "riverside-park",
        "Riverside Park",
        ("park", "tourist_attraction"),
        0,
        4.7,
        "outdoor walk picnic free nature romantic activity",
        None,
    ),
    _Venue(
        "botanical-gardens",
        "Botanical Gardens",
        ("botanical_garden", "park"),
        1,
        4.8,
        "outdoor garden walk flowers nature romantic activity",
        GARDEN,
    ),
    _Venue(
        "lakeview-trail",
        "Lakeview Trail",
        ("hiking_area", "park"),
        0,
        4.7,
        "outdoor hike walk nature free active activity",
        None,
    ),
    _Venue(
        "sunset-overlook",
        "Sunset Overlook",
        ("tourist_attraction",),
        0,
        4.8,
        "outdoor view sunset free romantic evening",
        None,
    ),
    _Venue(
        "city-art-museum",
        "City Art Museum",
        ("art_museum", "museum"),
        2,
        4.6,
        "indoor art culture exhibit gallery activity",
        DAYTIME,
    ),
    _Venue(
        "discovery-science-center",
        "Discovery Science Center",
        ("museum",),
        2,
        4.4,
        "indoor science interactive fun activity",
        DAYTIME,
    ),
    _Venue(
        "history-house",
        "Old Town History House",
        ("history_museum", "museum"),
        1,
        4.3,
        "indoor history culture quiet activity",
        DAYTIME,
    ),
    _Venue(
        "clay-and-kiln",
        "Clay & Kiln Pottery Studio",
        ("art_studio",),
        2,
        4.9,
        "pottery class workshop hands-on creative indoor fun activity",
        STUDIO,
    ),
    _Venue(
        "page-turner-books",
        "Page Turner Books",
        ("book_store", "store"),
        1,
        4.7,
        "bookstore books browse quiet cozy indoor",
        SHOPS,
    ),
    _Venue(
        "starlight-cinema",
        "Starlight Cinema",
        ("movie_theater",),
        2,
        4.4,
        "movie film cinema indoor evening fun activity",
        CINEMA,
    ),
    _Venue(
        "fairway-mini-golf",
        "Fairway Mini Golf",
        ("miniature_golf_course",),
        1,
        4.5,
        "minigolf golf outdoor games fun playful activity",
        EVENING,
    ),
    _Venue(
        "serenity-day-spa",
        "Serenity Day Spa",
        ("spa",),
        4,
        4.8,
        "spa massage relax couples treat indoor activity",
        _days(ALL, (9, 0), (20, 0)),
    ),
    _Venue(
        "bowl-o-rama",
        "Bowl-O-Rama",
        ("bowling_alley",),
        2,
        4.2,
        "bowling games playful indoor evening fun activity",
        EVENING,
    ),
    _Venue(
        "locked-in-escape",
        "Locked In Escape Rooms",
        ("amusement_center",),
        2,
        4.7,
        "escape room puzzle games teamwork indoor fun activity",
        EVENING,
    ),
    _Venue(
        "comedy-cellar",
        "The Comedy Cellar",
        ("comedy_club", "night_club"),
        2,
        4.6,
        "comedy show laugh evening nightlife activity",
        SHOWS,
    ),
    _Venue(
        "spin-cycle-records",
        "Spin Cycle Records",
        ("store",),
        1,
        4.6,
        "records vinyl music browse indoor quiet activity",
        _days(ALL, (11, 0), (20, 0)),
    ),
)


def _digest(slug: str) -> bytes:
    return hashlib.sha256(slug.encode()).digest()


def _location(venue: _Venue) -> LatLng:
    digest = _digest(venue.slug)
    north = (digest[0] / 255 * 2 - 1) * SPREAD_M
    east = (digest[1] / 255 * 2 - 1) * SPREAD_M
    return DEMO_CENTER.offset_m(north, east)


def _place(venue: _Venue) -> Place:
    digest = _digest(venue.slug)
    return Place(
        place_id=venue.place_id,
        name=venue.name,
        address=f"{100 + digest[2] * 3} Demo Street, Austin, TX",
        location=_location(venue),
        rating=venue.rating,
        rating_count=80 + digest[3] * 9,
        price_level=venue.price,
        types=list(venue.types),
        website=f"https://example.com/{venue.slug}",
        maps_uri=f"https://example.com/maps/{venue.place_id}",
        opening_periods=_periods(venue.hours) if venue.hours is not None else ALWAYS_OPEN,
        accessibility={"wheelchairAccessibleEntrance": digest[4] % 5 != 0},
        attributions=[ATTRIBUTION],
    )


def _tokens(query: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOPWORDS]
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def _matches(venue: _Venue, tokens: list[str]) -> bool:
    if not tokens:
        return True
    haystack = f"{venue.name} {' '.join(venue.types)} {venue.keywords}".lower().replace("_", " ")
    return any(token in haystack for token in tokens)


class FakePlaces:
    """Deterministic PlacesProvider."""

    async def search(
        self,
        query: str,
        near: LatLng,
        radius_m: int,
        *,
        included_type: str | None = None,
        price_levels: list[int] | None = None,
        open_at: datetime | None = None,
        max_results: int = 8,
    ) -> list[Place]:
        if max_results <= 0:
            return []
        tokens = _tokens(query)
        found: list[tuple[float, Place]] = []
        for venue in VENUES:
            place = _place(venue)
            assert place.location is not None
            distance = near.distance_m(place.location)
            if distance > radius_m or not _matches(venue, tokens):
                continue
            if included_type and included_type not in venue.types:
                continue
            if price_levels and venue.price not in price_levels:
                continue
            if open_at is not None and is_open_at(place.opening_periods, open_at) is False:
                continue
            found.append((distance, place))
        found.sort(key=lambda item: (item[0], item[1].place_id))
        return [place for _, place in found[:max_results]]

    async def details(self, place_id: str) -> Place:
        for venue in VENUES:
            if venue.place_id == place_id:
                return _place(venue)
        raise ProviderUnavailable("fake_places", NOT_FOUND)
