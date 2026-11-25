import hmac
import os
from functools import wraps

from flask import abort, redirect, url_for
from werkzeug.security import check_password_hash


def configured_reviewer():
    username = os.environ.get("REVIEWER_USERNAME", "").strip()
    password_hash = os.environ.get("REVIEWER_PASSWORD_HASH", "").strip()
    if not username or len(username) > 150 or not password_hash:
        return None
    return username, password_hash


def verify_reviewer_credentials(username, password):
    reviewer = configured_reviewer()
    if reviewer is None or not isinstance(username, str) or not isinstance(password, str):
        return None

    expected_username, password_hash = reviewer
    if not hmac.compare_digest(username.strip(), expected_username):
        return None
    try:
        valid_password = check_password_hash(password_hash, password)
    except (TypeError, ValueError):
        return None
    return expected_username if valid_password else None


def reviewer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import session

        reviewer = session.get("historical_reviewer")
        if not isinstance(reviewer, str) or not reviewer:
            return redirect(url_for("reports.historical_review_login"))
        configured = configured_reviewer()
        if configured is None or not hmac.compare_digest(reviewer, configured[0]):
            session.pop("historical_reviewer", None)
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def configured_disaster_officer():
    """Retrieve configured operational disaster officer credentials without fallback."""
    username = os.environ.get("DISASTER_OFFICER_USERNAME", "").strip()
    password_hash = os.environ.get("DISASTER_OFFICER_PASSWORD_HASH", "").strip()
    if not username or len(username) > 150 or not password_hash:
        return None
    return username, password_hash


def verify_disaster_officer_credentials(username, password):
    """Verify operational disaster officer credentials strictly against DISASTER_OFFICER_*."""
    officer = configured_disaster_officer()
    if officer is None or not isinstance(username, str) or not isinstance(password, str):
        return None

    expected_username, password_hash = officer
    if not hmac.compare_digest(username.strip(), expected_username):
        return None
    try:
        valid_password = check_password_hash(password_hash, password)
    except (TypeError, ValueError):
        return None
    return expected_username if valid_password else None


def establish_role_session(session, role_key, identity, other_role_key, configured_other_role):
    """Reset incidental session data while retaining CSRF and a valid other role."""
    csrf_token = session.get("_csrf_token")
    other_identity = session.get(other_role_key)
    session.clear()

    if isinstance(csrf_token, str) and csrf_token:
        session["_csrf_token"] = csrf_token
    if (
        isinstance(other_identity, str)
        and other_identity
        and configured_other_role is not None
        and hmac.compare_digest(other_identity, configured_other_role[0])
    ):
        session[other_role_key] = other_identity
    session[role_key] = identity


def operational_officer_required(view):
    """Ensure view is accessed only by an authenticated disaster officer. Zero reviewer fallback."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import session

        configured = configured_disaster_officer()
        if configured is None:
            abort(503, description="Operational officer authentication is not configured.")

        officer = session.get("disaster_officer")
        if not isinstance(officer, str) or not officer:
            return redirect(url_for("emergency_requests.officer_login"))

        if not hmac.compare_digest(officer, configured[0]):
            session.pop("disaster_officer", None)
            abort(403)
        return view(*args, **kwargs)

    return wrapped
