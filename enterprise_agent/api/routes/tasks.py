"""Task-run detail, trace replay and aggregate metric endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.observability.trace_store import get_trace_store

router = APIRouter(prefix="/tasks", tags=["task-runs"])


def _read_trace(user_id: int, trace_id: str) -> dict:
    try:
        return get_trace_store().get_trace(user_id, trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task trace not found") from exc


@router.get("/metrics")
async def get_task_metrics(user_id: int = Depends(get_current_user)):
    """Return metrics computed only from this user's persisted terminal traces."""
    return get_trace_store().aggregate_metrics(user_id)


@router.get("")
async def list_task_runs(
    limit: int = Query(50, ge=1, le=500),
    user_id: int = Depends(get_current_user),
):
    """List recent task summaries without loading full event arrays."""
    return {"tasks": get_trace_store().list_traces(user_id, limit=limit)}


@router.get("/{trace_id}")
async def get_task_run(trace_id: str, user_id: int = Depends(get_current_user)):
    """Return one task summary and its aggregate counters."""
    trace = _read_trace(user_id, trace_id)
    summary = {key: value for key, value in trace.items() if key != "events"}
    summary["event_count"] = len(trace.get("events", []))
    return summary


@router.get("/{trace_id}/trace")
async def replay_task_trace(trace_id: str, user_id: int = Depends(get_current_user)):
    """Return the ordered, redacted event timeline for task replay."""
    return _read_trace(user_id, trace_id)
