from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(10.0, read=300.0)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message

    @property
    def unauthorized(self) -> bool:
        return self.status == 401


class DatalystClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=DEFAULT_TIMEOUT, transport=transport
        )
        self.token = token

    def login(self, email: str, password: str) -> dict:
        data = self._call(
            "POST", "/api/auth/login", json={"email": email, "password": password}
        )
        self.token = data["access_token"]
        return data

    def me(self) -> dict:
        return self._call("GET", "/api/auth/me")

    def change_password(self, current: str, new: str) -> None:
        self._call(
            "POST",
            "/api/auth/password",
            json={"current_password": current, "new_password": new},
        )

    def sources(self) -> list[dict]:
        return self._call("GET", "/api/sources")

    def create_source(self, **fields: Any) -> dict:
        return self._call("POST", "/api/sources", json=fields)

    def update_source(self, source_id: int, **fields: Any) -> dict:
        return self._call("PATCH", f"/api/sources/{source_id}", json=fields)

    def delete_source(self, source_id: int) -> None:
        self._call("DELETE", f"/api/sources/{source_id}")

    def sync_source(self, source_id: int) -> dict:
        return self._call("POST", f"/api/sources/{source_id}/sync")

    def describe_source(self, source_id: int, force: bool = False) -> dict:
        return self._call(
            "POST", f"/api/sources/{source_id}/describe", params={"force": force}
        )

    def source_status(self, source_id: int) -> dict:
        return self._call("GET", f"/api/sources/{source_id}/status")

    def ask(self, source_id: int, question: str) -> dict:
        return self._call(
            "POST", f"/api/sources/{source_id}/ask", json={"question": question}
        )

    def run_sql(self, source_id: int, sql: str) -> dict:
        return self._call("POST", f"/api/sources/{source_id}/query", json={"sql": sql})

    def tables(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/tables")

    def table(self, source_id: int, table: str) -> dict:
        return self._call("GET", f"/api/sources/{source_id}/tables/{_path(table)}")

    def set_table_description(
        self, source_id: int, table: str, text: str | None
    ) -> None:
        self._call(
            "PUT",
            f"/api/sources/{source_id}/tables/{_path(table)}/description",
            json={"description": text or None},
        )

    def set_column_description(
        self, source_id: int, table: str, column: str, text: str | None
    ) -> None:
        self._call(
            "PUT",
            f"/api/sources/{source_id}/tables/{_path(table)}/columns/{_path(column)}/description",
            json={"description": text or None},
        )

    def stale(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/stale")

    def relationships(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/relationships")

    def set_relationship_status(
        self, source_id: int, relationship_id: int, status: str
    ) -> None:
        self._call(
            "PATCH",
            f"/api/sources/{source_id}/relationships/{relationship_id}",
            json={"status": status},
        )

    def add_relationship(self, source_id: int, child: str, parent: str) -> None:
        self._call(
            "POST",
            f"/api/sources/{source_id}/relationships",
            json={"child": child, "parent": parent},
        )

    def glossary(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/glossary")

    def save_term(
        self, source_id: int, term: str, definition: str, sql_hint: str | None
    ) -> dict:
        return self._call(
            "PUT",
            f"/api/sources/{source_id}/glossary/{_path(term)}",
            json={"definition": definition, "sql_hint": sql_hint or None},
        )

    def delete_term(self, source_id: int, term: str) -> None:
        self._call("DELETE", f"/api/sources/{source_id}/glossary/{_path(term)}")

    def examples(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/examples")

    def add_example(self, source_id: int, question: str, sql: str) -> dict:
        return self._call(
            "POST",
            f"/api/sources/{source_id}/examples",
            json={"question": question, "sql": sql},
        )

    def delete_example(self, source_id: int, example_id: int) -> None:
        self._call("DELETE", f"/api/sources/{source_id}/examples/{example_id}")

    def history(self, **params: Any) -> list[dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._call("GET", "/api/history", params=clean)

    def feedback(
        self, entry_id: int, value: int | None, note: str | None = None
    ) -> None:
        self._call(
            "POST",
            f"/api/history/{entry_id}/feedback",
            json={"value": value, "note": note},
        )

    def promote(self, entry_id: int) -> dict:
        return self._call("POST", f"/api/history/{entry_id}/promote")

    def users(self) -> list[dict]:
        return self._call("GET", "/api/users")

    def create_user(self, **fields: Any) -> dict:
        return self._call("POST", "/api/users", json=fields)

    def update_user(self, user_id: int, **fields: Any) -> dict:
        return self._call("PATCH", f"/api/users/{user_id}", json=fields)

    def delete_user(self, user_id: int) -> None:
        self._call("DELETE", f"/api/users/{user_id}")

    def groups(self) -> list[dict]:
        return self._call("GET", "/api/groups")

    def create_group(self, name: str) -> dict:
        return self._call("POST", "/api/groups", json={"name": name})

    def delete_group(self, group_id: int) -> None:
        self._call("DELETE", f"/api/groups/{group_id}")

    def add_member(self, group_id: int, user_id: int) -> None:
        self._call("PUT", f"/api/groups/{group_id}/members/{user_id}")

    def remove_member(self, group_id: int, user_id: int) -> None:
        self._call("DELETE", f"/api/groups/{group_id}/members/{user_id}")

    def grant(self, group_id: int, source_id: int, patterns: list[str] | None) -> None:
        self._call(
            "PUT",
            f"/api/groups/{group_id}/sources/{source_id}",
            json={"table_patterns": patterns or None},
        )

    def revoke(self, group_id: int, source_id: int) -> None:
        self._call("DELETE", f"/api/groups/{group_id}/sources/{source_id}")

    def usage(self, days: int = 30) -> list[dict]:
        return self._call("GET", "/api/usage", params={"days": days})

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise ApiError(0, f"Cannot reach the Datalyst API: {e}") from e
        if response.status_code >= 400:
            raise ApiError(response.status_code, _message(response))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):
            return body["error"].get("message", str(body))
        if "detail" in body:
            detail = body["detail"]
            if isinstance(detail, list):
                return "; ".join(str(d.get("msg", d)) for d in detail)
            return str(detail)
    return str(body)


def _path(part: str) -> str:
    return quote(part, safe="")
