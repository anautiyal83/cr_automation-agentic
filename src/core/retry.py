"""Retry logic — test-failure retry + infrastructure retry per FR-007, FR-020, FR-045."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from src.core.models import RetryAttempt, RetryContext, RetryOutcome, RetryType

logger = logging.getLogger(__name__)

# Steps 3-6 are generation steps whose outputs feed downstream steps 7-9
GENERATION_STEPS = {3, 4, 5, 6}
DOWNSTREAM_STEPS = {7, 8, 9}


class RetryManager:
    """Manages both test-failure and infrastructure retries with smart cascading."""

    def __init__(
        self,
        test_retry_limit: int = 3,
        infra_retry_limit: int = 3,
        infra_backoff: float = 2.0,
    ) -> None:
        self.test_retry_limit = test_retry_limit
        self.infra_retry_limit = infra_retry_limit
        self.infra_backoff = infra_backoff

    async def execute_with_retry(
        self,
        step_number: int,
        execute_fn: Callable[..., Coroutine],
        *args: Any,
        on_retry: Callable | None = None,
        **kwargs: Any,
    ) -> dict:
        """Execute a step with retry logic.

        Returns dict with: success, result, attempts, needs_cascade
        """
        attempts: list[RetryAttempt] = []
        test_retries = 0
        infra_retries = 0
        last_error: dict[str, Any] = {}
        retry_context: RetryContext | None = None

        while True:
            try:
                result = await execute_fn(*args, retry_context=retry_context, **kwargs)

                if result.get("status") == "passed":
                    return {
                        "success": True,
                        "result": result,
                        "attempts": attempts,
                        "needs_cascade": False,
                    }

                # Test failure
                error = result.get("error", {})
                test_retries += 1

                attempt = RetryAttempt(
                    step_result_id=result.get("step_result_id", ""),
                    attempt_number=test_retries,
                    retry_type=RetryType.TEST_FAILURE,
                    error_context=error,
                    outcome=RetryOutcome.FAILURE,
                )
                attempts.append(attempt)
                last_error = error

                if test_retries >= self.test_retry_limit:
                    return {
                        "success": False,
                        "result": result,
                        "attempts": attempts,
                        "needs_cascade": False,
                        "error": f"Test retry limit ({self.test_retry_limit}) exhausted",
                    }

                # Build retry context for next attempt
                retry_context = RetryContext(
                    attempt_number=test_retries + 1,
                    previous_error=error,
                    previous_output=result.get("output_data", {}),
                )

                if on_retry:
                    await on_retry(step_number, test_retries, error)

                logger.info(
                    "Test failure retry %d/%d for step %d",
                    test_retries, self.test_retry_limit, step_number,
                )

            except InfrastructureError as exc:
                infra_retries += 1

                attempt = RetryAttempt(
                    step_result_id="",
                    attempt_number=infra_retries,
                    retry_type=RetryType.INFRASTRUCTURE_ERROR,
                    error_context={"message": str(exc)},
                    outcome=RetryOutcome.FAILURE,
                )
                attempts.append(attempt)

                if infra_retries >= self.infra_retry_limit:
                    return {
                        "success": False,
                        "result": None,
                        "attempts": attempts,
                        "needs_cascade": False,
                        "error": f"Infrastructure retry limit ({self.infra_retry_limit}) exhausted: {exc}",
                    }

                delay = self.infra_backoff ** infra_retries
                logger.info(
                    "Infrastructure retry %d/%d for step %d (delay %.1fs)",
                    infra_retries, self.infra_retry_limit, step_number, delay,
                )
                await asyncio.sleep(delay)

    def needs_cascade(self, step_number: int) -> bool:
        """Check if retrying this step requires re-running downstream steps."""
        return step_number in GENERATION_STEPS

    def get_cascade_steps(self, step_number: int) -> list[int]:
        """Get the downstream steps that need re-running after a generation step retry."""
        if step_number in GENERATION_STEPS:
            return sorted(s for s in DOWNSTREAM_STEPS if s > step_number)
        return []


class InfrastructureError(Exception):
    """Raised for LLM API failures (rate limits, timeouts, network errors)."""
    pass
