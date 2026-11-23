from datetime import timedelta

from fastapi import APIRouter

from datalyst.api.deps import Admin, Services
from datalyst.api.schemas import GrantUpdate, GroupCreate, UserCreate, UserUpdate
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.models import Group, Role, User
from datalyst.security import hash_password

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/users")
def list_users(_: Admin, app: Services) -> list[User]:
    return app.metastore.users.all()


@router.post("/users", status_code=201)
def create_user(body: UserCreate, _: Admin, app: Services) -> User:
    return app.auth.create_user(body.email, body.password, body.name, body.role)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: Admin, app: Services) -> User:
    target = app.metastore.users.get(user_id)
    losing_admin = (
        target.role == Role.ADMIN
        and target.active
        and (body.role == Role.ANALYST or body.active is False)
    )
    if losing_admin and app.metastore.users.count_admins() <= 1:
        raise ConfigurationError("Keep at least one active admin.")
    if body.password:
        app.metastore.users.set_password(user_id, hash_password(body.password))
    return app.metastore.users.update(
        user_id, name=body.name, role=body.role, active=body.active
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: Admin, app: Services) -> None:
    target = app.metastore.users.get(user_id)
    if target.id == admin.user.id:
        raise ConfigurationError("You cannot delete your own account.")
    if target.role == Role.ADMIN and app.metastore.users.count_admins() <= 1:
        raise ConfigurationError("Keep at least one active admin.")
    app.metastore.users.delete(user_id)


@router.get("/groups")
def list_groups(_: Admin, app: Services) -> list[Group]:
    return app.metastore.groups.all()


@router.post("/groups", status_code=201)
def create_group(body: GroupCreate, _: Admin, app: Services) -> Group:
    return app.metastore.groups.create(body.name)


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: int, _: Admin, app: Services) -> None:
    app.metastore.groups.delete(group_id)


@router.put("/groups/{group_id}/members/{user_id}", status_code=204)
def add_member(group_id: int, user_id: int, _: Admin, app: Services) -> None:
    app.metastore.groups.add_member(group_id, user_id)


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
def remove_member(group_id: int, user_id: int, _: Admin, app: Services) -> None:
    app.metastore.groups.remove_member(group_id, user_id)


@router.put("/groups/{group_id}/sources/{source_id}", status_code=204)
def grant(
    group_id: int, source_id: int, body: GrantUpdate, _: Admin, app: Services
) -> None:
    patterns = [p.strip() for p in body.table_patterns or [] if p.strip()] or None
    app.metastore.groups.grant(group_id, source_id, patterns)


@router.delete("/groups/{group_id}/sources/{source_id}", status_code=204)
def revoke(group_id: int, source_id: int, _: Admin, app: Services) -> None:
    app.metastore.groups.revoke(group_id, source_id)


@router.get("/usage")
def usage(
    _: Admin, app: Services, source_id: int | None = None, days: int = 30
) -> list[dict]:
    return app.metastore.usage.summary(source_id, timedelta(days=days))
