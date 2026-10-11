from core.validation import choice, finish, integer, text

DISASTER_TYPES = (
    "Flood", "Earthquake", "Fire", "Cyclone",
    "Landslide", "Drought", "Tsunami", "Industrial Accident",
)
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def validate_disaster(form):
    errors = {}
    values = {
        "name": text(form, "name", "Disaster name", errors, 150),
        "type": choice(form, "type", "Disaster type", DISASTER_TYPES, errors),
        "severity": choice(form, "severity", "Severity", SEVERITIES, errors),
        "description": text(form, "description", "Description", errors, required=False),
        "location_id": integer(form, "location_id", "Location", errors),
    }
    return finish(errors, values)
