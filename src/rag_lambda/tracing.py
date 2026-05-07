"""Trace event helpers for RAG graph execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import uuid
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return a UTC timestamp string suitable for logs and SQL casts."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceEvent:
    """Structured backend trace event with an optional sanitized frontend view."""

    trace_id: str
    conversation_id: str
    turn_index: int
    step_index: int
    step_name: str
    status: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    latency_ms: Optional[int] = None
    model_id: Optional[str] = None
    input_summary: Optional[Dict[str, Any]] = None
    output_summary: Optional[Dict[str, Any]] = None
    input_payload: Optional[Dict[str, Any]] = None
    output_payload: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    frontend_event: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        """Return a JSON-serializable persistence record."""
        return {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "turn_index": self.turn_index,
            "step_index": self.step_index,
            "step_name": self.step_name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "latency_ms": self.latency_ms,
            "model_id": self.model_id,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "error": self.error,
            "frontend_event": self.frontend_event,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class StepRun:
    """Started step timing context."""

    step_index: int
    step_name: str
    started_at: str
    started_monotonic: float


class TraceCollector:
    """
    In-memory trace collector for synchronous responses.

    The same event objects can later be sent to a streaming sink as each step
    completes, while synchronous clients continue receiving the final list.
    """

    def __init__(self, *, trace_id: str, conversation_id: str, turn_index: int):
        self.trace_id = trace_id
        self.conversation_id = conversation_id
        self.turn_index = turn_index
        self._next_step_index = 1
        self.events: List[TraceEvent] = []

    def start_step(self, step_name: str) -> StepRun:
        """Reserve a step index and capture start timing."""
        step = StepRun(
            step_index=self._next_step_index,
            step_name=step_name,
            started_at=utc_now_iso(),
            started_monotonic=time.perf_counter(),
        )
        self._next_step_index += 1
        return step

    def complete_step(
        self,
        step: StepRun,
        *,
        label: str,
        detail: Optional[str] = None,
        items: Optional[List[str]] = None,
        model_id: Optional[str] = None,
        input_summary: Optional[Dict[str, Any]] = None,
        output_summary: Optional[Dict[str, Any]] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        frontend_metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Record a completed step with backend and frontend-safe payloads."""
        completed_at = utc_now_iso()
        latency_ms = max(0, int((time.perf_counter() - step.started_monotonic) * 1000))
        frontend_event = {
            "label": label,
            "status": "completed",
        }
        if detail:
            frontend_event["detail"] = detail
        if items:
            frontend_event["items"] = items
        if frontend_metadata:
            frontend_event["metadata"] = frontend_metadata

        event = TraceEvent(
            trace_id=self.trace_id,
            conversation_id=self.conversation_id,
            turn_index=self.turn_index,
            step_index=step.step_index,
            step_name=step.step_name,
            status="completed",
            started_at=step.started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            model_id=model_id,
            input_summary=input_summary,
            output_summary=output_summary,
            input_payload=input_payload,
            output_payload=output_payload,
            frontend_event=frontend_event,
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def fail_step(
        self,
        step: StepRun,
        *,
        label: str,
        detail: str,
        error_code: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Record a failed step with redacted error details."""
        completed_at = utc_now_iso()
        latency_ms = max(0, int((time.perf_counter() - step.started_monotonic) * 1000))
        event = TraceEvent(
            trace_id=self.trace_id,
            conversation_id=self.conversation_id,
            turn_index=self.turn_index,
            step_index=step.step_index,
            step_name=step.step_name,
            status="failed",
            started_at=step.started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            error={"code": error_code, "detail": detail},
            frontend_event={
                "label": label,
                "status": "failed",
                "detail": detail,
            },
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def frontend_steps(self) -> List[Dict[str, Any]]:
        """Return the sanitized step list for API responses."""
        return [event.frontend_event for event in self.events if event.frontend_event]


def get_trace_collector(config: Optional[Dict[str, Any]]) -> Optional[TraceCollector]:
    """Extract the trace collector from a LangGraph runnable config."""
    if not config:
        return None
    configurable = config.get("configurable", {})
    collector = configurable.get("trace_collector")
    return collector if isinstance(collector, TraceCollector) else None
