from flask import Blueprint, abort, redirect, render_template, request, url_for

from modules.notifications import service

bp = Blueprint("notifications", __name__)


@bp.app_context_processor
def inject_unread_notification_count():
    return {"unread_notification_count": service.get_unread_notification_count()}


@bp.route("/notifications")
def page():
    return render_template("notifications/page.html", notifications=service.get_notifications())


@bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_read(notification_id):
    if not service.mark_notification_read(notification_id):
        abort(404)
    return redirect(request.referrer or url_for("notifications.page"))


@bp.route("/notifications/read-all", methods=["POST"])
def mark_all_read():
    service.mark_all_notifications_read()
    return redirect(url_for("notifications.page"))
