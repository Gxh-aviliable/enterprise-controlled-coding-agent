from enterprise_agent.sandbox.preflight import dependency_diagnostic, requirements


def test_supported_runtime_dependencies_and_untrusted_failure_diagnostics():
    assert requirements("cd src && python -B -m pytest -q") == {"python", "module:pytest"}
    assert requirements("python -m ruff check .") == {"python", "module:ruff"}
    assert requirements("npm test") == {"npm", "node"}
    assert requirements("echo pytest") == set()
    result = {"exit_code": 1, "stderr": "ModuleNotFoundError: No module named 'synthetic_missing_package'"}
    assert dependency_diagnostic(result)["name"] == "synthetic_missing_package"
    assert dependency_diagnostic({**result, "exit_code": 0}) is None
    assert dependency_diagnostic({**result, "error_code": "task_cancelled"}) is None
