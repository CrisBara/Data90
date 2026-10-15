"""Consistent labels for match kickoff times and live/final states."""

from datetime import datetime, tzinfo


def match_clock_label(kickoff_utc: datetime, status: str, timezone: tzinfo) -> str:
    """Return a kickoff time, or the live/final label in its place."""
    if status == "FINISHED":
        return "Finalizado"
    if status in {"LIVE", "PAUSED"}:
        return "En vivo"
    return kickoff_utc.astimezone(timezone).strftime("%H:%M")


def match_datetime_label(kickoff_utc: datetime, status: str, timezone: tzinfo) -> str:
    """Keep the local date while replacing finished/live kickoff times with a state label."""
    local_kickoff = kickoff_utc.astimezone(timezone)
    clock = match_clock_label(kickoff_utc, status, timezone)
    if clock in {"Finalizado", "En vivo"}:
        return f"{local_kickoff:%d/%m/%Y} · {clock}"
    return local_kickoff.strftime("%d/%m/%Y %H:%M")
