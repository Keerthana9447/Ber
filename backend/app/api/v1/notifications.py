from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models import Notification, User

router = APIRouter(tags=["notifications"])


class NotificationResponse(BaseModel):
    id: int
    title: str
    body: str | None
    is_read: bool


class NotificationPage(BaseModel):
    items: list[NotificationResponse]


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db)],
    page_size: int = Query(default=10, ge=1, le=50),
) -> NotificationPage:
    visible_to_user = or_(
        Notification.user_id == user.id,
        Notification.role_id == user.role_id,
        and_(Notification.user_id.is_(None), Notification.role_id.is_(None)),
    )
    items = session.scalars(
        select(Notification)
        .where(visible_to_user)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(page_size)
    ).all()
    return NotificationPage(items=[NotificationResponse.model_validate(item) for item in items])
