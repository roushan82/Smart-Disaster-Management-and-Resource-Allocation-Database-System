from core.validation import choice, finish, integer

REQUEST_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def validate_request(form):
    errors = {}
    values = {
        "disaster_id": integer(form, "disaster_id", "Disaster", errors),
        "resource_type_id": integer(form, "resource_type_id", "Resource type", errors),
        "requested_quantity": integer(form, "requested_quantity", "Requested quantity", errors),
        "priority": choice(form, "priority", "Priority", REQUEST_PRIORITIES, errors),
    }
    return finish(errors, values)
