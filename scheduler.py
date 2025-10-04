# -*- coding: utf-8 -*-
from __future__ import annotations
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple

def compute_target_from_hhmm(h: int, m: int) -> tuple[datetime, int]:
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # «край минуты»: если выбрана текущая минута и до конца <10с — перенос на завтра
    if target.date() == now.date() and target.hour == now.hour and target.minute == now.minute:
        if (60 - now.second) < 10:
            target = target + timedelta(days=1)
    elif target <= now:
        target = target + timedelta(days=1)
    delay = int((target - now).total_seconds())
    return target, delay

def fmt_td(td: timedelta) -> str:
    secs = int(td.total_seconds())
    return f"{secs//3600}h {secs%3600//60}m {secs%60}s"

class OneShotScheduler:
    def __init__(self):
        # ВАЖНО: RLock вместо Lock, чтобы не ловить дедлок при schedule_dt -> cancel (внутри того же lock)
        self._timer: Optional[threading.Timer] = None
        self._target: Optional[datetime] = None
        self._lock = threading.RLock()

    def schedule_dt(self, when: datetime, fn, *args, **kwargs) -> Tuple[int, datetime]:
        with self._lock:
            # cancel() тоже берёт этот же lock -> с RLock это безопасно
            self.cancel()
            now = datetime.now()
            target = when
            if target.date() == now.date() and target.hour == now.hour and target.minute == now.minute:
                if (60 - now.second) < 10:
                    target = target + timedelta(days=1)
            elif target <= now:
                target = target + timedelta(days=1)
            delay = int((target - now).total_seconds())
            self._target = target
            self._timer = threading.Timer(delay, fn, args=args, kwargs=kwargs)
            self._timer.daemon = True
            self._timer.start()
            return delay, target

    def cancel(self):
        with self._lock:
            if self._timer:
                try:
                    self._timer.cancel()
                finally:
                    self._timer = None
            self._target = None

    def eta(self) -> Optional[timedelta]:
        with self._lock:
            if not self._target:
                return None
            return max(self._target - datetime.now(), timedelta(0))

scheduler = OneShotScheduler()
