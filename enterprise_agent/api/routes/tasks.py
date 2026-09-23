"""Task-run detail, trace replay and aggregate metric endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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


@router.get("/{trace_id}/changes")
def get_task_changes(trace_id: str, user_id: int = Depends(get_current_user)):
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace
    from enterprise_agent.core.execution.changes import task_changes

    _read_trace(user_id, trace_id)
    return task_changes(get_user_workspace(user_id), user_id, trace_id)


@router.get("/{trace_id}/events")
def get_task_events(trace_id: str, after: int = Query(0, ge=0), user_id: int = Depends(get_current_user)):
    from enterprise_agent.core.execution.events import read_stream_events

    _read_trace(user_id, trace_id)
    return read_stream_events(user_id, trace_id, after)


class RestoreRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/{trace_id}/restore")
def restore_task_files(trace_id: str, payload: RestoreRequest, user_id: int = Depends(get_current_user)):
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace
    from enterprise_agent.core.execution.changes import RestoreConflictError, restore_changes

    trace = _read_trace(user_id, trace_id)
    if trace.get('storage_trust') == 'legacy_unverified':
        raise HTTPException(status_code=409, detail='Legacy user-writable trace cannot authorize file restore')
    if trace['status'] not in {'succeeded', 'failed', 'cancelled'}:
        raise HTTPException(status_code=409, detail='Task must be terminal before restoring files')
    try:
        return restore_changes(get_user_workspace(user_id), user_id, trace_id, payload.paths, payload.expected_version)
    except (RestoreConflictError, TimeoutError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
