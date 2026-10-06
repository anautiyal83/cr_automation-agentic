"""Human-in-the-Loop (HIL) handler per FR-027 through FR-030."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from src.core.events import PipelineEvent, event_bus
from src.core.models import HILRequest, HILStatus


class HILHandler:
    """Manages HIL requests — pauses pipeline and waits for user input indefinitely."""

    def __init__(self) -> None:
        self._pending_events: dict[UUID, asyncio.Event] = {}
        self._responses: dict[UUID, str] = {}

    async def request_hil(
        self,
        run_id: UUID,
        step_number: int,
        field_or_section: str,
        agent_inference: str,
        question: str,
        options: list[str] | None = None,
    ) -> HILRequest:
        """Create a HIL request and return it (does not wait yet)."""
        hil = HILRequest(
            run_id=run_id,
            step_number=step_number,
            field_or_section=field_or_section,
            agent_inference=agent_inference,
            question=question,
            options=options,
        )
        self._pending_events[hil.id] = asyncio.Event()
        return hil

    async def request_and_wait(
        self,
        run_id: UUID,
        step_number: int,
        field_or_section: str,
        agent_inference: str,
        question: str,
        options: list[str] | None = None,
    ) -> str:
        """Create a HIL request and wait indefinitely for user response."""
        hil = await self.request_hil(
            run_id, step_number, field_or_section,
            agent_inference, question, options,
        )

        await event_bus.publish(PipelineEvent(
            run_id=run_id,
            event_type="hil_requested",
            step_number=step_number,
            payload={
                "hil_id": str(hil.id),
                "field": field_or_section,
                "question": question,
                "inference": agent_inference,
                "options": options,
            },
        ))

        # Wait indefinitely per FR-030
        await self._pending_events[hil.id].wait()
        response = self._responses.pop(hil.id, "")
        del self._pending_events[hil.id]
        return response

    async def batch_request_and_wait(
        self,
        run_id: UUID,
        step_number: int,
        requests: list[dict],
    ) -> list[str]:
        """Batch multiple HIL requests and wait for all responses."""
        hil_items = []
        for req in requests:
            hil = await self.request_hil(
                run_id=run_id,
                step_number=step_number,
                field_or_section=req["field_or_section"],
                agent_inference=req["agent_inference"],
                question=req["question"],
                options=req.get("options"),
            )
            hil_items.append(hil)

        # Publish all HIL requests as a batch event
        await event_bus.publish(PipelineEvent(
            run_id=run_id,
            event_type="hil_requested",
            step_number=step_number,
            payload={
                "batch": True,
                "hil_requests": [
                    {
                        "hil_id": str(h.id),
                        "field": h.field_or_section,
                        "question": h.question,
                        "inference": h.agent_inference,
                        "options": h.options,
                    }
                    for h in hil_items
                ],
            },
        ))

        # Wait for all responses
        await asyncio.gather(
            *(self._pending_events[h.id].wait() for h in hil_items)
        )

        responses = []
        for h in hil_items:
            responses.append(self._responses.pop(h.id, ""))
            del self._pending_events[h.id]

        await event_bus.publish(PipelineEvent(
            run_id=run_id,
            event_type="hil_responded",
            step_number=step_number,
            payload={"responses_count": len(responses)},
        ))

        return responses

    async def respond(self, hil_id: UUID, response: str) -> None:
        """Provide a user response to a pending HIL request."""
        self._responses[hil_id] = response
        if hil_id in self._pending_events:
            self._pending_events[hil_id].set()

    def has_pending(self, run_id: UUID | None = None) -> bool:
        """Check if there are pending HIL requests."""
        return len(self._pending_events) > 0


hil_handler = HILHandler()
