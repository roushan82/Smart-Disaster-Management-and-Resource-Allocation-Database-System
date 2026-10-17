from core.validation import choice, finish, integer, text

TEAM_TYPES = (
    "Flood Rescue", "Fire & Rescue", "Medical Response", "Search & Rescue",
    "Earthquake Response", "Evacuation Team", "Hazardous Materials Response",
)
TEAM_STATUSES = ("AVAILABLE", "DEPLOYED", "UNAVAILABLE")


def validate_team(form):
    errors = {}
    values = {
        "team_name": text(form, "team_name", "Team name", errors, 100),
        "team_type": choice(form, "team_type", "Team type", TEAM_TYPES, errors),
        "location_id": integer(form, "location_id", "Location", errors),
    }
    return finish(errors, values)
