from typing import Any

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

    def setup_status(self) -> dict:
        return self._call("GET", "/api/auth/setup")

    def set_up(self, email: str, password: str, name: str) -> dict:
        data = self._call(
            "POST",
            "/api/auth/setup",
            json={"email": email, "password": password, "name": name},
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

    def upload_source(
        self, name: str, filename: str, content: bytes, llm_data_policy: str
    ) -> dict:
        return self._call(
            "POST",
            "/api/sources/upload",
            data={"name": name, "llm_data_policy": llm_data_policy},
            files={"file": (filename, content)},
        )

    def source_files(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/files")

    def add_source_file(
        self, source_id: int, filename: str, content: bytes
    ) -> list[dict]:
        return self._call(
            "POST",
            f"/api/sources/{source_id}/files",
            files={"file": (filename, content)},
        )

    def delete_source_file(self, source_id: int, table: str) -> None:
        self._call("DELETE", f"/api/sources/{source_id}/files/{table}")

    def update_source(self, source_id: int, **fields: Any) -> dict:
        return self._call("PATCH", f"/api/sources/{source_id}", json=fields)

    def delete_source(self, source_id: int) -> None:
        self._call("DELETE", f"/api/sources/{source_id}")

    def sync_source(self, source_id: int) -> dict:
        return self._call("POST", f"/api/sources/{source_id}/sync")

    def source_status(self, source_id: int) -> dict:
        return self._call("GET", f"/api/sources/{source_id}/status")

    def source_access(self, source_id: int) -> dict:
        return self._call("GET", f"/api/sources/{source_id}/access")

    def set_source_access(
        self, source_id: int, open_to_all: bool, user_ids: list[int]
    ) -> dict:
        return self._call(
            "PUT",
            f"/api/sources/{source_id}/access",
            json={"open_to_all": open_to_all, "user_ids": user_ids},
        )

    def ask(
        self, source_id: int, question: str, conversation_id: int | None = None
    ) -> dict:
        return self._call(
            "POST",
            f"/api/sources/{source_id}/ask",
            json={"question": question, "conversation_id": conversation_id},
        )

    def conversations(self, source_id: int) -> list[dict]:
        return self._call("GET", "/api/conversations", params={"source_id": source_id})

    def conversation(self, conversation_id: int) -> dict:
        return self._call("GET", f"/api/conversations/{conversation_id}")

    def delete_conversation(self, conversation_id: int) -> None:
        self._call("DELETE", f"/api/conversations/{conversation_id}")

    def memories(self) -> list[dict]:
        return self._call("GET", "/api/memories")

    def add_memory(self, text: str, source_id: int | None = None) -> dict:
        return self._call(
            "POST", "/api/memories", json={"text": text, "source_id": source_id}
        )

    def delete_memory(self, memory_id: int) -> None:
        self._call("DELETE", f"/api/memories/{memory_id}")

    def clear_memories(self) -> None:
        self._call("DELETE", "/api/memories")

    def run_sql(self, source_id: int, sql: str) -> dict:
        return self._call("POST", f"/api/sources/{source_id}/query", json={"sql": sql})

    def examples(self, source_id: int) -> list[dict]:
        return self._call("GET", f"/api/sources/{source_id}/examples")

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

    def settings(self) -> list[dict]:
        return self._call("GET", "/api/settings")

    def update_settings(
        self, values: dict[str, Any], reset: list[str] | None = None
    ) -> list[dict]:
        return self._call(
            "PUT", "/api/settings", json={"values": values, "reset": reset or []}
        )

    def export_settings(self, fmt: str = "ini") -> str:
        return self._call(
            "GET", "/api/settings/export", params={"format": fmt}, text=True
        )

    def test_settings(self, target: str) -> dict:
        return self._call("POST", "/api/settings/test", json={"target": target})

    def _call(self, method: str, path: str, text: bool = False, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as e:
            raise ApiError(0, "The server took too long to answer. Try again.") from e
        except httpx.HTTPError as e:
            raise ApiError(
                0, "Cannot reach the Datalyst server. Try again in a moment."
            ) from e
        if response.status_code >= 400:
            raise ApiError(response.status_code, _message(response))
        if text:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


FALLBACK_MESSAGE = "The server could not complete the request. Try again."


def _message(response: httpx.Response) -> str:
    """The server's short explanation; never raw bodies such as proxy error pages."""
    try:
        body = response.json()
    except ValueError:
        return FALLBACK_MESSAGE
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return FALLBACK_MESSAGE
