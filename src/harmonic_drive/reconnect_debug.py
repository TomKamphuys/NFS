from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import uuid
from typing import Any

from fastapi import Request
from loguru import logger
from nicegui import app, context


RUN_ID = uuid.uuid4().hex[:8]
STARTED_AT = time.time()
_installed = False
_loop_monitor_started = False
_deep_ui_diagnostics = False
_page_count = 0
_scanner_update_count = 0
_last_scanner_report = 0.0
_last_scanner_state = None
_scanner_ui_scheduled_count = 0
_scanner_ui_applied_count = 0
_scanner_ui_dropped_count = 0
_scanner_ui_pending_count = 0
_last_scanner_ui_report = 0.0
_max_scanner_ui_queue_delay = 0.0
_max_scanner_ui_apply_time = 0.0
_progress_update_count = 0
_last_progress_report = 0.0
_measurement_phase = "idle"
_browser_endpoint_registered = False


def _thread_label() -> str:
    thread = threading.current_thread()
    return f"{thread.name}/{thread.ident}"


def _client_label(client: Any = None) -> str:
    if client is None:
        try:
            client = context.client
        except RuntimeError:
            return "no-client-context"
    client_id = getattr(client, "id", None)
    page = getattr(client, "page", None)
    page_path = getattr(page, "path", None) if page is not None else None
    return f"id={client_id!r}, page={page_path!r}, deleted={getattr(client, 'is_deleted', None)!r}"


def _state_label(state: Any) -> str:
    if state is None:
        return "None"
    return str(getattr(state, "name", state))


def set_deep_ui_diagnostics(enabled: bool) -> None:
    global _deep_ui_diagnostics
    _deep_ui_diagnostics = bool(enabled)
    logger.info("Deep UI diagnostics: run_id={}, enabled={}", RUN_ID, _deep_ui_diagnostics)


def install() -> None:
    global _installed, _browser_endpoint_registered
    if _installed:
        return
    _installed = True
    logger.info(
        "Reconnect diagnostics installed: run_id={}, pid={}, cwd={!r}, argv={!r}, thread={}",
        RUN_ID,
        os.getpid(),
        os.getcwd(),
        sys.argv,
        _thread_label(),
    )

    def on_connect(client=None):
        logger.info(
            "NiceGUI client connected: run_id={}, {}, phase={}, uptime={:.1f}s",
            RUN_ID,
            _client_label(client),
            _measurement_phase,
            time.time() - STARTED_AT,
        )

    def on_disconnect(client=None):
        logger.warning(
            "NiceGUI client disconnected/reconnected: run_id={}, {}, phase={}, uptime={:.1f}s",
            RUN_ID,
            _client_label(client),
            _measurement_phase,
            time.time() - STARTED_AT,
        )

    def on_exception(exc: Exception | None = None):
        logger.warning(
            "NiceGUI exception hook: run_id={}, phase={}, client={}, exception={!r}",
            RUN_ID,
            _measurement_phase,
            _client_label(),
            exc,
        )

    app.on_connect(on_connect)
    app.on_disconnect(on_disconnect)
    app.on_exception(on_exception)
    app.on_startup(start_loop_monitor)

    if not _browser_endpoint_registered:
        _browser_endpoint_registered = True

        @app.post("/_hals_reconnect_debug/browser")
        async def _browser_debug_event(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {"event": "invalid-json"}
            log = logger.debug if payload.get("event") == "browser-probe-installed" else logger.info
            log(
                "Browser lifecycle event: run_id={}, phase={}, payload={}",
                RUN_ID,
                _measurement_phase,
                payload,
            )
            return {"ok": True}


def start_loop_monitor() -> None:
    global _loop_monitor_started
    if _loop_monitor_started:
        return
    _loop_monitor_started = True
    try:
        asyncio.create_task(_loop_monitor())
    except RuntimeError as exc:
        logger.debug("Reconnect diagnostics loop monitor could not start: {}", exc)


async def _loop_monitor(interval_s: float = 0.5, warn_lag_s: float = 1.5) -> None:
    expected = time.perf_counter() + interval_s
    while True:
        await asyncio.sleep(interval_s)
        now = time.perf_counter()
        lag = now - expected
        if lag >= warn_lag_s:
            if _deep_ui_diagnostics:
                logger.warning(
                    "UI event loop lag detected: run_id={}, lag={:.3f}s, phase={}, "
                    "scanner_updates={}, scanner_ui_pending={}, scanner_ui_scheduled={}, "
                    "scanner_ui_applied={}, scanner_ui_dropped={}, scanner_ui_max_queue_delay={:.3f}s, "
                    "scanner_ui_max_apply_time={:.3f}s, progress_updates={}, thread={}",
                    RUN_ID,
                    lag,
                    _measurement_phase,
                    _scanner_update_count,
                    _scanner_ui_pending_count,
                    _scanner_ui_scheduled_count,
                    _scanner_ui_applied_count,
                    _scanner_ui_dropped_count,
                    _max_scanner_ui_queue_delay,
                    _max_scanner_ui_apply_time,
                    _progress_update_count,
                    _thread_label(),
                )
            else:
                logger.warning(
                    "UI event loop lag detected: run_id={}, lag={:.3f}s, phase={}, thread={}",
                    RUN_ID,
                    lag,
                    _measurement_phase,
                    _thread_label(),
                )
        expected = now + interval_s


def log_page_created() -> None:
    global _page_count
    _page_count += 1
    logger.info(
        "NiceGUI page created: run_id={}, page_count={}, {}, phase={}, uptime={:.1f}s",
        RUN_ID,
        _page_count,
        _client_label(),
        _measurement_phase,
        time.time() - STARTED_AT,
    )


def install_browser_probe() -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    ui.run_javascript(
        """
        (() => {
          if (window.__halsReconnectDebugInstalled) return;
          window.__halsReconnectDebugInstalled = true;

          const nav = performance.getEntriesByType('navigation')[0] || {};
          const post = (payload, beacon=false) => {
            const body = JSON.stringify({
              ...payload,
              href: window.location.href,
              visibility: document.visibilityState,
              navType: nav.type || 'unknown',
              transferSize: nav.transferSize || null,
              timestamp: new Date().toISOString(),
            });
            if (beacon && navigator.sendBeacon) {
              navigator.sendBeacon('/_hals_reconnect_debug/browser', new Blob([body], {type: 'application/json'}));
              return;
            }
            fetch('/_hals_reconnect_debug/browser', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body,
              keepalive: true,
            }).catch(() => {});
          };

          post({event: 'browser-probe-installed'});
          window.addEventListener('beforeunload', () => post({event: 'beforeunload'}, true));
          window.addEventListener('pagehide', (event) => post({event: 'pagehide', persisted: event.persisted}, true));
          document.addEventListener('visibilitychange', () => post({event: 'visibilitychange'}));
          window.addEventListener('error', (event) => post({
            event: 'browser-error',
            message: String(event.message || ''),
            source: String(event.filename || ''),
            line: event.lineno || null,
          }));
          window.addEventListener('unhandledrejection', (event) => post({
            event: 'browser-unhandledrejection',
            reason: String(event.reason || ''),
          }));
        })();
        """
    )


def set_measurement_phase(phase: str) -> None:
    global _measurement_phase
    _measurement_phase = phase
    logger.info("Measurement debug phase: run_id={}, phase={}", RUN_ID, phase)


def log_worker_event(event: str, func: Any = None, extra: str = "") -> None:
    func_name = getattr(func, "__qualname__", None) or getattr(func, "__name__", None) or repr(func)
    logger.info(
        "Audio worker {}: run_id={}, func={}, phase={}, thread={}, {}",
        event,
        RUN_ID,
        func_name,
        _measurement_phase,
        _thread_label(),
        extra,
    )


def record_scanner_update(state: Any = None) -> None:
    global _scanner_update_count, _last_scanner_report, _last_scanner_state
    _scanner_update_count += 1
    now = time.monotonic()
    state_text = _state_label(state)
    should_report = (
        _deep_ui_diagnostics
        and (
        _last_scanner_report == 0.0
        or now - _last_scanner_report >= 5.0
        or state_text != _last_scanner_state
        )
    )
    if not should_report:
        return
    logger.info(
        "Scanner callback activity: run_id={}, count={}, state={}, phase={}, thread={}",
        RUN_ID,
        _scanner_update_count,
        state_text,
        _measurement_phase,
        _thread_label(),
    )
    _last_scanner_report = now
    _last_scanner_state = state_text


def record_scanner_ui_scheduled() -> float:
    global _scanner_ui_scheduled_count, _scanner_ui_pending_count
    _scanner_ui_scheduled_count += 1
    _scanner_ui_pending_count += 1
    return time.monotonic()


def record_scanner_ui_dropped(reason: str) -> None:
    global _scanner_ui_dropped_count, _scanner_ui_pending_count
    _scanner_ui_dropped_count += 1
    _scanner_ui_pending_count = max(0, _scanner_ui_pending_count - 1)
    if not _deep_ui_diagnostics:
        return
    logger.debug(
        "Scanner UI update dropped: run_id={}, reason={}, pending={}, scheduled={}, applied={}, dropped={}, phase={}",
        RUN_ID,
        reason,
        _scanner_ui_pending_count,
        _scanner_ui_scheduled_count,
        _scanner_ui_applied_count,
        _scanner_ui_dropped_count,
        _measurement_phase,
    )


def record_scanner_ui_applied(
    queued_at: float | None,
    apply_started_at: float,
    apply_finished_at: float,
) -> None:
    global _scanner_ui_applied_count, _scanner_ui_pending_count
    global _last_scanner_ui_report, _max_scanner_ui_queue_delay, _max_scanner_ui_apply_time
    _scanner_ui_applied_count += 1
    _scanner_ui_pending_count = max(0, _scanner_ui_pending_count - 1)
    queue_delay = apply_started_at - queued_at if queued_at is not None else 0.0
    apply_time = apply_finished_at - apply_started_at
    _max_scanner_ui_queue_delay = max(_max_scanner_ui_queue_delay, queue_delay)
    _max_scanner_ui_apply_time = max(_max_scanner_ui_apply_time, apply_time)
    if not _deep_ui_diagnostics:
        return

    now = time.monotonic()
    if now - _last_scanner_ui_report < 5.0 and queue_delay < 0.5 and apply_time < 0.05:
        return
    logger.info(
        "Scanner UI update activity: run_id={}, pending={}, scheduled={}, applied={}, "
        "dropped={}, queue_delay={:.3f}s, apply_time={:.3f}s, max_queue_delay={:.3f}s, "
        "max_apply_time={:.3f}s, phase={}, thread={}",
        RUN_ID,
        _scanner_ui_pending_count,
        _scanner_ui_scheduled_count,
        _scanner_ui_applied_count,
        _scanner_ui_dropped_count,
        queue_delay,
        apply_time,
        _max_scanner_ui_queue_delay,
        _max_scanner_ui_apply_time,
        _measurement_phase,
        _thread_label(),
    )
    _last_scanner_ui_report = now


def record_progress_update(event: dict[str, Any] | None = None, *, source: str = "unknown") -> None:
    global _progress_update_count, _last_progress_report
    _progress_update_count += 1
    event = event or {}
    if not _deep_ui_diagnostics:
        return
    if (
        _measurement_phase == "idle"
        and str(event.get("status") or "Ready") == "Ready"
        and int(event.get("current") or 0) == 0
        and int(event.get("total") or 0) == 0
    ):
        return
    now = time.monotonic()
    if now - _last_progress_report < 5.0:
        return
    logger.info(
        "Measurement progress activity: run_id={}, count={}, source={}, phase={}, event={}",
        RUN_ID,
        _progress_update_count,
        source,
        _measurement_phase,
        event,
    )
    _last_progress_report = now
