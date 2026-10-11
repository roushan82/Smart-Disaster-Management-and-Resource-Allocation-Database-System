from core.validation import finish, integer


def validate_resource(form):
    errors = {}
    values = {
        "type_id": integer(form, "type_id", "Resource type", errors),
        "location_id": integer(form, "location_id", "Location", errors),
        "total_quantity": integer(form, "total_quantity", "Total quantity", errors, minimum=0),
    }
    return finish(errors, values)


def validate_replenishment(form):
    errors = {}
    values = {"quantity": integer(form, "quantity", "Quantity", errors)}
    return finish(errors, values)
