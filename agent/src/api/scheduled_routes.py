"""Scheduled research HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_scheduled_routes(app, ...)``.
"""

from __future__ import annotations

import logging
import os
import sys as _sys
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEDULED_RESEARCH_SCHEDULER_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
_SCHEDULED_RESEARCH_TRUE_VALUES = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_scheduled_research_store: Any = None
_scheduled_research_executor: Any = None


def _scheduled_research_scheduler_enabled() -> bool:
    """Return whether scheduled research execution is enabled."""
    return get_env_config().agent_tuning.vibe_trading_enable_scheduler


def _get_scheduled_research_store():
    """Return the singleton ScheduledResearchJobStore, creating it on first call."""
    global _scheduled_research_store
    if _scheduled_research_store is None:
        from src.scheduled_research.store import ScheduledResearchJobStore

        _scheduled_research_store = ScheduledResearchJobStore()
    return _scheduled_research_store


async def _dispatch_scheduled_research_job(job) -> Dict[str, str]:
    """Enqueue one scheduled research job through the session runtime.

    ``send_message`` queues the agent attempt and returns once accepted; it
    does not wait for that agent run to reach a terminal status. The executor's
    ``COMPLETED`` state for this dispatch path means "successfully enqueued."

    Returns dict with session_id and attempt_id for receipt tracking.
    """
    from src.core.invocation import InvocationContext

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    svc = host._get_session_service()
    if not svc:
        raise RuntimeError("Session runtime not enabled")
    # Pass a copy so the session runtime's internal config writes (e.g.
    # include_shell_tools) do not mutate the persisted scheduled-run config.
    session = svc.create_session(
        title=f"scheduled-research:{job.id}", config=dict(job.config)
    )
    now_ms = int(time.time() * 1000)
    invocation_ctx = InvocationContext(
        source="scheduled_research",
        trigger_id=job.id,
        scheduled_for=job.next_run_at,
        triggered_at=now_ms,
        schedule=job.schedule,
        unattended=True,
        research_only=True,
    )
    logger.info(
        "dispatching scheduled research job %s via session %s",
        job.id,
        session.session_id,
    )
    result = await svc.send_message(
        session.session_id, job.prompt,
        invocation_context=invocation_ctx,
    )
    return {"session_id": session.session_id, "attempt_id": result.get("attempt_id", "")}


def _get_scheduled_research_executor():
    """Return the singleton scheduled research executor."""
    global _scheduled_research_executor
    if _scheduled_research_executor is None:
        from src.scheduled_research.executor import ScheduledResearchExecutor

        _scheduled_research_executor = ScheduledResearchExecutor(
            _get_scheduled_research_store(),
            _dispatch_scheduled_research_job,
            enabled=_scheduled_research_scheduler_enabled(),
        )
    return _scheduled_research_executor


def _start_scheduled_research_executor() -> None:
    """Start scheduled research execution when explicitly enabled."""
    if not _scheduled_research_scheduler_enabled():
        return
    _get_scheduled_research_executor().start()


async def _stop_scheduled_research_executor() -> None:
    """Stop scheduled research execution if it was started."""
    executor = _scheduled_research_executor
    if executor is not None:
        await executor.stop()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateScheduledRunRequest(BaseModel):
    """Request body for POST /scheduled-runs."""

    id: Optional[str] = Field(
        None, description="Job id; auto-generated UUID when omitted"
    )
    prompt: str = Field(
        ..., min_length=1, description="Research prompt or backtest description"
    )
    schedule: str = Field(
        ..., min_length=1, description="Interval-ms or 5-field cron expression"
    )
    next_run_at: Optional[int] = Field(
        None, description="Epoch-ms for next run; defaults to now"
    )
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Optional backtest parameters"
    )


class ScheduledRunResponse(BaseModel):
    """API response for a single scheduled job."""

    id: str
    prompt: str
    schedule: str
    next_run_at: int
    status: str
    created_at: int
    config: Dict[str, Any] = Field(default_factory=dict)
    execution_enabled: bool = Field(
        default=True,
        description="Whether the scheduler is currently enabled to execute this job",
    )


class SchedulerStatusResponse(BaseModel):
    """API response for GET /scheduled-runs/status."""

    enabled: bool = Field(description="Whether the scheduler feature is enabled")
    running: bool = Field(description="Whether the background loop is active")
    session_runtime_enabled: bool = Field(
        description="Whether the session runtime is available for dispatch"
    )
    last_tick_at: Optional[int] = Field(
        None, description="Epoch-ms of the most recent tick"
    )
    last_error: Optional[str] = Field(
        None, description="Most recent error message, if any"
    )
    tick_interval_ms: int = Field(description="Poll interval in milliseconds")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_scheduled_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the scheduled routes onto ``app``.

    Resolves ``require_auth`` from the host ``api_server`` module via
    ``sys.modules`` when not passed explicitly.
    """
    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")

    if host is None:
        raise RuntimeError(
            "register_scheduled_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    # --- Routes ---

    @app.post(
        "/scheduled-runs",
        response_model=ScheduledRunResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_auth)],
    )
    async def create_scheduled_run(
        request: CreateScheduledRunRequest,
    ) -> ScheduledRunResponse:
        """Create (or replace) a scheduled research job.

        The job is persisted immediately.  When the scheduler executor is
        disabled the job is still stored but the response includes
        ``execution_enabled=false`` — the caller MUST check this field to
        know whether the job will actually run.
        """
        from src.scheduled_research.models import (
            JobStatus,
            ScheduledResearchJob,
            validate_schedule,
        )

        try:
            validate_schedule(request.schedule)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        now_ms = int(time.time() * 1000)
        executor_enabled = _scheduled_research_scheduler_enabled()
        job = ScheduledResearchJob(
            id=request.id or str(uuid.uuid4()),
            prompt=request.prompt,
            schedule=request.schedule,
            next_run_at=request.next_run_at if request.next_run_at is not None else now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config=request.config,
        )
        _get_scheduled_research_store().upsert(job)

        if not executor_enabled:
            logger.warning(
                "scheduled research job %s created but executor is disabled; "
                "set VIBE_TRADING_ENABLE_SCHEDULER=1 to enable execution",
                job.id,
            )
        else:
            # Wake the executor so a near-term job does not wait for a full tick.
            _get_scheduled_research_executor().wake()

        return ScheduledRunResponse(
            **job.to_dict(), execution_enabled=executor_enabled
        )

    @app.get(
        "/scheduled-runs",
        response_model=List[ScheduledRunResponse],
        dependencies=[Depends(require_auth)],
    )
    async def list_scheduled_runs(
        status_filter: Optional[str] = Query(None, alias="status"),
        limit: int = Query(50, ge=1, le=200),
    ) -> List[ScheduledRunResponse]:
        """List scheduled research jobs, optionally filtered by status."""
        jobs = _get_scheduled_research_store().list_jobs(
            status=status_filter, limit=limit
        )
        executor_enabled = _scheduled_research_scheduler_enabled()
        return [
            ScheduledRunResponse(**j.to_dict(), execution_enabled=executor_enabled)
            for j in jobs
        ]

    @app.get(
        "/scheduled-runs/status",
        response_model=SchedulerStatusResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_scheduler_status() -> SchedulerStatusResponse:
        """Return the current status of the scheduled research executor.

        Use this endpoint to verify whether the scheduler is enabled, running,
        and has a usable session runtime before creating scheduled jobs.
        """
        executor = _get_scheduled_research_executor()
        return SchedulerStatusResponse(
            enabled=executor.enabled,
            running=executor.is_running,
            session_runtime_enabled=executor.session_runtime_available,
            last_tick_at=executor.last_tick_at,
            last_error=executor.last_error,
            tick_interval_ms=executor._tick_interval_ms,
        )

    @app.delete(
        "/scheduled-runs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_auth)],
    )
    async def delete_scheduled_run(job_id: str) -> None:
        """Cancel (delete) a scheduled research job by id."""
        _host_validate_path_param(job_id, "job_id")
        removed = _get_scheduled_research_store().delete(job_id)
        if not removed:
            raise HTTPException(
                status_code=404, detail=f"scheduled run {job_id} not found"
            )
