# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, Body, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import alerts as _alerts
from . import autostart as _autostart
from . import i18n as _i18n
from .config import (
    LOCAL_TOKEN_HEADER,
    AppConfig,
    AuthConfig,
    ensure_local_token,
    load_app_config,
    parse_basic_auth,
    verify_local_token,
)
from .core import _to_plain, collect_status, latest_snapshot, load_overrides, render_text, set_provider_relevant, snapshot_json


def _autostart_config() -> _autostart.AutostartConfig:
    # The running interpreter is the one the agents should use (repo venv by
    # default; whatever the Makefile's AGENT_PYTHON overrides it to otherwise).
    return _autostart.AutostartConfig(python_executable=sys.executable)


def _scheduler(stop: threading.Event, interval_seconds: int) -> None:
    while not stop.wait(interval_seconds):
        try:
            snap = collect_status(persist=True)
            _alerts.process_snapshot(snap)
        except Exception:
            # Keep the local app alive; failures appear on next manual refresh.
            continue


def scheduler_lifespan(interval_seconds: int | None = None):
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        raw_interval = interval_seconds
        if raw_interval is None:
            raw_interval = load_app_config().intervals.refresh_seconds
        stop = threading.Event()
        thread: threading.Thread | None = None
        if raw_interval > 0:
            thread = threading.Thread(target=_scheduler, args=(stop, raw_interval), daemon=True)
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread:
                thread.join(timeout=2)

    return lifespan


def dashboard_html(config: AppConfig | None = None) -> str:
    # Single-file static dashboard. All fetches are relative (`./…`) so the same
    # document works at the standalone root and under the Hermes plugin prefix.
    # The document itself is public: it carries no data, only the shell. When
    # auth is enabled every data fetch 401s and the login gate takes over.
    #
    # Strings come from usage_monitor_app/i18n.py — the same table the tray
    # uses — inlined here as JSON so the page needs no extra round trip and
    # the two surfaces cannot drift apart.
    language = _i18n.resolve_language(config.dashboard.language if config is not None else None)
    # `<\/` keeps a stray `</script` inside a translated string from ending the
    # script element early; it is a valid JSON escape for `/`.
    strings = json.dumps(_i18n.translations(language), ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>API Usage Monitor</title>
  <style>
    :root {
      color-scheme: light;
      --bg:#f5f6fa;
      --text:#111827;
      --muted:#5b6577;
      --faint:#8a93a6;
      --surface:#ffffff;
      --surface-2:#f0f2f8;
      --border:#0f172a17;
      --border-strong:#0f172a29;
      --accent:#5b3ef0;
      --ok:#16a34a;
      --warn:#d97706;
      --err:#e11d48;
      --info:#0284c7;
      --radius:16px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-feature-settings: "tnum" 1;
    }
    * { box-sizing:border-box; }
    body {
      margin:0; padding:32px 24px 56px; background:var(--bg); color:var(--text);
      background-image:
        radial-gradient(60rem 30rem at 12% -10%, #5b3ef01f, transparent 60%),
        radial-gradient(50rem 26rem at 95% 0%, #0284c71f, transparent 55%);
      background-attachment:fixed;
      -webkit-font-smoothing:antialiased;
    }
    .wrap { max-width:1240px; margin:0 auto; }
    [hidden] { display:none !important; }

    header { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:22px; flex-wrap:wrap; }
    .brand { display:flex; align-items:center; gap:14px; min-width:0; }
    .logo {
      width:42px; height:42px; border-radius:13px; display:grid; place-items:center; font-size:19px; color:#fff;
      background:linear-gradient(150deg, #6d4bff, #14b8a6); box-shadow:0 8px 22px #5b3ef033; flex:none;
    }
    h1 { margin:0; font-size:21px; letter-spacing:-.02em; font-weight:800; }
    h2 { margin:0; font-size:15px; font-weight:700; letter-spacing:-.01em; }
    .meta { color:var(--muted); font-size:12.5px; }
    .controls { display:flex; align-items:center; gap:9px; flex-wrap:wrap; }

    button {
      font:inherit; font-weight:650; font-size:13px; color:var(--text); cursor:pointer;
      background:var(--surface); border:1px solid var(--border-strong); border-radius:11px; padding:9px 14px;
      transition:background .15s, border-color .15s, transform .08s, opacity .15s;
    }
    button:hover:not(:disabled) { background:var(--surface-2); border-color:#0f172a3d; }
    button:active:not(:disabled) { transform:translateY(1px); }
    button:disabled { opacity:.55; cursor:progress; }
    button.primary { color:#fff; background:linear-gradient(160deg, #6d4bff, #5b3ef0); border-color:#5b3ef0; box-shadow:0 6px 18px #5b3ef033; }
    button.primary:hover:not(:disabled) { background:linear-gradient(160deg, #7c5cff, #6544f2); border-color:#5b3ef0; }

    .toggle {
      display:flex; align-items:center; gap:7px; color:var(--muted); font-size:13px; user-select:none; cursor:pointer;
      background:var(--surface); border:1px solid var(--border-strong); border-radius:11px; padding:8px 13px;
    }
    .toggle input { accent-color:var(--accent); margin:0; }

    .overall {
      display:inline-flex; align-items:center; gap:8px; font-size:12.5px; font-weight:700; letter-spacing:.04em;
      text-transform:uppercase; border-radius:999px; padding:8px 14px; border:1px solid var(--border-strong); background:var(--surface);
    }
    .dot { width:9px; height:9px; border-radius:50%; background:var(--faint); }
    .overall[data-level="ok"] { color:#15803d; border-color:#16a34a55; background:#16a34a14; }
    .overall[data-level="ok"] .dot { background:var(--ok); box-shadow:0 0 0 4px #16a34a24; }
    .overall[data-level="warning"] { color:#b45309; border-color:#d9770655; background:#d9770614; }
    .overall[data-level="warning"] .dot { background:var(--warn); box-shadow:0 0 0 4px #d9770624; }
    .overall[data-level="error"] { color:#be123c; border-color:#e11d4855; background:#e11d4814; }
    .overall[data-level="error"] .dot { background:var(--err); box-shadow:0 0 0 4px #e11d4824; }

    .stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin-bottom:20px; }
    .stat { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:14px 16px; box-shadow:0 1px 2px #0f172a0f; }
    .stat .n { font-size:26px; font-weight:800; letter-spacing:-.03em; line-height:1.1; }
    .stat .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.07em; margin-top:4px; }
    .stat.ok .n { color:#15803d; } .stat.warn .n { color:#b45309; } .stat.err .n { color:#be123c; }

    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:14px; }
    .card {
      background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:16px;
      box-shadow:0 6px 18px #0f172a0f; position:relative; overflow:hidden;
      transition:border-color .15s, transform .15s, box-shadow .15s;
    }
    .card::before { content:""; position:absolute; inset:0 0 auto 0; height:2px; background:var(--faint); opacity:.5; }
    .card[data-status="ok"]::before { background:var(--ok); }
    .card[data-status="warning"]::before, .card[data-status="rate_limited"]::before { background:var(--warn); }
    .card[data-status="error"]::before, .card[data-status="quota_exhausted"]::before { background:var(--err); }
    .card:hover { border-color:var(--border-strong); transform:translateY(-2px); box-shadow:0 12px 26px #0f172a17; }
    .card.muted { opacity:.5; }
    .card.muted:hover { opacity:.8; }

    .top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .label { font-weight:750; font-size:15px; letter-spacing:-.01em; }
    .sub { color:var(--faint); font-size:11.5px; margin-top:3px; word-break:break-word; }
    .badges { display:flex; gap:6px; align-items:center; flex:none; }
    .chip {
      font-size:10.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
      border-radius:999px; padding:4px 9px; background:var(--surface-2); color:var(--muted); white-space:nowrap;
    }
    .chip.ok { background:#16a34a1a; color:#15803d; }
    .chip.warning, .chip.rate_limited { background:#d977061a; color:#b45309; }
    .chip.error, .chip.quota_exhausted { background:#e11d481a; color:#be123c; }
    .mute-btn { background:transparent; border:0; padding:3px 4px; font-size:15px; line-height:1; border-radius:8px; }
    .mute-btn:hover:not(:disabled) { background:var(--surface-2); border:0; }

    .money { display:flex; align-items:baseline; gap:7px; margin-top:14px; }
    .money .amount { font-size:27px; font-weight:800; letter-spacing:-.03em; }
    .money .cur { color:var(--muted); font-size:12px; font-weight:700; }
    .money .tag { color:var(--faint); font-size:11px; text-transform:uppercase; letter-spacing:.06em; margin-left:auto; }

    .window { margin-top:14px; }
    .window .row { display:flex; justify-content:space-between; gap:10px; font-size:12.5px; margin-bottom:6px; }
    .window .name { color:#334155; font-weight:600; }
    .window .val { color:var(--muted); }
    .track { height:6px; border-radius:999px; background:#0f172a14; overflow:hidden; }
    .fill { height:100%; border-radius:999px; background:linear-gradient(90deg, #0ea5e9, #6d4bff); transition:width .4s ease; }
    .fill.warn { background:linear-gradient(90deg, #f59e0b, #ea580c); }
    .fill.err { background:linear-gradient(90deg, #f43f5e, #be123c); }

    .note { color:#475569; font-size:12.5px; margin-top:11px; white-space:pre-wrap; word-break:break-word; }
    .note.faint { color:var(--faint); font-size:11.5px; }

    .panel { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:16px; margin-top:22px; box-shadow:0 6px 18px #0f172a0f; }
    .panel-head { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
    .chart { height:104px; display:flex; align-items:flex-end; gap:3px; overflow-x:auto; padding-bottom:2px; border-bottom:1px solid var(--border); }
    .bar { flex:1 0 6px; min-width:6px; min-height:3px; border-radius:4px 4px 0 0; background:linear-gradient(180deg, #38bdf8, #38bdf866); }
    .bar.warn { background:linear-gradient(180deg, #f59e0b, #f59e0b66); }
    .bar.err { background:linear-gradient(180deg, #f43f5e, #f43f5e66); }

    .empty { color:var(--faint); font-size:13px; padding:26px; text-align:center; border:1px dashed var(--border-strong); border-radius:var(--radius); }

    .gate { min-height:70vh; display:grid; place-items:center; }
    .gate form {
      width:min(360px, 100%); background:var(--surface); border:1px solid var(--border);
      border-radius:var(--radius); padding:26px 24px; box-shadow:0 12px 34px #0f172a1a;
      display:flex; flex-direction:column; gap:12px;
    }
    .gate .logo { margin:0 auto 2px; }
    .gate h1 { text-align:center; font-size:18px; }
    .gate .meta { text-align:center; margin-bottom:6px; }
    .gate label { display:flex; flex-direction:column; gap:5px; font-size:12px; font-weight:650; color:var(--muted); }
    .gate input {
      font:inherit; font-size:14px; color:var(--text); background:var(--surface-2);
      border:1px solid var(--border-strong); border-radius:10px; padding:10px 12px;
    }
    .gate input:focus { outline:2px solid var(--accent); outline-offset:1px; }
    .gate .error { color:var(--err); font-size:12.5px; }

    @media (max-width:640px) {
      body { padding:20px 14px 40px; }
      header { align-items:flex-start; }
      .controls { width:100%; }
    }
  </style>
</head>
<body>
  <div class="gate" id="gate" hidden>
    <form id="loginForm" onsubmit="login(event)">
      <div class="logo">◎</div>
      <h1>API Usage Monitor</h1>
      <div class="meta" data-i18n="dashboard.gate_intro">This dashboard requires authentication.</div>
      <label><span data-i18n="dashboard.username">username</span><input id="authUser" name="username" autocomplete="username" autofocus required></label>
      <label><span data-i18n="dashboard.password">password</span><input id="authPass" name="password" type="password" autocomplete="current-password" required></label>
      <div class="error" id="authError" hidden></div>
      <button class="primary" type="submit" data-i18n="dashboard.sign_in">Sign in</button>
    </form>
  </div>

  <div class="wrap" id="app">
    <header>
      <div class="brand">
        <div class="logo">◎</div>
        <div>
          <h1>API Usage Monitor</h1>
          <div class="meta" id="meta" data-i18n="dashboard.loading">loading…</div>
        </div>
      </div>
      <div class="controls">
        <span class="overall" id="overall"><span class="dot"></span><span id="overallText">…</span></span>
        <button class="ghost" id="startupBtn" onclick="toggleStartup()" data-i18n="dashboard.startup_loading">startup…</button>
        <button class="ghost" id="logoutBtn" onclick="logout()" hidden data-i18n="dashboard.logout">Logout</button>
        <button class="primary" id="refreshBtn" onclick="refresh()" data-i18n="dashboard.refresh">Refresh</button>
      </div>
    </header>

    <section class="stats" id="stats"></section>
    <section class="grid" id="providers"></section>

    <section class="panel">
      <div class="panel-head">
        <h2 data-i18n="dashboard.history">History</h2>
        <span class="meta" data-i18n="dashboard.history_hint">height = number of alerts per snapshot</span>
      </div>
      <div class="chart" id="history"></div>
    </section>
  </div>
<script>
let lastStatus = null;
let lastLoadedAt = null;
let pendingOverrides = {};
let authRequired = false;

// Rendered server-side from usage_monitor_app/i18n.py for the configured
// language: the page never waits on a round trip to know what to say, and the
// tray reads the very same table, so the two surfaces cannot drift apart.
const I18N = __STRINGS__;

function t(key, vars) {
  const text = I18N[key];
  if (text == null) return key;  // a missing key stays visible instead of blank
  if (!vars) return text;
  return Object.keys(vars).reduce((acc, name) => acc.split('{' + name + '}').join(vars[name]), text);
}

function applyLanguage() {
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
}

const ESCAPES = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ESCAPES[c]);

// --- auth ------------------------------------------------------------------
// The credential is a base64 "user:pass" kept in localStorage and replayed as
// an Authorization header. `X-Requested-With` tells the server to skip
// `WWW-Authenticate`, so the browser never opens its native login dialog on
// top of the gate below.
const AUTH_KEY = 'usagemonAuth';
const b64utf8 = s => btoa(String.fromCharCode(...new TextEncoder().encode(s)));

function authHeaders(extra) {
  const headers = Object.assign({'X-Requested-With': 'XMLHttpRequest'}, extra || {});
  const token = localStorage.getItem(AUTH_KEY);
  if (token) headers['Authorization'] = 'Basic ' + token;
  return headers;
}

function api(path, options) {
  const opts = Object.assign({}, options);
  opts.headers = authHeaders(opts.headers);
  return fetch(path, opts).then(resp => {
    if (resp.status === 401) {
      showGate(t('dashboard.session_expired'));
      throw new Error('unauthorized');
    }
    return resp;
  });
}

function showGate(message) {
  document.getElementById('app').hidden = true;
  document.getElementById('gate').hidden = false;
  const box = document.getElementById('authError');
  box.textContent = message || '';
  box.hidden = !message;
  document.getElementById('authUser').focus();
}

function hideGate() {
  document.getElementById('gate').hidden = true;
  document.getElementById('app').hidden = false;
}

async function login(ev) {
  ev.preventDefault();
  const user = document.getElementById('authUser').value;
  const pass = document.getElementById('authPass').value;
  const token = b64utf8(user + ':' + pass);
  const resp = await fetch('./auth/check', {headers: {'Authorization': 'Basic ' + token, 'X-Requested-With': 'XMLHttpRequest'}});
  if (!resp.ok) { showGate(t('dashboard.invalid_credentials')); return; }
  localStorage.setItem(AUTH_KEY, token);
  document.getElementById('authPass').value = '';
  hideGate();
  start();
}

function logout() {
  localStorage.removeItem(AUTH_KEY);
  showGate('');
}

function visibleProviders(providers) {
  return providers || [];
}

function effectiveRelevant(p) {
  return (p.id in pendingOverrides) ? pendingOverrides[p.id] : (p.relevant !== false);
}

function severity(status) {
  if (status === 'error' || status === 'quota_exhausted') return 'err';
  if (status === 'warning' || status === 'rate_limited') return 'warn';
  if (status === 'ok') return 'ok';
  return 'idle';
}

function money(m, tag) {
  if (!m || m.amount == null) return '';
  return `<div class="money"><span class="amount">${Number(m.amount).toFixed(2)}</span>` +
         `<span class="cur">${esc(m.currency || '')}</span><span class="tag">${tag}</span></div>`;
}

// Mirrors UsageWindow.reset_label(): the snapshot carries days/hours, not the label.
function resetLabel(w) {
  if (w.days_until_reset > 0) return w.days_until_reset + 'd';
  if (w.hours_until_reset > 0) return w.hours_until_reset + 'h';
  if (w.reset_at) return t('dashboard.reset_now');
  return null;
}

function windowBar(w) {
  const remaining = (w.remaining_percent != null) ? Number(w.remaining_percent)
                  : (w.used_percent != null ? 100 - Number(w.used_percent) : null);
  const used = remaining == null ? null : Math.min(100, Math.max(0, 100 - remaining));
  const cls = used == null ? '' : used >= 95 ? ' err' : used >= 85 ? ' warn' : '';
  const label = resetLabel(w);
  const reset = label ? t('dashboard.reset_in', {label: esc(label)}) : '';
  const val = remaining == null ? (w.detail ? esc(w.detail) : t('dashboard.no_data'))
            : t('dashboard.remaining', {percent: remaining.toFixed(0)}) + reset;
  return `<div class="window">
    <div class="row"><span class="name">${esc(w.label)}</span><span class="val">${val}</span></div>
    <div class="track"><div class="fill${cls}" style="width:${used == null ? 0 : used}%"></div></div>
  </div>`;
}

function renderStats(providers) {
  const counts = {ok:0, warn:0, err:0, idle:0, muted:0};
  providers.forEach(p => {
    if (!effectiveRelevant(p)) { counts.muted++; return; }
    counts[severity(p.status)]++;
  });
  document.getElementById('stats').innerHTML = [
    ['', providers.length, t('dashboard.stat_providers')],
    ['ok', counts.ok, t('dashboard.stat_ok')],
    ['warn', counts.warn, t('dashboard.stat_warn')],
    ['err', counts.err, t('dashboard.stat_err')],
    ['', counts.idle + counts.muted, t('dashboard.stat_idle')],
  ].map(([cls, n, k]) => `<div class="stat ${cls}"><div class="n">${n}</div><div class="k">${k}</div></div>`).join('');
}

function renderProviders() {
  if (!lastStatus) return;
  const providers = visibleProviders(lastStatus.providers);
  renderStats(providers);
  const host = document.getElementById('providers');
  if (!providers.length) {
    host.innerHTML = `<div class="empty">${t('dashboard.empty_providers')}</div>`;
    return;
  }
  host.innerHTML = providers.map(p => {
    const muted = !effectiveRelevant(p);
    const muteTitle = muted
      ? t('dashboard.muted_title')
      : t('dashboard.relevant_title');
    return `
    <article class="card${muted ? ' muted' : ''}" data-status="${esc(p.status)}">
      <div class="top">
        <div>
          <div class="label">${esc(p.label)}</div>
          <div class="sub">${esc(p.id)}${p.source ? ' · ' + esc(p.source) : ''}</div>
        </div>
        <div class="badges">
          <button class="mute-btn" title="${muteTitle}" onclick="toggleRelevant(${esc(JSON.stringify(String(p.id)))}, ${muted})">${muted ? '🔕' : '🔔'}</button>
          <span class="chip ${esc(p.status)}">${esc(p.status)}</span>
        </div>
      </div>
      ${money(p.balance, t('dashboard.balance'))}
      ${money(p.usage, t('dashboard.spent'))}
      ${(p.windows || []).map(windowBar).join('')}
      ${p.message ? `<div class="note">${esc(p.message)}</div>` : ''}
      ${(p.details || []).slice(0, 3).map(d => `<div class="note faint">${esc(d)}</div>`).join('')}
    </article>`;
  }).join('');
}

function renderHistory(snaps) {
  const host = document.getElementById('history');
  if (!snaps.length) {
    host.innerHTML = `<div class="empty" style="flex:1">${t('dashboard.empty_history')}</div>`;
    return;
  }
  const max = Math.max(1, ...snaps.map(s => (s.alerts || []).length));
  host.innerHTML = snaps.map(s => {
    const n = (s.alerts || []).length;
    const cls = n === 0 ? '' : n >= max ? ' err' : ' warn';
    const height = 6 + Math.round((n / max) * 88);
    const title = t('dashboard.history_bar_title', {when: esc(s.checked_at), count: n, overall: esc(s.overall || '')});
    return `<div class="bar${cls}" style="height:${height}px" title="${title}"></div>`;
  }).join('');
}

function renderMeta() {
  if (!lastStatus) return;
  const ago = lastLoadedAt ? Math.round((Date.now() - lastLoadedAt) / 1000) : 0;
  const when = new Date(lastStatus.checked_at);
  const stamp = isNaN(when) ? lastStatus.checked_at : when.toLocaleString();
  document.getElementById('meta').textContent = t('dashboard.meta', {stamp: stamp, ago: ago});
  const overall = String(lastStatus.overall || 'unknown');
  const pill = document.getElementById('overall');
  pill.dataset.level = overall;
  document.getElementById('overallText').textContent = overall;
}

async function load() {
  const [status, history] = await Promise.all([
    api('./status/cached').then(r => r.json()),
    api('./history?limit=80').then(r => r.json()),
  ]);
  lastStatus = status;
  lastLoadedAt = Date.now();
  pendingOverrides = {};  // server state caught up
  renderMeta();
  renderProviders();
  renderHistory(history.snapshots || []);
}

async function toggleRelevant(id, wasMuted) {
  pendingOverrides[id] = !wasMuted;  // optimistic UI: flips immediately
  renderProviders();
  await api('./overrides/' + encodeURIComponent(id), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({relevant: !wasMuted})}).catch(() => {});
  // Recollect in the background so the cached snapshot (overall/alerts) catches up.
  api('./refresh', {method:'POST'}).then(() => load()).catch(() => {});
}

async function loadStartup() {
  const btn = document.getElementById('startupBtn');
  try {
    const state = await api('./autostart').then(r => r.json());
    const menubar = (state.agents || []).find(a => a.label.endsWith('.menubar')) || {};
    const server = (state.agents || []).find(a => a.label.endsWith('.server')) || {};
    const on = !!(server.plist_exists || menubar.plist_exists);
    btn.textContent = on ? t('dashboard.startup_on') + (menubar.loaded ? t('dashboard.startup_tray_active') : '') : t('dashboard.startup_off');
    btn.title = on ? t('dashboard.startup_on_title') : t('dashboard.startup_off_title');
    btn.dataset.on = on ? '1' : '0';
  } catch (e) {
    btn.textContent = t('dashboard.startup_unknown');
  }
}

async function toggleStartup() {
  const btn = document.getElementById('startupBtn');
  const on = btn.dataset.on === '1';
  if (on && !confirm(t('dashboard.startup_confirm'))) return;
  btn.disabled = true;
  try {
    await api('./autostart/' + (on ? 'uninstall' : 'install'), {method:'POST'});
  } finally {
    btn.disabled = false;
    await loadStartup();
  }
}

async function refresh() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = t('dashboard.collecting');
  try {
    await api('./refresh', {method:'POST'}).catch(() => {});
  } finally {
    btn.disabled = false;
    btn.textContent = t('dashboard.refresh');
    await load().catch(() => {});
  }
}

let started = false;

// Data fetches only start after the gate is cleared; every one of them can
// still 401 later (credential rotated, config reloaded) and reopen it.
function start() {
  if (!started) {
    started = true;
    setInterval(() => { load().catch(() => {}); loadStartup(); }, 30000);
    setInterval(renderMeta, 5000);
  }
  load().catch(() => {});
  loadStartup();
}

async function boot() {
  applyLanguage();
  let config = {};
  try {
    config = await fetch('./config').then(r => r.ok ? r.json() : {});
  } catch (e) { config = {}; }
  // Language is already baked into the document; /config only decides whether
  // the login gate is needed at all.
  authRequired = !!config.auth_required;
  document.getElementById('logoutBtn').hidden = !authRequired;
  if (authRequired && !localStorage.getItem(AUTH_KEY)) { showGate(''); return; }
  start();
}

boot();
</script>
</body>
</html>""".replace("__LANG__", language).replace("__STRINGS__", strings)


API_PREFIX = "/api/v1"


def create_api_router() -> APIRouter:
    """Every data route of the local API, mounted twice by `create_app`.

    Once unprefixed (the historical paths the dashboard and existing clients
    use) and once under `/api/v1`, which is the stable path for external
    consumers. Both mounts serve the same handlers, so they never drift.
    """
    router = APIRouter()

    @router.get("/status")
    async def status(persist: bool = Query(False, description="Persist this read as a snapshot")):
        return _to_plain(collect_status(persist=persist))

    @router.get("/status/cached")
    async def status_cached():
        """Fast path: newest persisted snapshot; collects only if none exists yet."""
        snaps = latest_snapshot(1)
        if snaps:
            return snaps[0]
        return _to_plain(collect_status(persist=False))

    @router.post("/refresh")
    async def refresh():
        return _to_plain(collect_status(persist=True))

    @router.get("/latest")
    @router.get("/history")
    async def latest(limit: int = Query(50, ge=1, le=500)):
        return {"snapshots": latest_snapshot(limit)}

    @router.get("/status.txt", response_class=PlainTextResponse)
    async def status_text(persist: bool = Query(False)):
        return render_text(collect_status(persist=persist))

    @router.get("/status.json", response_class=PlainTextResponse)
    async def status_json(pretty: bool = Query(True), persist: bool = Query(False)):
        return snapshot_json(pretty=pretty, persist=persist)

    @router.get("/overrides")
    async def get_overrides():
        return load_overrides()

    @router.post("/overrides/{provider_id}")
    async def post_override(provider_id: str, payload: dict = Body(default=None)):
        relevant = bool((payload or {}).get("relevant", True))
        return set_provider_relevant(provider_id, relevant)

    @router.get("/auth/check")
    async def auth_check():
        """Credential probe for the dashboard login form.

        Unauthenticated requests never reach it: the middleware answers 401
        first. When auth is disabled it simply always succeeds.
        """
        return {"authenticated": True}

    @router.get("/autostart")
    async def autostart_state():
        return _autostart.agent_status(_autostart_config())

    @router.post("/autostart/install")
    async def autostart_install():
        return _autostart.install_agents(_autostart_config())

    @router.post("/autostart/uninstall")
    async def autostart_uninstall():
        return _autostart.uninstall_agents(_autostart_config())

    return router


# Paths served without credentials even when auth is on. Both carry no user
# data: the dashboard shell and the flags it needs to draw the login gate.
PUBLIC_PATHS = frozenset({"/", "/config"})

# The only paths the loopback token unlocks. Same-user tools (the menu bar app)
# can ask for a refresh without replaying a password they cannot read back from
# the stored hash; everything else still demands Basic Auth.
LOCAL_TOKEN_PATHS = frozenset({"/refresh", f"{API_PREFIX}/refresh"})


def _install_basic_auth(app: FastAPI, auth: AuthConfig, local_token: str | None = None) -> None:
    """Deny-by-default Basic Auth over every path outside PUBLIC_PATHS.

    Middleware rather than a route dependency so that new routes — and
    `/docs`, `/openapi.json` — are protected by construction instead of by
    remembering to add a dependency.
    """
    realm = auth.realm.replace("\\", "").replace('"', "")
    challenge = f'Basic realm="{realm}", charset="UTF-8"'

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if request.url.path in LOCAL_TOKEN_PATHS and verify_local_token(
            request.headers.get(LOCAL_TOKEN_HEADER), local_token
        ):
            return await call_next(request)
        credentials = parse_basic_auth(request.headers.get("authorization"))
        if credentials is not None and auth.verify(*credentials):
            return await call_next(request)
        headers = {}
        # The dashboard sets X-Requested-With so the browser does not stack its
        # native Basic Auth dialog on top of the in-page login form.
        if request.headers.get("x-requested-with", "").lower() != "xmlhttprequest":
            headers["WWW-Authenticate"] = challenge
        return JSONResponse({"detail": "Not authenticated"}, status_code=401, headers=headers)


def create_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config if config is not None else load_app_config()
    if app_config.auth.enabled and not app_config.auth.configured:
        raise RuntimeError(
            "auth.enabled is set but no credential is configured. Set "
            "USAGE_MONITOR_AUTH_PASSWORD / USAGE_MONITOR_AUTH_PASSWORD_HASH, or run "
            "`usagemon auth enable`."
        )

    app = FastAPI(title="API Usage Monitor", lifespan=scheduler_lifespan(app_config.intervals.refresh_seconds))
    app.state.app_config = app_config

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return dashboard_html(app_config)

    @app.get("/config")
    async def public_config():
        """Dashboard bootstrap flags. Never includes credentials."""
        return app_config.public_dict()

    app.include_router(create_api_router())
    app.include_router(create_api_router(), prefix=API_PREFIX, tags=["v1"])

    if app_config.auth.enabled:
        # Minted here rather than lazily, so the tray finds a token the moment
        # the backend is up instead of on the second refresh.
        try:
            local_token = ensure_local_token()
        except OSError:
            local_token = None  # read-only home: Basic Auth still works
        _install_basic_auth(app, app_config.auth, local_token=local_token)

    return app


app = create_app()
