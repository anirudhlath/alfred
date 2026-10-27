"""The simulated house: devices, the memories that matter, and the questions asked of them.

Everything here is static, hand-written content. ``dataset.py`` turns it into a
timeline with a seeded RNG. Probe questions were written before any run, as a user
would ask them — not tuned against results.

Significant memories carry the entities the Librarian's analysis LLM would extract
(the eval runs no LLM); the real ``SignificanceScorer`` scores them. Passive
observations and Reflex actions carry none of that — they go through the Memory
Ingestor's own summary builders, exactly as production writes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Routine devices — the passive noise that fills the hot store
# ---------------------------------------------------------------------------

# Relative activity per hour of day (index = hour). Mornings and evenings dominate.
HOME_ACTIVITY: tuple[float, ...] = (
    0.2, 0.1, 0.1, 0.1, 0.1, 0.3, 1.0, 2.0, 2.0, 1.0, 0.8, 0.8,
    1.0, 0.8, 0.7, 0.8, 1.0, 1.5, 2.0, 2.2, 2.0, 1.6, 1.0, 0.5,
)  # fmt: skip
EVENING: tuple[float, ...] = (
    0.3, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.1, 0.6, 1.5, 2.0, 2.0, 1.5, 1.0, 0.6,
)  # fmt: skip
WORKDAY: tuple[float, ...] = (
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 1.0, 1.5, 1.5, 1.2,
    0.8, 1.2, 1.5, 1.5, 1.2, 0.6, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0,
)  # fmt: skip


@dataclass(frozen=True)
class ToggleDevice:
    """A device that flips to ``active`` and back to ``idle`` a few times a day."""

    entity_id: str
    friendly_name: str
    idle: str
    active: str
    per_day: tuple[int, int]
    hours: tuple[float, ...] = HOME_ACTIVITY
    # Minutes until the device returns to idle.
    hold_minutes: tuple[int, int] = (1, 10)
    weekend_per_day: tuple[int, int] | None = None
    # Brightness levels shown on activation (lights only).
    brightness: tuple[int, ...] = ()


TOGGLE_DEVICES: tuple[ToggleDevice, ...] = (
    ToggleDevice("binary_sensor.hallway_motion", "Hallway Motion", "off", "on", (10, 14)),
    ToggleDevice("binary_sensor.kitchen_motion", "Kitchen Motion", "off", "on", (8, 12)),
    ToggleDevice("binary_sensor.living_room_motion", "Living Room Motion", "off", "on", (6, 10)),
    ToggleDevice("binary_sensor.bathroom_motion", "Bathroom Motion", "off", "on", (6, 10)),
    ToggleDevice(
        "binary_sensor.office_motion",
        "Office Motion",
        "off",
        "on",
        (4, 8),
        hours=WORKDAY,
        weekend_per_day=(0, 2),
    ),
    ToggleDevice("binary_sensor.porch_motion", "Porch Motion", "off", "on", (1, 4)),
    ToggleDevice(
        "light.hallway",
        "Hallway Light",
        "off",
        "on",
        (4, 7),
        hold_minutes=(3, 60),
        brightness=(51, 102, 153),
    ),
    ToggleDevice(
        "light.kitchen",
        "Kitchen Light",
        "off",
        "on",
        (4, 6),
        hold_minutes=(10, 90),
        brightness=(153, 204, 255),
    ),
    ToggleDevice(
        "light.living_room",
        "Living Room Light",
        "off",
        "on",
        (3, 5),
        hours=EVENING,
        hold_minutes=(30, 180),
        brightness=(77, 102, 153),
    ),
    ToggleDevice(
        "light.bedroom",
        "Bedroom Light",
        "off",
        "on",
        (2, 4),
        hold_minutes=(5, 60),
        brightness=(26, 51, 102),
    ),
    ToggleDevice(
        "light.bathroom",
        "Bathroom Light",
        "off",
        "on",
        (4, 6),
        hold_minutes=(3, 25),
        brightness=(204, 255),
    ),
    ToggleDevice(
        "light.office",
        "Office Light",
        "off",
        "on",
        (2, 4),
        hours=WORKDAY,
        hold_minutes=(30, 240),
        weekend_per_day=(0, 1),
        brightness=(153, 204),
    ),
    ToggleDevice(
        "light.porch",
        "Porch Light",
        "off",
        "on",
        (1, 2),
        hours=EVENING,
        hold_minutes=(60, 300),
        brightness=(128,),
    ),
    ToggleDevice("binary_sensor.front_door_contact", "Front Door", "off", "on", (4, 8), (), (1, 3)),
    ToggleDevice("binary_sensor.back_door_contact", "Back Door", "off", "on", (1, 4), (), (1, 3)),
    ToggleDevice("cover.garage_door", "Garage Door", "closed", "open", (1, 3), (), (2, 10)),
    ToggleDevice("lock.front_door", "Front Door Lock", "locked", "unlocked", (2, 4), (), (1, 5)),
    ToggleDevice(
        "switch.coffee_maker",
        "Coffee Maker",
        "off",
        "on",
        (1, 1),
        hours=(0,) * 6 + (3.0, 2.0, 1.0) + (0,) * 15,
        hold_minutes=(8, 20),
    ),
)
# An empty ``hours`` tuple means "use HOME_ACTIVITY" (keeps the one-line entries short).

THERMOSTAT_SETPOINTS: tuple[int, ...] = (67, 68, 69, 70, 71)

TV_TITLES: tuple[str, ...] = (
    "The Great British Bake Off",
    "Bluey",
    "Seinfeld",
    "Only Murders in the Building",
    "Ted Lasso",
    "Abbott Elementary",
    "Slow Horses",
    "Severance",
    "Taskmaster",
    "Brooklyn Nine-Nine",
    "Shogun",
    "The Last of Us",
    "Hacks",
    "Parks and Recreation",
    "Champions League Highlights",
    "BBC News at Ten",
    "Masterchef",
    "The Crown",
    "Succession",
    "Gardeners' World",
)

SPEAKER_PLAYLISTS: tuple[str, ...] = (
    "Morning Jazz",
    "Discover Weekly",
    "Lo-fi Beats",
    "Classic Soul",
    "Today's Top Hits",
    "Cooking Mix",
    "Acoustic Covers",
    "Radio 4 Today",
)


@dataclass(frozen=True)
class ReflexTemplate:
    """A System 1 action the Reflex Engine takes most days."""

    trigger_entity: str
    trigger_old: str
    trigger_new: str
    tool_name: str
    parameters: dict[str, str | int]
    reason: str
    per_day: tuple[int, int]
    hours: tuple[float, ...]


REFLEX_TEMPLATES: tuple[ReflexTemplate, ...] = (
    ReflexTemplate(
        "binary_sensor.hallway_motion",
        "off",
        "on",
        "lighting.turn_on",
        {"room": "hallway", "brightness": 40},
        "Motion in the hallway after dark",
        (2, 4),
        EVENING,
    ),
    ReflexTemplate(
        "binary_sensor.kitchen_motion",
        "off",
        "on",
        "lighting.turn_on",
        {"room": "kitchen", "brightness": 80},
        "Motion in the kitchen after dark",
        (1, 3),
        EVENING,
    ),
    ReflexTemplate(
        "sun.sun",
        "above_horizon",
        "below_horizon",
        "lighting.turn_on",
        {"room": "porch"},
        "Sunset — porch light on",
        (1, 1),
        (0,) * 16 + (1.0, 1.0) + (0,) * 6,
    ),
    ReflexTemplate(
        "media_player.living_room_tv",
        "off",
        "playing",
        "lighting.dim_lights",
        {"room": "living_room", "brightness": 30},
        "TV started — dim the living room",
        (1, 2),
        EVENING,
    ),
)


# ---------------------------------------------------------------------------
# Memories that matter, and the questions that should surface them
# ---------------------------------------------------------------------------

Source = Literal["conversation", "trigger", "integration"]


@dataclass(frozen=True)
class SignificantEvent:
    """Something a user would expect Alfred to remember.

    ``entities`` stand in for what the Librarian's analysis LLM extracts.
    ``probe`` is the question that should surface it (``None``: background only).
    """

    day: int
    hour: int
    minute: int
    source: Source
    summary: str
    entities: tuple[str, ...]
    probe: str | None = None


SIGNIFICANT_EVENTS: tuple[SignificantEvent, ...] = (
    # --- things sir said ---------------------------------------------------
    SignificantEvent(
        1,
        19,
        12,
        "conversation",
        "Sir said the plumber is coming Thursday at 10am to fix the leaking kitchen sink.",
        ("plumber", "kitchen_sink"),
        "When is the plumber coming?",
    ),
    SignificantEvent(
        5,
        20,
        5,
        "conversation",
        "Sir mentioned his mother is visiting from the 14th to the 21st and will stay in "
        "the guest room.",
        ("mother", "guest_room"),
        "When is my mom coming to stay?",
    ),
    SignificantEvent(
        7,
        8,
        40,
        "conversation",
        "Sir asked Alfred to remember that the spare house key is with the neighbour, Priya, "
        "at number 42.",
        ("spare_key", "priya"),
        "Who has our spare key?",
    ),
    SignificantEvent(
        13,
        18,
        55,
        "conversation",
        "Sir said he is allergic to cashews and wants a warning about any recipe that uses them.",
        ("allergy", "cashews"),
        "Do I have any food allergies?",
    ),
    SignificantEvent(
        19,
        9,
        15,
        "conversation",
        "Sir said the car registration has to be renewed before the end of March.",
        ("car", "registration"),
        "When does the car registration need renewing?",
    ),
    SignificantEvent(
        26,
        21,
        30,
        "conversation",
        "Sir said the old Wi-Fi router was replaced and the new one lives in the hall cupboard.",
        ("router", "hall_cupboard"),
        "Where did we put the new router?",
    ),
    SignificantEvent(
        31,
        19,
        45,
        "conversation",
        "Sir said his sister Ana's birthday is on April 9 and he wants a reminder a week before.",
        ("ana", "birthday"),
        "When is Ana's birthday?",
    ),
    SignificantEvent(
        38,
        17,
        20,
        "conversation",
        "Sir said the dog's vaccination appointment at the vet is on the 2nd at 4pm.",
        ("dog", "vet"),
        "When is the dog's vet appointment?",
    ),
    SignificantEvent(
        44,
        10,
        5,
        "conversation",
        "Sir said never to run the robot vacuum while he is on work calls in the office.",
        ("vacuum.robot", "office"),
        "Is there a rule about when the robot vacuum can run?",
    ),
    SignificantEvent(
        52,
        20,
        50,
        "conversation",
        "Sir said the landlord is inspecting the flat next Friday morning.",
        ("landlord", "inspection"),
        "When is the landlord's inspection?",
    ),
    SignificantEvent(
        3,
        21,
        10,
        "conversation",
        "Sir asked for the porch light to switch off at midnight from now on.",
        ("light.porch",),
    ),
    SignificantEvent(
        23,
        7,
        50,
        "conversation",
        "Sir said he is training for a half marathon in May and runs on Tuesday and "
        "Saturday mornings.",
        ("half_marathon", "running"),
    ),
    SignificantEvent(
        35,
        22,
        15,
        "conversation",
        "Sir said the guest Wi-Fi network is called Maple-Guest.",
        ("guest_wifi",),
    ),
    SignificantEvent(
        49,
        18,
        30,
        "conversation",
        "Sir said he will be travelling to Lisbon for work from the 20th to the 24th.",
        ("lisbon", "travel"),
    ),
    # --- alerts --------------------------------------------------------------
    SignificantEvent(
        2,
        3,
        12,
        "trigger",
        "Urgent: the front door opened at 03:12 while the house was in away mode; security "
        "alert sent to Sir.",
        ("binary_sensor.front_door_contact",),
        "Has the front door ever opened in the middle of the night while we were away?",
    ),
    SignificantEvent(
        10,
        18,
        42,
        "trigger",
        "Smoke alarm triggered in the kitchen at 18:42 and cleared after three minutes.",
        ("smoke_detector.kitchen",),
        "Has the smoke alarm gone off?",
    ),
    SignificantEvent(
        16,
        6,
        30,
        "trigger",
        "Critical: water leak detected under the upstairs bathroom sink.",
        ("sensor.bathroom_leak",),
        "Have we had a leak in the bathroom?",
    ),
    SignificantEvent(
        22,
        1,
        30,
        "trigger",
        "Alert: the garage door was left open for two hours overnight.",
        ("cover.garage_door",),
        "Was the garage door ever left open all night?",
    ),
    SignificantEvent(
        33,
        2,
        10,
        "trigger",
        "Urgent: motion on the porch camera at 02:10 while Sir was asleep; security alert sent.",
        ("camera.porch",),
        "Was there anyone on the porch in the middle of the night?",
    ),
    SignificantEvent(
        47,
        11,
        5,
        "trigger",
        "Emergency: the carbon monoxide detector in the basement reported a fault.",
        ("co_detector.basement",),
        "Has the carbon monoxide detector had any problems?",
    ),
    SignificantEvent(
        8,
        14,
        5,
        "trigger",
        "The doorbell was pressed twice at 14:05 while nobody was home.",
        ("doorbell",),
    ),
    SignificantEvent(
        29,
        23,
        40,
        "trigger",
        "Alarm: the back door was unlocked at 23:40 with nobody home.",
        ("lock.back_door",),
    ),
    SignificantEvent(
        40,
        4,
        15,
        "trigger",
        "Critical: the freezer temperature rose above -10C for 40 minutes.",
        ("sensor.freezer_temperature",),
    ),
    SignificantEvent(
        57,
        16,
        25,
        "trigger",
        "Smoke alarm low-battery warning in the hallway.",
        ("smoke_detector.hallway",),
    ),
    # --- appointments and deliveries ----------------------------------------
    SignificantEvent(
        14,
        9,
        0,
        "integration",
        "Calendar: the boiler and heating maintenance visit is booked for Saturday at 9am.",
        ("boiler", "heating"),
        "When is someone coming to service the heating?",
    ),
    SignificantEvent(
        28,
        12,
        10,
        "integration",
        "Delivery: the new dishwasher arrives Wednesday between 8am and noon.",
        ("dishwasher", "delivery"),
        "When is the dishwasher being delivered?",
    ),
    SignificantEvent(
        41,
        8,
        30,
        "integration",
        "Calendar: dentist appointment for Sir on Tuesday at 3pm.",
        ("dentist",),
        "When is my dentist appointment?",
    ),
    SignificantEvent(
        55,
        19,
        0,
        "integration",
        "Calendar: parent-teacher conference on Thursday at 5:30pm.",
        ("school", "parent_teacher_conference"),
        "When is the parent-teacher conference?",
    ),
    SignificantEvent(
        11,
        7,
        30,
        "integration",
        "Calendar: bins go out on Sunday night; recycling is collected every other Monday.",
        ("bins", "recycling"),
    ),
    SignificantEvent(
        46,
        13,
        0,
        "integration",
        "Delivery: a parcel from the furniture shop was left with the neighbour at number 42.",
        ("parcel", "priya"),
    ),
)


@dataclass(frozen=True)
class RecalledMemory:
    """A memory sir keeps asking about, every ``every_days`` days from the next day on.

    ``observation`` memories go through the ingestor's passive path; the rest are
    written like significant events.
    """

    day: int
    hour: int
    minute: int
    every_days: int
    probe: str
    source: Literal["conversation", "integration", "observation"]
    summary: str = ""
    entities: tuple[str, ...] = ()
    # For ``observation``: (entity_id, old, new, attributes)
    transition: tuple[str, str, str, tuple[tuple[str, str], ...]] | None = None


RECALLED_MEMORIES: tuple[RecalledMemory, ...] = (
    RecalledMemory(
        3,
        8,
        5,
        3,
        "How do I take my coffee?",
        "conversation",
        "Sir's coffee order is an oat milk flat white, extra hot.",
        ("coffee",),
    ),
    RecalledMemory(
        8,
        16,
        0,
        2,
        "What time is school pickup on Wednesdays?",
        "conversation",
        "Sir said school pickup is at 3:15pm on weekdays and 1:30pm on Wednesdays.",
        ("school", "pickup"),
    ),
    RecalledMemory(
        20,
        9,
        45,
        7,
        "When is my physio?",
        "integration",
        "Calendar: weekly physiotherapy with Dr. Osei, Mondays at 6pm at Riverside Clinic.",
        ("physiotherapy",),
    ),
    RecalledMemory(
        18,
        22,
        35,
        5,
        "Which episode of The Bear were we on?",
        "observation",
        transition=(
            "media_player.living_room_tv",
            "playing",
            "paused",
            (("media_title", "The Bear S3E4"), ("friendly_name", "Living Room TV")),
        ),
    ),
    RecalledMemory(
        11,
        6,
        20,
        4,
        "What is the garage Wi-Fi extender's network called?",
        "observation",
        transition=(
            "sensor.garage_extender_ssid",
            "unknown",
            "Maple-Garage-5G",
            (("friendly_name", "Garage Wi-Fi Extender"),),
        ),
    ),
)


@dataclass(frozen=True)
class DetailObservation:
    """A one-off passive observation a user might later ask about.

    These are low-significance by construction — exactly what decay is meant to
    archive — so their probes measure whether an archived memory is still reachable.
    """

    day: int
    hour: int
    minute: int
    entity_id: str
    old: str
    new: str
    attributes: tuple[tuple[str, str], ...]
    probe: str


DETAIL_OBSERVATIONS: tuple[DetailObservation, ...] = (
    DetailObservation(
        4,
        20,
        30,
        "media_player.living_room_tv",
        "off",
        "playing",
        (("media_title", "Planet Earth II: Islands"), ("friendly_name", "Living Room TV")),
        "What was that nature documentary we watched a while back?",
    ),
    DetailObservation(
        6,
        18,
        10,
        "media_player.kitchen_speaker",
        "idle",
        "playing",
        (
            ("media_title", "Hamilton Original Broadway Cast Recording"),
            ("friendly_name", "Kitchen Speaker"),
        ),
        "What was the musical soundtrack we played in the kitchen?",
    ),
    DetailObservation(
        9,
        17,
        0,
        "switch.christmas_tree_lights",
        "off",
        "on",
        (("friendly_name", "Christmas Tree Lights"),),
        "When were the Christmas tree lights last on?",
    ),
    DetailObservation(
        12,
        23,
        5,
        "climate.thermostat",
        "heat",
        "heat",
        (("temperature", "78"), ("friendly_name", "Thermostat")),
        "Did someone turn the heating up really high at some point?",
    ),
    DetailObservation(
        15,
        10,
        25,
        "sensor.washing_machine",
        "running",
        "error",
        (("friendly_name", "Washing Machine"),),
        "Did the washing machine have a problem?",
    ),
    DetailObservation(
        21,
        15,
        40,
        "sensor.basement_humidity",
        "54",
        "81",
        (("friendly_name", "Basement Humidity"),),
        "Has the basement been damp?",
    ),
    DetailObservation(
        24,
        8,
        55,
        "lock.back_door",
        "locked",
        "jammed",
        (("friendly_name", "Back Door Lock"),),
        "Has the back door lock jammed before?",
    ),
    DetailObservation(
        27,
        13,
        20,
        "vacuum.robot",
        "cleaning",
        "error",
        (("friendly_name", "Robot Vacuum"),),
        "Did the robot vacuum get stuck?",
    ),
)


# ---------------------------------------------------------------------------
# Non-episodic index content: semantic sections and routines
# ---------------------------------------------------------------------------

SEMANTIC_FILES: dict[str, str] = {
    "learned.md": """# Learned Preferences

## Lighting
- Prefers the living room at 30% in the evening while the TV is on
- Hallway light at 40% for night-time motion

## Climate
- Likes 68F during the day and 66F overnight
- Heating off when everyone is away

## Media
- Watches TV in the living room most evenings after 8pm
- Morning jazz in the kitchen on weekdays

## Security
- Wants an alert whenever a door opens while the house is in away mode
- Front door locks automatically at 11pm

## Mornings
- Coffee maker on at 6:45 on weekdays
- Prefers a short briefing with weather and calendar before 8am

## Cleaning
- Robot vacuum runs on weekday afternoons when nobody is home
""",
    "profile.md": """# Profile

## Household
- Sir lives with his partner and their two children
- A dog named Biscuit

## Work
- Works from the home office on Mondays, Wednesdays and Fridays

## Health
- Runs three times a week

## Communication
- Prefers Signal for urgent notifications and the web app for everything else
""",
}


@dataclass(frozen=True)
class RoutineSeed:
    name: str
    trigger_pattern: str
    steps: tuple[str, ...]
    confidence: float
    state: Literal["candidate", "active"] = "active"
    learned_from: tuple[str, ...] = field(default=())


ROUTINES: tuple[RoutineSeed, ...] = (
    RoutineSeed("evening_dim", "20:00 daily", ("Dim living room lights to 30%",), 0.8),
    RoutineSeed("morning_coffee", "06:45 weekday", ("Turn on the coffee maker",), 0.85),
    RoutineSeed("porch_at_sunset", "sunset daily", ("Turn on the porch light",), 0.9),
    RoutineSeed("night_lock", "23:00 daily", ("Lock the front door",), 0.75),
    RoutineSeed(
        "away_heating", "weekday morning", ("Set the thermostat to 62F when Sir leaves",), 0.65
    ),
    RoutineSeed(
        "hallway_night_light",
        "after sunset",
        ("Hallway light to 40% on motion",),
        0.7,
        state="candidate",
    ),
)
