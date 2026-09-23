from enterprise_agent.core.execution.events import read_stream_events, record_stream_event


def test_durable_cursor_tenant_isolation_bounded_gap_and_no_partial_secrets(monkeypatch, tmp_path):
    from enterprise_agent.core.execution import events

    monkeypatch.setenv('WORKSPACE_BASE', str(tmp_path))
    monkeypatch.setattr(events, 'MAX_EVENTS', 2)
    payload = {'trace_id': 'event-test', 'session_id': 'synthetic', 'stream_fence': 1}
    assert record_stream_event(61, {**payload, 'delta': 'api_key=synthetic-secret'})['seq'] == 1
    record_stream_event(61, {**payload, 'event': 'tool_start', 'id': 'one', 'name': 'bash'})
    assert record_stream_event(61, {**payload, 'event': 'done'})['seq'] == 3
    view = read_stream_events(61, 'event-test', 0)
    assert view['gap'] and view['cursor'] == 3
    assert [e['seq'] for e in view['events']] == [2, 3]
    assert read_stream_events(61, 'event-test', 2)['events'][0]['seq'] == 3
    assert read_stream_events(62, 'event-test', 0)['events'] == []
    assert 'synthetic-secret' not in events._event_path(61, 'event-test').read_text()
