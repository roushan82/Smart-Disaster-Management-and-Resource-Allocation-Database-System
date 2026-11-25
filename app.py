import os

from flask import Flask

from core.security import init_security
from modules.assignments.routes import bp as assignments_bp
from modules.dashboard.routes import bp as dashboard_bp
from modules.disasters.routes import bp as disasters_bp
from modules.emergency_requests.routes import bp as emergency_requests_bp
from modules.notifications.routes import bp as notifications_bp
from modules.personnel.routes import bp as personnel_bp
from modules.reports.routes import bp as reports_bp
from modules.resources.routes import bp as resources_bp
from modules.teams.routes import bp as teams_bp


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    )
    init_security(app)
    for blueprint in (
        dashboard_bp, disasters_bp, resources_bp, emergency_requests_bp,
        teams_bp, personnel_bp, reports_bp, notifications_bp, assignments_bp,
    ):
        app.register_blueprint(blueprint)
    return app


app = create_app()

if __name__ == "__main__":
    app.run()
