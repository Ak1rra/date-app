"""Deterministic stand-ins for the planning model (DEMO_MODE and tests).

Each responder calls the real tools through the runner, so the fake providers are exercised, and
builds its output only from the places the tools returned (grounded) or from clearly evergreen
stops. Nothing is invented. Registered into `FAKE_RESPONDERS` on import.
"""

import datetime as dt
import json
from typing import Any

from app.ai.client import FAKE_RESPONDERS, ToolRunner
from app.ai.schemas import (
    BestTime,
    DraftChecklistItem,
    DraftStop,
    GeneratedIdea,
    GeneratedIdeas,
    HintSuggestions,
    IndoorOutdoor,
    ItineraryDraft,
    PlanOption,
    PlanOptions,
    PlanOptionStop,
    PolishedInvitation,
    StopKind,
    SuggestedHint,
    TasteSummaryOut,
)

# Per-person cost guess by Google price level (USD); the fake venues carry no real prices.
_PRICE_USD = {0: 0.0, 1: 15.0, 2: 30.0, 3: 55.0, 4: 90.0}
_PEOPLE = 2


class _Found:
    """The few fields of a tool-returned place the fakes use."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.place_id: str = raw["place_id"]
        self.name: str = raw["name"]
        price = raw.get("price_level")
        self.cost: float | None = None if price is None else _PRICE_USD.get(price, 30.0) * _PEOPLE


async def _search(
    tools: ToolRunner | None, query: str, open_at: dt.datetime | None = None
) -> list[_Found]:
    if tools is None:
        return []
    args: dict[str, Any] = {"query": query, "max_results": 4}
    if open_at is not None:
        args["open_at"] = open_at.isoformat()
    try:
        result = json.loads(await tools.execute("search_places", args))
    except ValueError:
        return []
    if not isinstance(result, dict) or "data" not in result:
        return []
    return [_Found(raw) for raw in result["data"]]


def _total(costs: list[float | None]) -> float | None:
    known = [c for c in costs if c is not None]
    return sum(known) if known else None


def _stop(title: str, kind: StopKind, found: _Found | None, cost: float | None) -> PlanOptionStop:
    return PlanOptionStop(
        title=title[:120],
        kind=kind,
        place_id=found.place_id if found else None,
        evergreen=found is None,
        est_cost=cost if found else None,
    )


def _option(
    title: str, pitch: str, why: str, stops: list[PlanOptionStop], *, occasion: str | None = None
) -> PlanOption:
    return PlanOption(
        title=title,
        pitch=pitch,
        why_for_them=why,
        stops=stops,
        est_cost=_total([s.est_cost for s in stops]),
        grounded=all(not s.evergreen for s in stops),
        occasion=occasion,
    )


async def fake_plan_options(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> PlanOptions:
    dinners = await _search(tools, "dinner")
    desserts = await _search(tools, "dessert")
    parks = await _search(tools, "park")

    def pick(found: list[_Found], i: int) -> _Found | None:
        return found[i % len(found)] if found else None

    dinner, dessert, park = pick(dinners, 0), pick(desserts, 0), pick(parks, 0)
    dessert2 = pick(desserts, 1)

    def named(found: _Found | None, fallback: str) -> str:
        return found.name if found else fallback

    options = [
        _option(
            "Dinner, then something sweet",
            "A proper sit-down dinner and a dessert stop after, because you deserve both "
            "and I intend to steal bites.",
            "Good food, good company and zero rushing: made for a relaxed evening together.",
            [
                _stop(
                    f"Dinner at {named(dinner, 'your favourite local spot')}",
                    "food",
                    dinner,
                    dinner.cost if dinner else None,
                ),
                _stop(
                    f"Dessert at {named(dessert, 'a cosy dessert place')}",
                    "food",
                    dessert,
                    dessert.cost if dessert else None,
                ),
            ],
        ),
        _option(
            "A stroll and a treat",
            "Walk somewhere pretty, hold hands shamelessly, then reward ourselves with "
            "something sweet.",
            "Easy, outdoorsy and unhurried, with plenty of time to talk.",
            [
                _stop(f"Stroll at {named(park, 'a nearby park')}", "outdoors", park, None),
                _stop(
                    f"Treat at {named(dessert2, 'a sweet spot on the way home')}",
                    "food",
                    dessert2,
                    dessert2.cost if dessert2 else None,
                ),
            ],
        ),
        _option(
            "Cosy night in, chef edition",
            "Stay in, cook something ambitious together, and see who is better at it. Spoiler: me.",
            "A low-key night that is all about the two of you.",
            [
                _stop("Cook a new recipe together", "activity", None, None),
                _stop("Movie night with homemade dessert", "other", None, None),
            ],
        ),
    ]
    return PlanOptions(options=options)


# --- Itinerary ---------------------------------------------------------------------------------


def _user_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts = []
    for message in messages:
        content = message.get("content")
        if message.get("role") != "user":
            continue
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append(
                "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
            )
    return texts


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
    return ""


def _parse_window(messages: list[dict[str, Any]]) -> tuple[dt.datetime, dt.datetime]:
    text = _last_user_text(messages)
    start, end = (dt.datetime.fromisoformat(f"{_tomorrow()}T{t}+00:00") for t in ("18:00", "22:00"))
    lo, hi = text.find("{"), text.rfind("}")
    if lo == -1 or hi <= lo:
        return start, end
    try:
        data = json.loads(text[lo : hi + 1])
        ws = dt.datetime.fromisoformat(str(data["window_start"]))
        we = dt.datetime.fromisoformat(str(data["window_end"]))
    except (ValueError, KeyError, TypeError):
        return start, end
    if ws.tzinfo is None or we.tzinfo is None or we <= ws:
        return start, end
    return ws, we


def _tomorrow() -> str:
    return (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)).isoformat()


# (search query, evergreen title, minutes)
_PLAN = (
    ("dinner", "Dinner somewhere you both love", 75),
    ("dessert", "Dessert and a little something sweet", 45),
    ("park", "A slow walk, no destination", 45),
)
_GAP_MINUTES = 30  # between stops: travel plus a buffer


async def _fake_regenerate(
    tools: ToolRunner | None, messages: list[dict[str, Any]]
) -> ItineraryDraft | None:
    """The itinerary from a stop-regeneration message with only the chosen stop swapped for
    another place (or an evergreen stop), keeping every time. None for any other message."""
    # The request is the first user message; a repair pass appends the issue list after it.
    for text in _user_texts(messages):
        lo, hi = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[lo : hi + 1])
            index = int(data["replace_stop_index"])
            current = data["itinerary"]["stops"]
            old = current[index]
            break
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    else:
        return None
    stops = [
        DraftStop(
            title=str(s["title"])[:120],
            place_id=s.get("place_id"),
            evergreen=not s.get("place_id"),
            starts_at=s["starts_at"],
            ends_at=s["ends_at"],
            description=str(s.get("description") or "Enjoy it together.")[:600],
            est_cost=s.get("est_cost"),
        )
        for s in current
    ]
    starts = dt.datetime.fromisoformat(old["starts_at"])
    found = [
        f for f in await _search(tools, "cafe", open_at=starts) if f.place_id != old.get("place_id")
    ]
    place = found[0] if found else None
    stops[index] = DraftStop(
        title=(place.name if place else "A change of scene")[:120],
        place_id=place.place_id if place else None,
        evergreen=place is None,
        starts_at=old["starts_at"],
        ends_at=old["ends_at"],
        description=(
            f"Swap in {place.name} for something a little different."
            if place
            else "Something a little different. Keep it relaxed and make it yours."
        ),
        est_cost=place.cost if place else None,
    )
    return ItineraryDraft(
        title=str(data["itinerary"].get("title") or "A date made for two")[:200],
        stops=stops,
        transport_mode="drive",
    )


async def fake_itinerary(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> ItineraryDraft:
    regenerated = await _fake_regenerate(tools, messages)
    if regenerated is not None:
        return regenerated
    window_start, window_end = _parse_window(messages)
    stops: list[DraftStop] = []
    checklist: list[DraftChecklistItem] = []
    cursor = window_start
    for query, evergreen_title, minutes in _PLAN:
        starts = cursor
        ends = starts + dt.timedelta(minutes=minutes)
        if ends > window_end:
            break
        found = (await _search(tools, query, open_at=starts))[:1]
        place = found[0] if found else None
        stops.append(
            DraftStop(
                title=(place.name if place else evergreen_title)[:120],
                place_id=place.place_id if place else None,
                evergreen=place is None,
                starts_at=starts,
                ends_at=ends,
                description=(
                    f"Enjoy {place.name} together, no phones, plenty of eye contact."
                    if place
                    else f"{evergreen_title}. Keep it relaxed and make it yours."
                ),
                est_cost=place.cost if place else None,
            )
        )
        if place and query == "dinner":
            checklist.append(
                DraftChecklistItem(text=f"Book a table at {place.name}", kind="reserve")
            )
        cursor = ends + dt.timedelta(minutes=_GAP_MINUTES)
    if not stops:
        stops.append(
            DraftStop(
                title="A cosy evening together",
                evergreen=True,
                starts_at=window_start,
                ends_at=window_end,
                description="Keep it simple and spend the time together.",
            )
        )
    return ItineraryDraft(
        title="A date made for two",
        stops=stops,
        transport_mode="drive",
        backup_plan="If the weather turns, swap the walk for a cosy night in with a film.",
        bring_wear="Comfortable shoes and a light layer; dress up a little, I will notice.",
        checklist=checklist,
        invitation_message=(
            "I have planned something for us. Clear your evening, wear something that makes "
            "me forget my own name, and leave the rest to me."
        ),
    )


FAKE_RESPONDERS[PlanOptions] = fake_plan_options


async def fake_polished_invitation(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> PolishedInvitation:
    text = _last_user_text(messages)
    draft = str(json.loads(text[text.find("{") :])["draft"]).strip()
    return PolishedInvitation(text=draft[:996] + " 💕")


async def fake_hint_suggestions(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> HintSuggestions:
    return HintSuggestions(
        hints=[
            SuggestedHint(kind="dress_code", text="Dress for a cozy evening — comfy shoes win."),
            SuggestedHint(kind="eat", text="Come a little hungry."),
            SuggestedHint(kind="ready_by", text="Be ready by the start time with a smile."),
        ]
    )


async def fake_taste_summary(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> TasteSummaryOut:
    text = _last_user_text(messages)
    counts = json.loads(text[text.find("{") :])
    loved = [
        category
        for category, tally in counts.get("votes", {}).get("by_category", {}).items()
        if tally.get("yes", 0) > tally.get("no", 0)
    ][:5]
    return TasteSummaryOut(
        summary="Enjoys " + (", ".join(loved) if loved else "a mix of dates") + ".",
        loves=loved,
        avoid=[],
    )


# --- Ideas -------------------------------------------------------------------------------------


async def _first_event(tools: ToolRunner | None) -> dict[str, Any] | None:
    if tools is None:
        return None
    start = dt.datetime.fromisoformat(f"{_tomorrow()}T00:00:00+00:00")
    args = {"start": start.isoformat(), "end": (start + dt.timedelta(days=7)).isoformat()}
    try:
        result = json.loads(await tools.execute("search_events", args))
    except ValueError:
        return None
    if not isinstance(result, dict) or not result.get("data"):
        return None
    first: dict[str, Any] = result["data"][0]
    return first


async def fake_generated_ideas(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> GeneratedIdeas:
    dinners = await _search(tools, "dinner")
    parks = await _search(tools, "park")
    desserts = await _search(tools, "dessert")
    event = await _first_event(tools)
    dinner = dinners[0] if dinners else None
    park = parks[0] if parks else None
    dessert = desserts[0] if desserts else None

    def idea(
        title: str,
        pitch: str,
        category: StopKind,
        found: _Found | None = None,
        *,
        tags: list[str],
        indoor_outdoor: IndoorOutdoor,
        best_time: BestTime = "any",
        at_home: bool = False,
    ) -> GeneratedIdea:
        return GeneratedIdea(
            title=title[:120],
            pitch=pitch,
            tags=tags,
            category=category,
            place_ids=[found.place_id] if found else [],
            evergreen=found is None,
            est_cost=found.cost if found else None,
            duration_minutes=120,
            best_time=best_time,
            indoor_outdoor=indoor_outdoor,
            at_home=at_home,
        )

    ideas = [
        idea(
            f"Dinner at {dinner.name if dinner else 'your favourite local spot'}",
            "A proper sit-down dinner, because you deserve it and I intend to steal bites.",
            "food",
            dinner,
            tags=["dinner", "cosy"],
            indoor_outdoor="indoor",
            best_time="evening",
        ),
        idea(
            f"Stroll at {park.name if park else 'a nearby park'}",
            "Walk somewhere pretty and hold hands shamelessly.",
            "outdoors",
            park,
            tags=["walk", "easy"],
            indoor_outdoor="outdoor",
            best_time="day",
        ),
    ]
    if event is not None:
        ideas.append(
            GeneratedIdea(
                title=str(event["name"])[:120],
                pitch="Live and loud and ours: grab tickets and make a night of it.",
                tags=["live", "event"],
                category="event",
                event_id=event["event_id"],
                evergreen=False,
                duration_minutes=180,
                best_time="evening",
                indoor_outdoor="either",
            )
        )
    ideas += [
        idea(
            "Cook a new recipe together",
            "Stay in, cook something ambitious, and see who is better at it. Spoiler: me.",
            "activity",
            tags=["cooking", "night in"],
            indoor_outdoor="indoor",
            best_time="evening",
            at_home=True,
        ),
        idea(
            f"Dessert at {dessert.name if dessert else 'a cosy dessert place'}",
            "Skip the main course and go straight to the sweet part.",
            "food",
            dessert,
            tags=["dessert"],
            indoor_outdoor="indoor",
        ),
        idea(
            "Movie night with homemade popcorn",
            "Blankets, a film we both pick and popcorn we definitely over-salt.",
            "other",
            tags=["movie", "night in"],
            indoor_outdoor="indoor",
            best_time="evening",
            at_home=True,
        ),
        idea(
            "Sunrise picnic on a blanket",
            "Coffee, pastries and a very early start for a very pretty view.",
            "outdoors",
            tags=["picnic", "sunrise"],
            indoor_outdoor="outdoor",
            best_time="day",
        ),
        idea(
            "Board game tournament",
            "Loser does the dishes. Winner gloats. Everyone is happy.",
            "activity",
            tags=["games", "night in"],
            indoor_outdoor="indoor",
            at_home=True,
        ),
        idea(
            "Bowling and milkshakes",
            "Gutter balls, terrible shoes and a shared milkshake as the prize for whoever loses.",
            "activity",
            tags=["bowling", "playful"],
            indoor_outdoor="indoor",
            best_time="evening",
        ),
        idea(
            "Bike ride to a lookout",
            "Pedal somewhere with a view, race the last hill, and let me win. Just once.",
            "outdoors",
            tags=["cycling", "views"],
            indoor_outdoor="outdoor",
            best_time="day",
        ),
        idea(
            "Browse a bookshop and pick for each other",
            "Each choose a book for the other, then read the first page aloud over coffee.",
            "activity",
            tags=["books", "cosy"],
            indoor_outdoor="indoor",
            best_time="day",
        ),
        idea(
            "Wine and cheese tasting night",
            "Swirl, sniff, pretend we know what we are doing, and rate everything out of ten.",
            "drink",
            tags=["tasting", "date night"],
            indoor_outdoor="indoor",
            best_time="evening",
        ),
    ]
    return GeneratedIdeas(ideas=ideas)


FAKE_RESPONDERS[ItineraryDraft] = fake_itinerary
FAKE_RESPONDERS[PolishedInvitation] = fake_polished_invitation
FAKE_RESPONDERS[HintSuggestions] = fake_hint_suggestions
FAKE_RESPONDERS[GeneratedIdeas] = fake_generated_ideas
FAKE_RESPONDERS[TasteSummaryOut] = fake_taste_summary
