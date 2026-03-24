"""
FIT súbor export pre jednotlivých jazdcov.

Používa knižnicu fit-tool (pip install fit-tool).
Generuje FIT Activity súbor kompatibilný s Garmin Connect, Strava, atď.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _parse_ms(ts_str: str) -> int:
    """ISO timestamp → milisekundy od Unix epoch."""
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def build_fit(session_info: dict, rider_info: dict, records: list) -> bytes:
    """
    Vygeneruje FIT súbor pre jedného jazdca.

    session_info: {started_at, ended_at, label}
    rider_info:   {name, birth_year, weight_kg, gender, max_hr}
    records:      [{ts, hr, cal_inc, watts}]  — zoradené podľa ts

    Vracia bytes (binárny FIT súbor).
    """
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.activity_message import ActivityMessage
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.profile_type import (
        FileType, Manufacturer, Sport, SubSport,
        Activity, Event, EventType, LapTrigger,
    )

    if not records:
        raise ValueError("Žiadne HR záznamy pre export")

    start_ms = _parse_ms(session_info["started_at"])
    if session_info.get("ended_at"):
        end_ms = _parse_ms(session_info["ended_at"])
    else:
        end_ms = _parse_ms(records[-1]["ts"])

    elapsed_sec = max((end_ms - start_ms) / 1000, 1.0)
    total_calories = round(sum(r.get("cal_inc", 0) for r in records))
    hr_values = [r["hr"] for r in records if r.get("hr", 0) > 0]
    avg_hr = round(sum(hr_values) / len(hr_values)) if hr_values else 0
    max_hr = max(hr_values, default=0)

    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    # ── File ID ────────────────────────────────────────────────────────────────
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.time_created = start_ms
    file_id.serial_number = 1
    builder.add(file_id)

    # ── Records (jeden per sekunda) ────────────────────────────────────────────
    for rec in records:
        if not rec.get("hr"):
            continue
        r = RecordMessage()
        r.timestamp = _parse_ms(rec["ts"])
        r.heart_rate = rec["hr"]
        if rec.get("watts"):
            r.power = rec["watts"]
        builder.add(r)

    # ── Lap ────────────────────────────────────────────────────────────────────
    lap = LapMessage()
    lap.timestamp = end_ms
    lap.start_time = start_ms
    lap.total_elapsed_time = elapsed_sec
    lap.total_timer_time = elapsed_sec
    lap.avg_heart_rate = avg_hr
    lap.max_heart_rate = max_hr
    lap.total_calories = total_calories
    lap.sport = Sport.CYCLING
    lap.event = Event.LAP
    lap.event_type = EventType.STOP
    lap.lap_trigger = LapTrigger.SESSION_END
    builder.add(lap)

    # ── Session ────────────────────────────────────────────────────────────────
    session = SessionMessage()
    session.timestamp = end_ms
    session.start_time = start_ms
    session.total_elapsed_time = elapsed_sec
    session.total_timer_time = elapsed_sec
    session.avg_heart_rate = avg_hr
    session.max_heart_rate = max_hr
    session.total_calories = total_calories
    session.sport = Sport.CYCLING
    session.sub_sport = SubSport.INDOOR_CYCLING
    session.event = Event.SESSION
    session.event_type = EventType.STOP
    session.num_laps = 1
    builder.add(session)

    # ── Activity ───────────────────────────────────────────────────────────────
    activity = ActivityMessage()
    activity.timestamp = end_ms
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    builder.add(activity)

    fit_file = builder.build()
    return fit_file.to_bytes()
