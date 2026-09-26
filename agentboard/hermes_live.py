"""Read-only live Hermes status and outstanding requests for the overlay."""
from __future__ import annotations

import copy
import threading
import time

from . import backend


def enrich(data: dict, sessions: list[dict]) -> dict:
    """Only real outstanding requests may become attention cards."""
    data = copy.deepcopy(data)
    live = {str(item.get('session_key')): item for item in sessions}
    for row in data.get('sessions', []):
        if row.get('provider') != 'hermes' or row.get('host', 'local') != 'local':
            continue
        attached = live.get(row['id'])
        # A question mark in an old assistant reply is not an outstanding request.
        if row.get('status') == 'needs':
            row.update(status='reply', status_label='Last reply')
        if not attached:
            continue
        status = attached.get('status')
        if status in {'working', 'starting'}:
            row.update(status='working', status_label='Working')
        elif status == 'waiting':
            row.update(status='waiting', status_label='Waiting')
        elif status == 'idle' and row.get('status') in {'working', 'waiting'}:
            row.update(status='finished', status_label='Finished')
        for request in attached.get('open_requests', []):
            params = request.get('params') or {}
            if request.get('method') == 'approval' and not row.get('approval_request_id'):
                row.update(approval_request_id=request['id'],
                           approval_summary=str(params.get('command') or params.get('description') or 'Approval requested'),
                           status='needs', status_label='Approval requested')
                row['capabilities'] = [*row.get('capabilities', []), 'approve', 'decline']
            elif request.get('method') == 'clarify' and not row.get('user_input_request_id'):
                questions = [dict(question, id=question.get('qid') or question.get('id') or 'answer',
                                  options=question.get('choices') or question.get('options') or [])
                             for question in (params.get('questions') or [dict(params, id='answer')])]
                row.update(user_input_request_id=request['id'],
                           user_input_questions=questions,
                           user_input_summary=str(questions[0].get('question') or 'Answer requested'),
                           status='needs', status_label='Answer requested')
                row['capabilities'] = [*row.get('capabilities', []), 'answer']
    return data


class Monitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sessions = []
        self._updated = 0.0
        self._thread = threading.Thread(target=self._poll, daemon=True, name='hermes-live-status')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def enrich(self, data):
        with self._lock:
            sessions = self._sessions if time.monotonic() - self._updated < 10 else []
            return enrich(data, sessions)

    def _poll(self):
        while not self._stop.is_set():
            sessions = []
            try:
                target = backend.find_backend()
                if target:
                    sessions = backend.call('session.active_list', {}, backend=target, timeout=3).get('sessions', [])
                    for session in sessions:
                        if session.get('status') == 'waiting':
                            snapshot = backend.call('session.events.since', {
                                'session_id': session['id'], 'last_seen': 9007199254740991,
                            }, backend=target, timeout=3)
                            session['open_requests'] = snapshot.get('open_requests', [])
            except Exception:
                sessions = []
            with self._lock:
                self._sessions = sessions
                self._updated = time.monotonic()
            self._stop.wait(2)


def respond(action):
    target = backend.find_backend()
    if not target:
        raise RuntimeError('Hermes desktop backend is unavailable; request was not answered')
    sid = backend._runtime_session_id(action['session_id'], backend=target)
    snapshot = backend.call('session.events.since', {'session_id': sid, 'last_seen': 9007199254740991}, backend=target)
    request_id = action.get('request_id') or action.get('text')
    request = next((item for item in snapshot.get('open_requests', []) if item['id'] == request_id), None)
    if not request:
        raise RuntimeError('This request has already been answered or expired')
    if action['kind'] in {'approve', 'decline'}:
        if request['method'] != 'approval':
            raise RuntimeError('This is not an approval request')
        result = {'choice': 'once' if action['kind'] == 'approve' else 'deny'}
    else:
        if request['method'] != 'clarify':
            raise RuntimeError('This is not a clarification request')
        answers = action.get('answers')
        if not isinstance(answers, dict) or not answers:
            raise RuntimeError('Answer each question before sending')
        params = request.get('params') or {}
        expected = [question.get('qid') or question.get('id') for question in params.get('questions', [])] or ['answer']
        if any(not isinstance(answers.get(key), str) or not answers[key].strip() for key in expected):
            raise RuntimeError('Answer each question before sending')
        result = {'answers': answers} if params.get('questions') else {'answer': answers['answer']}
    response = backend.call('request.answer', {'id': request_id, 'result': result}, backend=target)
    if response.get('status') != 'ok':
        raise RuntimeError('The request expired before the answer arrived')
    return {'ok': True, 'message': 'Answered'}
