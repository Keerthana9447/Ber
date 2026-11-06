from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.security import decode_token, permissions_satisfy
from app.db.session import get_db
from app.models import Role, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    payload = decode_token(request.app.state.settings, credentials.credentials, "access")
    if payload is None:
        raise unauthorized
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized from None
    user = session.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == user_id)
    )
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_permissions(*required: str) -> Callable:
    required_set = set(required)

    def permission_dependency(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        granted = {permission.code for permission in user.role.permissions}
        if not permissions_satisfy(granted, required_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return permission_dependency
