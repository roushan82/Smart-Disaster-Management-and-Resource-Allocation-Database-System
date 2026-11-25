from core.validation import choice, finish, integer, text

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


def validate_operational_generation(form, has_prior_allocation=False):
    errors = {}
    values = {
        "disaster_id": integer(form, "disaster_id", "Disaster", errors),
        "resource_type_id": integer(form, "resource_type_id", "Resource type", errors),
        "requested_quantity": integer(form, "requested_quantity", "Requested quantity", errors),
        "priority": choice(form, "priority", "Priority", REQUEST_PRIORITIES, errors),
        "justification": text(
            form, "justification", "Operational justification", errors, maximum=500, required=True
        ),
        "submission_token": text(
            form, "submission_token", "Submission token", errors, maximum=64, required=True
        ),
    }

    justification = values.get("justification")
    if justification and len(justification.strip()) < 10:
        errors["justification"] = "Operational justification must contain at least 10 characters."

    if has_prior_allocation:
        ack_val = form.get("escalation_acknowledged", "")
        if ack_val not in ("1", "true", "True", "yes", "on"):
            errors["escalation_acknowledged"] = (
                "You must acknowledge existing allocations to request additional resources."
            )
        escalation_justification = text(
            form,
            "escalation_justification",
            "Escalation justification",
            errors,
            maximum=250,
            required=True,
        )
        if escalation_justification and len(escalation_justification.strip()) < 15:
            errors["escalation_justification"] = (
                "Escalation justification must contain at least 15 characters."
            )
        values["escalation_acknowledged"] = True
        values["escalation_justification"] = escalation_justification
    else:
        values["escalation_acknowledged"] = False
        values["escalation_justification"] = ""

    return finish(errors, values)
