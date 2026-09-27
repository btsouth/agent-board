#!/usr/bin/env python3
"""Compare fixture roster work; pass the old roster.py as the only argument."""
import ast
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agentboard import roster

spec = importlib.util.spec_from_file_location('old_roster', sys.argv[1])
old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
schema = next(ast.literal_eval(node.value) for node in ast.parse((ROOT / 'tests/roster_test.py').read_text()).body
              if isinstance(node, ast.Assign) and any(getattr(target, 'id', '') == 'SCHEMA' for target in node.targets))
with tempfile.TemporaryDirectory() as temp:
    db = Path(temp) / 'state.db'
    with sqlite3.connect(db) as conn:
        conn.executescript(schema)
        for index in range(200):
            sid = f'session-{index}'
            stamp = time.time() - index
            conn.execute('INSERT INTO sessions (id,title,started_at,last_activity_at,last_read_at,message_count) VALUES (?,?,?,?,?,?)', (sid,sid,stamp,stamp,stamp,20))
            for message in range(20):
                conn.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)', (sid,'assistant' if message % 2 else 'user','Fixture message ' + 'sample ' * 40,stamp - 20 + message))
    results = []
    for label, module, cadence, cached in [('v0.2.6', old, .25, False), ('candidate idle', roster, 2, True), ('candidate active', roster, .25, True)]:
        original = module._connect
        counters = {'queries': 0, 'connections': 0, 'polls': 0}
        def trace(_sql): counters['queries'] += 1
        def connect(path):
            counters['connections'] += 1
            conn = original(path); conn.set_trace_callback(trace); return conn
        module._connect = connect
        reader = roster.CachedBoard().read if cached else module.board
        started = time.monotonic(); cpu = time.process_time()
        for index in range(round(8 / cadence)):
            board = reader(db_path=db, limit=15, days=3)
            assert len(board['sessions']) == 15
            counters['polls'] += 1
            time.sleep(max(0, started + (index + 1) * cadence - time.monotonic()))
        results.append({'case': label, 'wall_s': round(time.monotonic() - started, 3), 'cpu_ms': round((time.process_time() - cpu)*1000, 2), **counters})
        module._connect = original
    print(json.dumps({'fixture': '200 sessions, 20 messages each, unchanged SQLite store, 8 seconds per case', 'results': results}, indent=2))
