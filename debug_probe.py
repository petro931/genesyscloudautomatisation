# -*- coding: utf-8 -*-
"""
debug_probe: безопасный отладчик кнопки Start/планировщика.

Подключение:
    import debug_probe as dbg
    dbg.init("start_debug.log")
    dbg.enable_scheduler_probe()       # логирует schedule_dt и момент "Timer fired"
    dbg.attach_gui_watch(root)         # в create_interface(), после root = tk.Tk()

Опционально в Start:
    target, delay = dbg.log_gui_start_click(hh, mm, snapshot)
    dbg.log_schedule_result(delay, target)
"""
from __future__ import annotations
import logging, logging.handlers, sys, threading, time, traceback
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

DBG = logging.getLogger("startdebug")
DBG.setLevel(logging.DEBUG)
DBG.propagate = False
_log_path = None
_original_schedule_dt = None
_gui_watch_active = False

def init(log_file: str = "start_debug.log", level: int = logging.DEBUG) -> None:
    global _log_path
    _log_path = log_file
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in DBG.handlers):
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
        fmt = logging.Formatter("[%(asctime)s] %(levelname)s [thr=%(threadName)s] %(message)s", "%H:%M:%S")
        fh.setFormatter(fmt)
        DBG.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        DBG.addHandler(sh)
    DBG.setLevel(level)
    DBG.info("=== debug_probe.init → log=%s level=%s ===", log_file, logging.getLevelName(level))

def get_log_path() -> Optional[str]:
    return _log_path

def dump_threads(reason: str = "") -> None:
    DBG.warning("THREAD DUMP start %s", f"({reason})" if reason else "")
    frames = sys._current_frames()
    for th in threading.enumerate():
        DBG.warning("• Thread name=%s ident=%s daemon=%s alive=%s", th.name, th.ident, th.daemon, th.is_alive())
        st = frames.get(th.ident)
        if st:
            lines = "".join(traceback.format_stack(st)).splitlines()
            for ln in lines[-20:]:
                DBG.warning("    %s", ln)
    DBG.warning("THREAD DUMP end")

def mark_event(tag: str, **kw: Any) -> None:
    DBG.info("EVENT %s | %s", tag, ", ".join(f"{k}={v}" for k, v in kw.items()))

def log_snapshot(snapshot: Any) -> None:
    try:
        DBG.info(
            "SNAPSHOT: start_on_shift=%s, fb_after=%s fb_dur=%s, lunch_after=%s lunch_dur=%s, "
            "sb_after=%s sb_dur=%s, close_after=%s",
            getattr(snapshot, "start_on_shift", None),
            getattr(snapshot, "first_break_after", None), getattr(snapshot, "first_break_duration", None),
            getattr(snapshot, "lunch_after", None), getattr(snapshot, "lunch_duration", None),
            getattr(snapshot, "second_break_after", None), getattr(snapshot, "second_break_duration", None),
            getattr(snapshot, "close_after", None),
        )
    except Exception as e:
        DBG.warning("SNAPSHOT: unable to format: %s", e)

def enable_scheduler_probe() -> None:
    global _original_schedule_dt
    if _original_schedule_dt:
        return
    import scheduler as _sched_mod
    _original_schedule_dt = _sched_mod.OneShotScheduler.schedule_dt

    def patched(self, when, fn, *args, **kwargs):
        DBG.info("schedule_dt CALLED: when=%s (now=%s) fn=%s args=%d kwargs=%d",
                 when.strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 getattr(fn, "__name__", fn), len(args), len(kwargs))

        def wrapped_fn(*a, **k):
            DBG.info(">>> TIMER FIRED at %s | thr=%s | fn=%s | args=%d kwargs=%d",
                     datetime.now().strftime("%H:%M:%S"), threading.current_thread().name,
                     getattr(fn, "__name__", fn), len(a), len(k))
            try:
                return fn(*a, **k)
            except Exception:
                DBG.exception("EXCEPTION inside scheduled fn:")
                raise
            finally:
                try:
                    time.sleep(0.05)
                    DBG.info("After fire: scheduler.eta() = %s", self.eta())
                except Exception:
                    pass

        delay, target = _original_schedule_dt(self, when, wrapped_fn, *args, **kwargs)
        DBG.info("schedule_dt RESULT: delay=%ss target=%s | timer=%s",
                 delay, target.strftime("%H:%M:%S"), getattr(self, "_timer", None))
        try:
            DBG.info("ETA now: %s", self.eta())
        except Exception:
            pass
        return delay, target

    _sched_mod.OneShotScheduler.schedule_dt = patched
    DBG.info("enable_scheduler_probe: patched OneShotScheduler.schedule_dt OK")

def disable_scheduler_probe() -> None:
    global _original_schedule_dt
    if not _original_schedule_dt:
        return
    import scheduler as _sched_mod
    _sched_mod.OneShotScheduler.schedule_dt = _original_schedule_dt
    _original_schedule_dt = None
    DBG.info("disable_scheduler_probe: restored original schedule_dt")

def attach_gui_watch(root, period_ms: int = 1000) -> None:
    global _gui_watch_active
    if _gui_watch_active:
        return
    _gui_watch_active = True

    from scheduler import scheduler
    hb = {"i": 0}

    def _tick():
        hb["i"] += 1
        try:
            eta = scheduler.eta()
        except Exception:
            eta = None
        DBG.debug("GUI_HEARTBEAT #%d | ETA=%s | active_timer=%s",
                  hb["i"], eta, getattr(scheduler, "_timer", None))
        try:
            root.after(period_ms, _tick)
        except Exception:
            _gui_watch_active = False

    try:
        root.after(period_ms, _tick)
        DBG.info("attach_gui_watch: started (period=%d ms)", period_ms)
    except Exception as e:
        DBG.warning("attach_gui_watch: failed to start: %s", e)

def log_gui_start_click(hh: int, mm: int, snapshot: Any = None) -> Tuple[datetime, int]:
    try:
        from scheduler import compute_target_from_hhmm
        target, delay = compute_target_from_hhmm(hh, mm)
        DBG.info("GUI_START_CLICK: hh=%02d mm=%02d -> target=%s delay=%s",
                 hh, mm, target.strftime("%Y-%m-%d %H:%M:%S"), delay)
    except Exception as e:
        target, delay = datetime.now(), -1
        DBG.exception("GUI_START_CLICK: compute_target_from_hhmm failed: %s", e)

    if snapshot is not None:
        log_snapshot(snapshot)
    dump_threads("after GUI Start click")
    return target, delay

def log_schedule_result(delay: int, target: datetime) -> None:
    DBG.info("GUI_START_SCHEDULED: target=%s delay=%ss (now=%s)",
             target.strftime("%Y-%m-%d %H:%M:%S"), delay, datetime.now().strftime("%H:%M:%S"))

def self_test(seconds: int = 2) -> None:
    from scheduler import scheduler
    when = datetime.now() + timedelta(seconds=seconds)
    def _probe():
        DBG.info("SELFTEST: probe fired after %ds", seconds)
    DBG.info("SELFTEST: scheduling probe at +%ds", seconds)
    scheduler.schedule_dt(when, _probe)
