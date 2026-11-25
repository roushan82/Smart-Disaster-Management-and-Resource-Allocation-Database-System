import re

MAX_MYSQL_INT = 2147483647


class FormValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("The submitted form contains invalid values.")


def text(form, key, label, errors, maximum=None, required=True):
    value = form.get(key, "")
    if not isinstance(value, str):
        errors[key] = f"{label} must be text."
        return ""

    value = value.strip()
    if required and not value:
        errors[key] = f"{label} is required."
    elif maximum is not None and len(value) > maximum:
        errors[key] = f"{label} must be {maximum} characters or fewer."
    return value


def choice(form, key, label, choices, errors):
    value = text(form, key, label, errors)
    if value and value not in choices:
        errors[key] = f"Select a supported {label.lower()}."
    return value


def integer(form, key, label, errors, minimum=1, maximum=MAX_MYSQL_INT):
    value = form.get(key, "")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        errors[key] = f"{label} must be a whole number."
        return None

    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(maximum)):
        errors[key] = f"{label} must be between {minimum} and {maximum}."
        return None
    number = int(normalized)
    if number < minimum or number > maximum:
        if minimum == 0:
            errors[key] = f"{label} must be between 0 and {maximum}."
        else:
            errors[key] = f"{label} must be between {minimum} and {maximum}."
        return None
    return number


def finish(errors, values):
    if errors:
        raise FormValidationError(errors)
    return values


def validate_status(form, field, label, allowed):
    errors = {}
    value = choice(form, field, label, allowed, errors)
    return finish(errors, {field: value})[field]
