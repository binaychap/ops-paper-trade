from datetime import datetime
import sqlite3

import pytest

from app.records import read_records


def test_time_range_and_read_only(tmp_path):
    path = tmp_path / 'ledger.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, updated_at TEXT, payload_json TEXT)')
        conn.executemany('INSERT INTO events VALUES (?, ?, ?)', [
            (1, '2026-09-15T13:00:00Z', '{"symbol":"TEST"}'),
            (2, '2026-09-15T14:00:00+00:00', '{}'),
            (3, '2026-09-15T15:00:00Z', '{}'),
        ])
    before = path.read_bytes()
    data = read_records(path, 'events', start=datetime.fromisoformat('2026-09-15T09:00:00-04:00'),
                        end=datetime.fromisoformat('2026-09-15T11:00:00-04:00'))
    assert data['total'] == 2
    assert [r['id'] for r in data['records']] == [2, 1]
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        read_records(path, 'events', start=datetime(2026, 9, 15))
    with pytest.raises(ValueError):
        read_records(path, 'events', time_field='created_at')
    with pytest.raises(ValueError):
        read_records(path, 'events; DROP TABLE events')


def test_pagination_and_missing_database(tmp_path):
    path = tmp_path / 'ledger.db'
    assert read_records(path, 'events')['total'] == 0
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, updated_at TEXT)')
        conn.executemany('INSERT INTO events VALUES (?, ?)', [(i, '2026-09-15T14:00:00Z') for i in range(60)])
    first = read_records(path, 'events')
    second = read_records(path, 'events', page=2)
    assert first['total'] == second['total'] == 60
    assert len(first['records']) == 50
    assert len(second['records']) == 10
    assert not {r['id'] for r in first['records']} & {r['id'] for r in second['records']}
