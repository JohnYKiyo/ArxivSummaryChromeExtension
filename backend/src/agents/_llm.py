"""Per-instance Gemini model wrapper.

ADK's default :class:`google.adk.models.Gemini` builds its underlying
``google.genai.Client`` lazily and the client reads ``GOOGLE_API_KEY``
from the process environment at request time. That is fine when one
global key serves every caller, but the Chrome extension now supplies
the user's own key per request — overwriting ``os.environ`` to inject
it would race between concurrent in-process pipeline runs.

:class:`ScopedKeyGemini` pins the key on the instance, so each pipeline
run gets its own ``genai.Client`` bound to the caller's key without
touching env state. When no key is supplied, the parent's env-based
behaviour is preserved (used by the React web UI which still relies on
the backend's ``.env``).
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from google.adk.models import Gemini
from google.genai import Client
from google.genai import types as genai_types
from pydantic import PrivateAttr


class ScopedKeyGemini(Gemini):
    """Gemini whose ``api_client`` carries an explicit per-instance API key.

    Mirrors :meth:`Gemini.api_client`'s ``HttpOptions`` construction so
    base-URL, headers, retry-options and Vertex/AI-Studio routing remain
    unchanged — the only difference is that an explicit ``api_key`` is
    threaded into ``google.genai.Client`` when supplied.
    """

    _api_key: str = PrivateAttr(default="")

    def __init__(self, *, api_key: str = "", **data: Any) -> None:
        super().__init__(**data)
        self._api_key = api_key

    @cached_property
    def api_client(self) -> Client:
        base_url, api_version = self._base_url_and_api_version
        http_kwargs: dict[str, Any] = {
            "headers": self._tracking_headers(),
            "retry_options": self.retry_options,
            "base_url": base_url,
        }
        if api_version:
            http_kwargs["api_version"] = api_version

        client_kwargs: dict[str, Any] = {
            "http_options": genai_types.HttpOptions(**http_kwargs),
        }
        if self.model.startswith("projects/"):
            client_kwargs["vertexai"] = True
        if self._api_key:
            # Explicit key — bypass env entirely so concurrent runs with
            # different keys do not collide on ``GOOGLE_API_KEY``.
            client_kwargs["api_key"] = self._api_key

        return Client(**client_kwargs)
