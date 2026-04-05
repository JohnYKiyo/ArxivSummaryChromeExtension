"""Server-Sent Events (SSE) service.

Provides SSE stream management for real-time progress reporting.
Handles event formatting, client connection tracking, and
broadcasting progress updates from the agent pipeline to connected clients.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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


class EventBus:
    """Per-job event distribution backed by ``asyncio.Queue``.

    Each call to :meth:`subscribe` creates a dedicated queue for the
    subscriber.  :meth:`publish` fans the event out to every queue
    registered for that job.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[SSEEvent | None]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, job_id: str) -> AsyncGenerator[SSEEvent, None]:
        """Yield events for *job_id* as they arrive.

        The generator terminates when a ``None`` sentinel is placed on the
        queue (see :meth:`remove`) or when the job's subscriber list is
        cleared.

        Args:
            job_id: The job to listen to.

        Yields:
            :class:`SSEEvent` instances in publication order.
        """
        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()

        async with self._lock:
            if job_id not in self._subscribers:
                self._subscribers[job_id] = []
            self._subscribers[job_id].append(queue)

        logger.debug("New subscriber for job %s", job_id)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                queues = self._subscribers.get(job_id, [])
                if queue in queues:
                    queues.remove(queue)
                    if not queues:
                        self._subscribers.pop(job_id, None)
            logger.debug("Subscriber removed for job %s", job_id)

    async def publish(self, job_id: str, event: SSEEvent) -> None:
        """Broadcast an event to all subscribers of *job_id*.

        If there are no active subscribers the event is silently dropped.

        Args:
            job_id: The job whose subscribers should receive the event.
            event: The event to publish.
        """
        async with self._lock:
            queues = list(self._subscribers.get(job_id, []))

        for queue in queues:
            await queue.put(event)

        logger.debug(
            "Published event to %d subscriber(s) for job %s: %s",
            len(queues),
            job_id,
            event.message,
        )

    async def remove(self, job_id: str) -> None:
        """Signal all subscribers that *job_id* is done and clean up.

        Sends a ``None`` sentinel to each subscriber queue so their
        :meth:`subscribe` generators terminate gracefully.

        Args:
            job_id: The job to remove.
        """
        async with self._lock:
            queues = self._subscribers.pop(job_id, [])

        for queue in queues:
            await queue.put(None)

        if queues:
            logger.info("Removed %d subscriber(s) for job %s", len(queues), job_id)
