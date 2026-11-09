from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Notification, NotificationDelivery, Role, User

logger = logging.getLogger("berev.notifications")


def _send_email(settings: Settings, recipient: str, subject: str, body: str) -> None:
    if not settings.smtp_configured:
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


def send_password_reset_email(settings: Settings, recipient: str, token: str) -> None:
    reset_link = f"{settings.public_app_url.rstrip('/')}/reset-password?token={token}"
    _send_email(
        settings,
        recipient,
        "BEREV ERP password reset",
        "A password reset was requested for your account. Use this single-use link "
        f"within {settings.password_reset_minutes} minutes:\n\n{reset_link}\n\n"
        "If you did not request this, you can ignore this message.",
    )


def _recipients(session: Session, notification: Notification) -> list[User]:
    criteria = []
    if notification.user_id is not None:
        criteria.append(User.id == notification.user_id)
    if notification.role_id is not None:
        criteria.append(User.role_id == notification.role_id)
    if not criteria:
        criteria.append(True)
    return list(
        session.scalars(
            select(User)
            .where(User.is_active.is_(True), or_(*criteria))
            .order_by(User.id)
        ).all()
    )


def _materialize_deliveries(session: Session, limit: int) -> None:
    notifications = session.scalars(
        select(Notification)
        .where(
            ~exists(
                select(NotificationDelivery.notification_id).where(
                    NotificationDelivery.notification_id == Notification.id
                )
            )
        )
        .order_by(Notification.created_at, Notification.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for notification in notifications:
        for user in _recipients(session, notification):
            session.add(
                NotificationDelivery(
                    notification_id=notification.id,
                    user_id=user.id,
                    read_at=datetime.now(timezone.utc) if notification.is_read else None,
                )
            )


def process_notification_batch(
    factory: sessionmaker[Session],
    settings: Settings,
    send_email: Callable[[Settings, str, str, str], None] = _send_email,
    batch_size: int = 100,
) -> int:
    """Create per-user delivery records and send queued email only with configured SMTP."""
    now = datetime.now(timezone.utc)
    processed = 0
    with factory.begin() as session:
        _materialize_deliveries(session, batch_size)
    if not settings.smtp_configured:
        return processed

    with factory.begin() as session:
        deliveries = session.execute(
            select(NotificationDelivery, Notification, User)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .join(User, User.id == NotificationDelivery.user_id)
            .where(
                NotificationDelivery.email_sent_at.is_(None),
                NotificationDelivery.next_email_attempt_at <= now,
                User.is_active.is_(True),
            )
            .order_by(NotificationDelivery.notification_id, NotificationDelivery.user_id)
            .limit(batch_size)
            .with_for_update(of=NotificationDelivery, skip_locked=True)
        ).all()
        for delivery, notification, user in deliveries:
            delivery.email_attempts += 1
            delivery.next_email_attempt_at = now + timedelta(
                minutes=min(2 ** min(delivery.email_attempts, 8), 360)
            )
            try:
                send_email(
                    settings,
                    user.email,
                    notification.title,
                    notification.body or notification.title,
                )
            except Exception as error:
                delivery.email_last_error = type(error).__name__[:100]
                logger.warning(
                    "notification_email_failed",
                    extra={"notification_id": notification.id, "error_type": type(error).__name__},
                )
            else:
                delivery.email_sent_at = now
                delivery.email_last_error = None
                processed += 1
    return processed
