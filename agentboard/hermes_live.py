"""Read-only live Hermes status and outstanding requests for the overlay."""
from __future__ import annotations

import copy
import threading
import time

from . import backend


NO_EVENTS = 9007199254740991  # a watermark past any event: open requests only
STREAM_KEEP = 4  # finished provisional replies kept until the store has them


class Stream:
    """One working session's reply text, rebuilt from the backend's event replay.

    The session store only receives assistant text after a tool round or at the
    end of a turn, so while a turn runs the only way to show the reply growing is
    the backend's per-token events. Reading the replay buffer never attaches to
    the session, so the desktop app keeps sole ownership of it.
    """

    def __init__(self):
        self.last_seen = 0
        self.epoch = None
        self.done: list[tuple[int, str]] = []
        self.current = ''
        self.serial = 0

    def apply(self, snapshot: dict) -> None:
        epoch = snapshot.get('epoch')
        if self.epoch is not None and epoch != self.epoch:
            self.__init__()
        self.epoch = epoch
        for event in snapshot.get('events') or []:
            if int(event.get('seq') or snapshot.get('latest_seq') or 0) <= self.last_seen:
                continue
            kind = event.get('type')
            payload = event.get('payload') or {}
            if kind == 'message.start':
                self._finish(self.current)
            elif kind == 'message.delta':
                self.current += str(payload.get('text') or '')
            elif kind in {'message.interim', 'message.complete'}:
                text = str(payload.get('text') or '')
                already = kind == 'message.interim' and payload.get('already_streamed')
                self._finish(self.current if already or not text else text)
        try:
            self.last_seen = max(self.last_seen, int(snapshot.get('latest_seq') or 0))
        except (TypeError, ValueError):
            pass

    def _finish(self, text: str) -> None:
        if text.strip():
            self.done = [*self.done, (self.serial, text)][-STREAM_KEEP:]
            self.serial += 1
        self.current = ''

    def messages(self) -> list[dict]:
        rows = [{'id': f'stream-{self.epoch}-{serial}', 'role': 'agent', 'text': text} for serial, text in self.done]
        if self.current.strip():
            rows.append({'id': f'stream-{self.epoch}-{self.serial}', 'role': 'agent', 'text': self.current, 'streaming': True})
        return rows


def _merge_stream(conversation: list[dict], provisional: list[dict]) -> list[dict]:
    """Append streamed text the store does not hold yet, without duplicates."""
    stored = {str(message.get('text') or '').strip() for message in conversation[-8:] if message.get('role') == 'agent'}
    extra = []
    for index, message in enumerate(provisional):
        text = str(message.get('text') or '')
        if text.strip() in stored:
            continue
        extra.append(dict(message, id=message.get('id') or f'streaming-{index}', created_at=''))
    return [*conversation, *extra]


def enrich(data: dict, sessions: list[dict], streams: dict | None = None) -> dict:
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
        stream = (streams or {}).get(row['id'])
        if stream is not None:
            row['conversation'] = _merge_stream(list(row.get('conversation') or []), stream.messages())
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
        self._streams: dict[str, Stream] = {}
        self._updated = 0.0
        self._thread = threading.Thread(target=self._poll, daemon=True, name='hermes-live-status')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def session_ids(self):
        with self._lock:
            return tuple(str(row['session_key']) for row in self._sessions if row.get('session_key'))

    def enrich(self, data):
        with self._lock:
            fresh = time.monotonic() - self._updated < 10
            return enrich(data, self._sessions if fresh else [], self._streams if fresh else {})

    def _poll(self):
        connection = backend.ReadConnection()
        target = None
        next_discovery = 0.0
        while not self._stop.is_set():
            sessions = []
            streams = dict(self._streams)
            try:
                if time.monotonic() >= next_discovery:
                    target = backend.find_backend()
                    next_discovery = time.monotonic() + 10
                if target:
                    sessions = connection.call('session.active_list', {}, backend=target, timeout=3).get('sessions', [])
                    live = set()
                    for session in sessions:
                        status = session.get('status')
                        key = str(session.get('session_key') or '')
                        if status in {'working', 'starting', 'waiting'} and key:
                            live.add(key)
                            stream = streams.setdefault(key, Stream())
                            try:
                                snapshot = connection.call('session.events.since', {
                                    'session_id': session['id'], 'last_seen': stream.last_seen,
                                }, backend=target, timeout=1)
                                stream.apply(snapshot)
                                session['open_requests'] = snapshot.get('open_requests', [])
                            except Exception:
                                # A bad session must not erase every other live row.
                                continue
                    # A finished turn's text is in the store now; stop carrying it.
                    streams = {key: stream for key, stream in streams.items() if key in live}
            except Exception:
                connection.close()
                target = None
                next_discovery = min(next_discovery, time.monotonic() + 2)
                sessions = []
            working = any(session.get('status') in {'working', 'starting'} for session in sessions)
            with self._lock:
                self._sessions = sessions
                self._streams = streams
                self._updated = time.monotonic()
            # Fast while a reply is streaming, relaxed otherwise.
            self._stop.wait(0.25 if working else 2)
        connection.close()


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
