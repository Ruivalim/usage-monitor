from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from usage_monitor_app.core import collect_status, latest_snapshot, render_text, snapshot_json, _to_plain
from usage_monitor_app.web import dashboard_html

router = APIRouter()


@router.get('/', response_class=HTMLResponse)
async def dashboard():
    return dashboard_html()


@router.get('/status')
async def status(persist: bool = Query(False, description='Persist this read as a snapshot')):
    return _to_plain(collect_status(persist=persist))


@router.post('/refresh')
async def refresh():
    return _to_plain(collect_status(persist=True))


@router.get('/latest')
@router.get('/history')
async def latest(limit: int = Query(1, ge=1, le=50)):
    return {'snapshots': latest_snapshot(limit)}


@router.get('/status.txt', response_class=PlainTextResponse)
async def status_text(persist: bool = Query(False)):
    return render_text(collect_status(persist=persist))


@router.get('/status.json', response_class=PlainTextResponse)
async def status_json(pretty: bool = Query(True), persist: bool = Query(False)):
    return snapshot_json(pretty=pretty, persist=persist)
