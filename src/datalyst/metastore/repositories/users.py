import psycopg
from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import ConfigurationError, NotFoundError
from datalyst.core.models import Group, Role, User

SETUP_LOCK = 7_210_418_001
_COLUMNS = "id, email, name, role, active, token_version, created_at, last_login_at"


class UserRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(
        self, email: str, password_hash: str, name: str = "", role: Role = Role.ANALYST
    ) -> User:
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    f"""
                    INSERT INTO users (email, name, password_hash, role)
                    VALUES (%s, %s, %s, %s) RETURNING {_COLUMNS}
                    """,
                    (email.strip(), name.strip(), password_hash, role.value),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ConfigurationError(f"A user with email {email} exists.") from e
        return User(**row)

    def exist(self) -> bool:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT EXISTS (SELECT 1 FROM users) AS found"
            ).fetchone()["found"]

    def create_first_admin(
        self, email: str, password_hash: str, name: str = ""
    ) -> User:
        """Create the first admin; fails once any user exists, even under a race."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
            if conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS found").fetchone()[
                "found"
            ]:
                raise ConfigurationError("Datalyst is already set up; sign in instead.")
            row = conn.execute(
                f"""
                INSERT INTO users (email, name, password_hash, role)
                VALUES (%s, %s, %s, 'admin') RETURNING {_COLUMNS}
                """,
                (email.strip(), name.strip(), password_hash),
            ).fetchone()
        return User(**row)

    def get(self, user_id: int) -> User:
        return self._one("id = %s", user_id)

    def get_by_email(self, email: str) -> User:
        return self._one("lower(email) = lower(%s)", email.strip())

    def credentials(self, email: str) -> tuple[User, str] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS}, password_hash FROM users WHERE lower(email) = lower(%s)",
                (email.strip(),),
            ).fetchone()
        if row is None:
            return None
        password_hash = row.pop("password_hash")
        return User(**row), password_hash

    def all(self) -> list[User]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM users ORDER BY lower(email)"
            ).fetchall()
        return [User(**row) for row in rows]

    def count_admins(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM users WHERE role = 'admin' AND active"
            ).fetchone()
        return row["n"]

    def update(
        self,
        user_id: int,
        *,
        name: str | None = None,
        role: Role | None = None,
        active: bool | None = None,
    ) -> User:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""
                UPDATE users
                SET name = coalesce(%(name)s, name),
                    role = coalesce(%(role)s, role),
                    active = coalesce(%(active)s, active),
                    token_version = token_version
                        + CASE WHEN %(role)s IS NOT NULL AND %(role)s <> role
                                    OR %(active)s IS NOT NULL AND %(active)s <> active
                               THEN 1 ELSE 0 END
                WHERE id = %(id)s RETURNING {_COLUMNS}
                """,
                {
                    "name": name,
                    "role": role.value if role else None,
                    "active": active,
                    "id": user_id,
                },
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User {user_id} does not exist.")
        return User(**row)

    def set_password(self, user_id: int, password_hash: str) -> None:
        with self._pool.connection() as conn:
            updated = conn.execute(
                """
                UPDATE users SET password_hash = %s, token_version = token_version + 1
                WHERE id = %s
                """,
                (password_hash, user_id),
            ).rowcount
        if not updated:
            raise NotFoundError(f"User {user_id} does not exist.")

    def record_login(self, user_id: int, password_hash: str | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE users SET last_login_at = now(),
                       password_hash = coalesce(%s, password_hash)
                WHERE id = %s
                """,
                (password_hash, user_id),
            )

    def delete(self, user_id: int) -> None:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM users WHERE id = %s", (user_id,)
            ).rowcount
        if not deleted:
            raise NotFoundError(f"User {user_id} does not exist.")

    def _one(self, condition: str, value: object) -> User:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM users WHERE {condition}", (value,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User '{value}' does not exist.")
        return User(**row)


class GroupRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(self, name: str) -> Group:
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    "INSERT INTO groups (name) VALUES (%s) RETURNING id, name",
                    (name.strip(),),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ConfigurationError(f"A group named '{name}' exists.") from e
        return Group(**row)

    def all(self) -> list[Group]:
        with self._pool.connection() as conn:
            groups = conn.execute(
                "SELECT id, name FROM groups ORDER BY name"
            ).fetchall()
            members = conn.execute(
                "SELECT group_id, user_id FROM group_members"
            ).fetchall()
            grants = conn.execute(
                "SELECT group_id, source_id, table_patterns FROM source_grants"
            ).fetchall()
        result = {row["id"]: Group(**row) for row in groups}
        for row in members:
            result[row["group_id"]].members.append(row["user_id"])
        for row in grants:
            result[row["group_id"]].grants[row["source_id"]] = row["table_patterns"]
        return list(result.values())

    def get(self, group_id: int) -> Group:
        for group in self.all():
            if group.id == group_id:
                return group
        raise NotFoundError(f"Group {group_id} does not exist.")

    def get_by_name(self, name: str) -> Group:
        for group in self.all():
            if group.name == name:
                return group
        raise NotFoundError(f"Group '{name}' does not exist.")

    def delete(self, group_id: int) -> None:
        with self._pool.connection() as conn:
            if not conn.execute(
                "DELETE FROM groups WHERE id = %s", (group_id,)
            ).rowcount:
                raise NotFoundError(f"Group {group_id} does not exist.")

    def add_member(self, group_id: int, user_id: int) -> None:
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO group_members VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (group_id, user_id),
                )
        except psycopg.errors.ForeignKeyViolation as e:
            raise NotFoundError("That group or user does not exist.") from e

    def remove_member(self, group_id: int, user_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM group_members WHERE group_id = %s AND user_id = %s",
                (group_id, user_id),
            )

    def grant(
        self, group_id: int, source_id: int, table_patterns: list[str] | None
    ) -> None:
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO source_grants (group_id, source_id, table_patterns)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id, source_id) DO UPDATE
                    SET table_patterns = excluded.table_patterns
                    """,
                    (group_id, source_id, table_patterns),
                )
        except psycopg.errors.ForeignKeyViolation as e:
            raise NotFoundError("That group or data source does not exist.") from e

    def revoke(self, group_id: int, source_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM source_grants WHERE group_id = %s AND source_id = %s",
                (group_id, source_id),
            )

    def grants_for(self, user_id: int) -> list[tuple[int, list[str] | None]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT g.source_id, g.table_patterns
                FROM source_grants g
                JOIN group_members m ON m.group_id = g.group_id
                WHERE m.user_id = %s
                """,
                (user_id,),
            ).fetchall()
        return [(row["source_id"], row["table_patterns"]) for row in rows]
