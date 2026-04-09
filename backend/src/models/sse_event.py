"""SSE event domain model."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SSEEvent:
    """A single server-sent event payload.

    Attributes:
        event: The SSE event type (used as the ``event:`` field).
        step: Current pipeline step name.
        message: Human-readable progress message.
        progress: Completion percentage (0--100).
        data: Optional extra data attached to the event.
    """

    event: str
    step: str
    message: str
    progress: int
    data: dict[str, Any] | None = field(default=None)

    def to_sse_string(self) -> str:
        """Serialize to the wire format expected by an ``EventSource`` client.

        Returns:
            A string of the form ``event: <type>\\ndata: <json>\\n\\n``.
        """
        payload = asdict(self)
        return f"event: {self.event}\ndata: {json.dumps(payload)}\n\n"
