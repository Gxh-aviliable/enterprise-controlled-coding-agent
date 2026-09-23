import pytest

from enterprise_agent.core.agent.tools.contracts import normalize_tool_result


@pytest.mark.parametrize("code", ["sandbox_unavailable", "sandbox_cleanup_failed", "task_cancelled", "tool_timeout"])
def test_executor_error_code_survives_tool_normalization(code):
    record = normalize_tool_result(
        tool_name="bash",
        tool_call_id="test",
        duration_ms=1,
        attempt_count=1,
        raw_result={"stdout": "", "stderr": "", "exit_code": -1, "error_code": code},
    )
    assert record.error_code == code
    assert not record.ok
    assert record.status.value == ("timeout" if code == "tool_timeout" else "error")


def test_shutdown_waits_for_final_execution_receipt(monkeypatch, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from enterprise_agent.core.agent.tools.workspace import get_user_workspace
    from enterprise_agent.sandbox import executor

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = get_user_workspace(981)
    reached = threading.Event()
    release = threading.Event()
    shutdown_done = threading.Event()

    class FakeExecutor:
        def run(self, request, cancelled):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(executor, "get_executor", lambda: FakeExecutor())
    original = executor.finish_receipt

    def delayed(**kwargs):
        reached.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(executor, "finish_receipt", delayed)

    def shutdown():
        executor.shutdown_executions(timeout=5)
        shutdown_done.set()

    with ThreadPoolExecutor() as pool:
        execution = pool.submit(
            executor.execute, executor.ExecutionRequest("pwd", root, 981, "receipt-shutdown"), lambda: False
        )
        assert reached.wait(5)
        stopping = pool.submit(shutdown)
        assert not shutdown_done.wait(0.1)
        release.set()
        execution.result(timeout=5)
        stopping.result(timeout=5)
        assert shutdown_done.is_set()
