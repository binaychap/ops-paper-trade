"""Read-only paginated browser for all application ledger tables."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()
TABLES = ('events', 'optionomics_trade_ideas', 'scheduled_stock_exits', 'top_bullish_trades')


@router.get('/records', include_in_schema=False)
def records_page():
    return FileResponse(Path(__file__).parent / 'static' / 'records.html', headers={'Cache-Control': 'no-store'})


def read_records(database_path, table, time_field='updated_at', start=None, end=None, page=1):
    if table not in TABLES or time_field not in ('created_at', 'updated_at'):
        raise ValueError('Invalid table or time field')
    if page < 1:
        raise ValueError('Invalid page')
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ValueError('Time filters must include a timezone')
    if start and end and start >= end:
        raise ValueError('Start must be before end')
    result = dict(tables=[], columns=[], records=[], total=0, page=page, page_size=50)
    path = Path(database_path).resolve()
    if not path.exists():
        return result
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        available = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result['tables'] = [t for t in TABLES if t in available]
        if table not in available:
            return result
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        result['columns'] = columns
        if time_field not in columns:
            raise ValueError('This table has no selected time field; choose Updated time')
        where, params = [], []
        for value, operator in ((start, '>='), (end, '<')):
            if value:
                where.append(f'julianday("{time_field}") {operator} julianday(?)')
                params.append(value.isoformat())
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        result['total'] = conn.execute(f'SELECT COUNT(*) FROM "{table}"' + clause, params).fetchone()[0]
        result['records'] = [dict(r) for r in conn.execute(
            f'SELECT * FROM "{table}"{clause} ORDER BY julianday("{time_field}") DESC, rowid DESC LIMIT 50 OFFSET ?',
            [*params, (page - 1) * 50])]
    return result


@router.get('/api/records')
def records_api(table: str = 'top_bullish_trades', time_field: str = 'updated_at',
                start: datetime | None = None, end: datetime | None = None,
                page: int = Query(1, ge=1)):
    from app.main import get_settings
    try:
        result = read_records(get_settings().database_path, table, time_field, start, end, page)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(503, 'Database temporarily unavailable') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})
