from core.validation import FormValidationError, choice, finish, text


REVIEW_STATUSES = ("APPROVED", "REJECTED", "NEEDS_MORE_DATA")


def validate_detection_review(form):
    errors = {}
    values = {
        "review_status": choice(
            form,
            "review_status",
            "Review decision",
            REVIEW_STATUSES,
            errors,
        ),
        "reason": text(form, "reason", "Review reason", errors, maximum=2000),
    }
    return finish(errors, values)


__all__ = ("FormValidationError", "REVIEW_STATUSES", "validate_detection_review")
