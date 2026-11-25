import hmac
import secrets

from flask import abort, request, session


def init_security(app):
    @app.before_request
    def protect_state_changes():
        if not app.secret_key or len(app.secret_key.encode("utf-8")) < 32:
            abort(500, description="Set a random FLASK_SECRET_KEY of at least 32 bytes to protect form sessions.")

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected = session.get("_csrf_token", "")
            submitted = request.form.get("_csrf_token", "") or request.headers.get("X-CSRF-Token", "")
            if not expected or not submitted or not hmac.compare_digest(expected, submitted):
                abort(400, description="Invalid or missing CSRF token.")

    @app.context_processor
    def inject_csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return {"csrf_token": token}
