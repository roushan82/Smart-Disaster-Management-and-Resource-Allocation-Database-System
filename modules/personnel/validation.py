import re

from core.validation import choice, finish, integer, text

PERSONNEL_ROLES = (
    "Doctor", "Nurse", "Paramedic", "Rescue Worker",
    "Firefighter", "Driver", "Team Leader",
)
PERSONNEL_STATUSES = ("AVAILABLE", "DEPLOYED", "UNAVAILABLE")


def validate_personnel(form):
    errors = {}
    contact = form.get("contact", "")
    if not isinstance(contact, str):
        errors["contact"] = "Phone number must be text."
        contact = ""
    elif not contact:
        errors["contact"] = "Phone number is required."
    elif not re.fullmatch(r"[6-9][0-9]{9}", contact):
        errors["contact"] = (
            "Enter a 10-digit Indian mobile number starting with 6, 7, 8, or 9. "
            "Spaces and the +91 prefix are not supported."
        )
    values = {
        "name": text(form, "name", "Name", errors, 100),
        "role": choice(form, "role", "Role", PERSONNEL_ROLES, errors),
        "contact": contact,
        "team_id": integer(form, "team_id", "Rescue team", errors),
    }
    return finish(errors, values)
