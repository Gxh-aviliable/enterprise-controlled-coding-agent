"""Bounded durable stream cursor; text is recovered from owned chat history."""
import json
import time

from filelock import FileLock

from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.execution.changes import _atomic_json
from enterprise_agent.core.execution.evidence import _path

MAX_EVENTS = 512


def _event_path(user_id, trace_id):
    path = _path(user_id, trace_id, get_user_workspace(user_id)).with_suffix('.stream.json')
    if path.is_symlink():
        raise PermissionError('Unsafe stream event index')
    return path


def record_stream_event(user_id, payload):
    path = _event_path(user_id, payload['trace_id'])
    with FileLock(str(path) + '.lock'):
        log = json.loads(path.read_text()) if path.exists() else {'cursor': 0, 'events': []}
        sequence = log['cursor'] + 1
        # Never store partial token text or model/tool arguments here: credentials
        # can cross chunk boundaries. Durable chat/trace serializers own content.
        record = {key: payload[key] for key in ('trace_id', 'session_id', 'stream_fence', 'id', 'name', 'status')
                  if key in payload}
        record.update(seq=sequence, event='text_updated' if 'delta' in payload else payload.get('event', 'error'),
                      recorded_at=time.time())
        log['cursor'] = sequence
        log['events'] = (log['events'] + [record])[-MAX_EVENTS:]
        _atomic_json(path, log)
    return {**payload, 'seq': sequence}


def read_stream_events(user_id, trace_id, after=0):
    path = _event_path(user_id, trace_id)
    log = json.loads(path.read_text()) if path.exists() else {'cursor': 0, 'events': []}
    earliest = log['events'][0]['seq'] if log['events'] else 1
    return {'trace_id': trace_id, 'cursor': log['cursor'], 'gap': after < earliest - 1,
            'events': [e for e in log['events'] if e['seq'] > after],
            'history_required': True, 'replay_policy': 'Reload owned durable history and status; never resume tools.'}
