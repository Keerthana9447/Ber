from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models import Notification, NotificationDelivery, User

router = APIRouter(tags=["notifications"])


class NotificationResponse(BaseModel):
    id: int
    kind: str
    title: str
    body: str | None
    link: str | None
    is_read: bool
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationResponse]


def _visible_to_user(user: User):
    return or_(
        Notification.user_id == user.id,
        Notification.role_id == user.role_id,
        and_(Notification.user_id.is_(None), Notification.role_id.is_(None)),
    )


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db)],
    page_size: int = Query(default=10, ge=1, le=50),
) -> NotificationPage:
    rows = session.execute(
        select(Notification, NotificationDelivery.read_at)
        .outerjoin(
            NotificationDelivery,
            and_(
                NotificationDelivery.notification_id == Notification.id,
                NotificationDelivery.user_id == user.id,
            ),
        )
        .where(_visible_to_user(user))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(page_size)
    ).all()
    return NotificationPage(
        items=[
            NotificationResponse(
                id=item.id,
                kind=item.kind,
                title=item.title,
                body=item.body,
                link=item.link,
                is_read=read_at is not None or item.is_read,
                created_at=item.created_at,
            )
            for item, read_at in rows
        ]
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationResponse,
)
def mark_notification_read(
    notification_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> NotificationResponse:
    item = session.scalar(
        select(Notification)
        .where(Notification.id == notification_id, _visible_to_user(user))
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    delivery = session.get(NotificationDelivery, (item.id, user.id))
    if delivery is None:
        delivery = NotificationDelivery(
            notification_id=item.id,
            user_id=user.id,
            read_at=datetime.now(timezone.utc),
        )
        session.add(delivery)
    elif delivery.read_at is None:
        delivery.read_at = datetime.now(timezone.utc)
    session.commit()
    return NotificationResponse(
        id=item.id,
        kind=item.kind,
        title=item.title,
        body=item.body,
        link=item.link,
        is_read=True,
        created_at=item.created_at,
    )
