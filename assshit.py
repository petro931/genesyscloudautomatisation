# -*- coding: utf-8 -*-
from __future__ import annotations

# Combined single-file build WITHOUT debug_probe. Sections below are verbatim except debug lines removed.
# Added: simple persistent config with console prompts that override defaults and save to app_config.json

# ===== config bootstrap =====
import os, json, sys

def _to_int_list(s: str, current: list[int]) -> list[int]:
    s = s.strip()
    if not s:
        return current
    parts = [p.strip() for p in s.replace(";", ",").replace(" ", ",").split(",")]
    out = []
    for p in parts:
        if not p:
            continue
        try:
            out.append(int(p))
        except Exception:
            pass
    return out or current

def _prompt_value(label: str, current: str) -> str:
    try:
        val = input(f"{label} [Enter=keep] (current: {current}): ").strip()
        return val or current
    except (EOFError, KeyboardInterrupt):
        # нет интерактивного ввода - оставляем как есть
        print(f"[config] No input available for {label}. Keeping current.")
        return current

def _prompt_list(label: str, current: list[int]) -> list[int]:
    cur_str = ",".join(str(i) for i in current)
    try:
        raw = input(f"{label} comma-separated [Enter=keep] (current: {cur_str}): ").strip()
        if not raw:
            return current
        return _to_int_list(raw, current)
    except (EOFError, KeyboardInterrupt):
        print(f"[config] No input available for {label}. Keeping current.")
        return current

class _CONFIG:
    bot_token: str
    dest_chat_ids: list[int]
    allowed_users: list[int]
    chrome_version_main: int
    path: str

    def __init__(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_config.json")
        # defaults from code
        self.bot_token = "8359055783:AAEAt9TpqlLrWQwyAvDTQFvhIClBUikoFS0"
        self.dest_chat_ids = [594953162, -1002993626250]
        self.allowed_users = [594953162]
        self.chrome_version_main = 140

    def _merge(self, data: dict):
        self.bot_token = str(data.get("bot_token", self.bot_token))
        self.dest_chat_ids = list(data.get("dest_chat_ids", self.dest_chat_ids))
        self.allowed_users = list(data.get("allowed_users", self.allowed_users))
        self.chrome_version_main = int(data.get("chrome_version_main", self.chrome_version_main))

    def load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._merge(data)
            except Exception as e:
                print(f"[config] Failed to load {self.path}: {e}")

    def prompt_always(self):
        print("\n=== Optional setup. Press Enter to keep current values ===")
        print(f"[config] File: {self.path}")
        # Спрашиваем всегда. Если нет stdin - оставим как есть.
        self.bot_token = _prompt_value("Telegram BOT_TOKEN", self.bot_token)
        self.dest_chat_ids = _prompt_list("DEST_CHAT_IDS", self.dest_chat_ids)
        self.allowed_users = _prompt_list("ALLOWED_USERS", self.allowed_users)
        cvm_raw = _prompt_value("CHROME_VERSION_MAIN (int)", str(self.chrome_version_main))
        try:
            self.chrome_version_main = int(cvm_raw)
        except Exception:
            print("[config] Invalid CHROME_VERSION_MAIN. Keeping previous value.")

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "bot_token": self.bot_token,
                    "dest_chat_ids": self.dest_chat_ids,
                    "allowed_users": self.allowed_users,
                    "chrome_version_main": self.chrome_version_main,
                }, f, ensure_ascii=False, indent=2)
            print(f"[config] Saved to {self.path}")
        except Exception as e:
            print(f"[config] Failed to save {self.path}: {e}")

CONFIG = _CONFIG()
CONFIG.load()
CONFIG.prompt_always()  # спрашиваем на каждом запуске
CONFIG.save()
git --version



# ===== logic.py =====
# -*- coding: utf-8 -*-
"""
Core logic:
- Selenium StatusBot (Genesys statuses)
- Telegram helpers (send text/photo)
- Run controller (start/stop, is_running)
- Snapshot of Intervals for GUI/TG
"""
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Tuple, Iterable, List

import requests
import pyautogui
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)

# =========================
# Config / logging
# =========================
LOG_LEVEL = logging.INFO
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("logic")

# Telegram from CONFIG
BOT_TOKEN = CONFIG.bot_token
DEST_CHAT_IDS = CONFIG.dest_chat_ids
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send_text(text: str) -> None:
    for chat_id in DEST_CHAT_IDS:
        try:
            requests.post(f"{API_BASE}/sendMessage", params={"chat_id": chat_id, "text": text}, timeout=10)
        except Exception as e:
            log.warning("tg_send_text failed: %s", e)

def tg_send_photo_bytes(b: bytes, caption: str = "") -> None:
    for chat_id in DEST_CHAT_IDS:
        try:
            files = {"photo": ("screen.png", b, "image/png")}
            data = {"chat_id": chat_id, "caption": caption}
            requests.post(f"{API_BASE}/sendPhoto", data=data, files=files, timeout=30)
        except Exception as e:
            log.warning("tg_send_photo failed: %s", e)

def os_screenshot_and_send(caption: str) -> None:
    try:
        img = pyautogui.screenshot()
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tg_send_photo_bytes(buf.getvalue(), caption=caption)
    except Exception as e:
        tg_send_text(f"{caption} (screenshot failed: {e})")

# =========================
# Selenium / Genesys config
# =========================
CHROME_PROFILE_DIR = "C:/temp/uc_profile_2"
GENESYS_URL = "https://apps.mypurecloud.de/directory/#/activity/schedule"
CHROME_VERSION_MAIN = CONFIG.chrome_version_main

RESTART_MAX_TRIES = 2
RESTART_BACKOFF = 40  # seconds

class Status(Enum):
    AVAILABLE = "Available"
    BREAK = "Break"
    MEAL = "Meal"  # Lunch

STATUS_SELECTORS: Dict[Status, str] = {
    Status.AVAILABLE: "li:nth-of-type(2) span.presence-label > span > span",
    Status.BREAK:     "li:nth-of-type(5) span.presence-label > span > span",
    Status.MEAL:      "li:nth-of-type(6) span.presence-label > span > span",
}

AVATAR_XPATHS: List[str] = [
    "//*[contains(@id,'entity-image')]",
    "//*[@role='img' and @aria-label and string-length(@aria-label)>0]",
]

CLEAR_SELECTORS: List[Tuple[By, str]] = [
    (By.CSS_SELECTOR, "div.gux-scrollable-section"),
    (By.CSS_SELECTOR, "main"),
    (By.CSS_SELECTOR, "body"),
    (By.XPATH, "//*[@id='app']"),
]

BACK_TO_PRIMARY_SELECTORS: List[Tuple[By, str]] = [
    (By.XPATH, "//*[contains(@aria-label,'Navigate back to primary presences')]"),
    (By.CSS_SELECTOR, "nav:nth-of-type(1) ul > div gux-icon"),
]

# =========================
# Intervals / plan
# =========================
@dataclass(frozen=True)
class Intervals:
    first_break_after: int
    first_break_duration: int
    lunch_after: int
    lunch_duration: int
    second_break_after: int
    second_break_duration: int
    close_after: int
    start_on_shift: int = 180

def sequence_plan(intervals: Intervals) -> Iterable[Tuple[Status, int, int]]:
    return [
        (Status.BREAK, intervals.first_break_after, intervals.first_break_duration),
        (Status.AVAILABLE, 0, 0),
        (Status.MEAL, intervals.lunch_after, intervals.lunch_duration),
        (Status.AVAILABLE, 0, 0),
        (Status.BREAK, intervals.second_break_after, intervals.second_break_duration),
        (Status.AVAILABLE, 0, 0),
    ]

# =========================
# Selenium helpers
# =========================
def robust_click_element(driver: WebDriver, el, retries: int = 5, pause: float = 0.2) -> bool:
    for _ in range(retries):
        try:
            try:
                el.click()
                return True
            except (ElementClickInterceptedException, StaleElementReferenceException):
                driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", el)
                time.sleep(0.05)
                try:
                    el.click()
                    return True
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                    return True
        except Exception:
            time.sleep(pause)
    return False

def find_in_any_frame(driver: WebDriver, by: By, locator: str) -> tuple[Optional[int], Optional[object]]:
    driver.switch_to.default_content()
    try:
        el = driver.find_element(by, locator)
        return None, el
    except Exception:
        pass
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for idx, fr in enumerate(frames):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            el = driver.find_element(by, locator)
            return idx, el
        except Exception:
            continue
    driver.switch_to.default_content()
    return None, None

def send_escape_and_clear(driver: WebDriver, esc_times: int = 1) -> None:
    driver.switch_to.default_content()
    try:
        for _ in range(esc_times):
            driver.execute_script("document.activeElement && document.activeElement.blur && document.activeElement.blur();")
            driver.execute_script("""
                const e = new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, which:27, bubbles:true});
                document.dispatchEvent(e);
            """)
            time.sleep(0.05)
    except Exception:
        pass
    for by, loc in CLEAR_SELECTORS:
        try:
            driver.switch_to.default_content()
            el = driver.find_element(by, loc)
            if robust_click_element(driver, el, retries=1, pause=0.05):
                time.sleep(0.05)
                return
        except Exception:
            continue

def nav_back_to_primary_if_present(driver: WebDriver) -> bool:
    driver.switch_to.default_content()
    for by, loc in BACK_TO_PRIMARY_SELECTORS:
        try:
            el = driver.find_element(by, loc)
            if robust_click_element(driver, el, retries=2, pause=0.1):
                time.sleep(0.1)
                return True
        except Exception:
            pass
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for fr in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            for by, loc in BACK_TO_PRIMARY_SELECTORS:
                try:
                    el = driver.find_element(by, loc)
                    if robust_click_element(driver, el, retries=2, pause=0.1):
                        time.sleep(0.1)
                        return True
                except Exception:
                    pass
        except Exception:
            continue
    driver.switch_to.default_content()
    return False

def _anchor_on_menu(driver: WebDriver, menu_frame_index: Optional[int]) -> bool:
    try:
        driver.switch_to.default_content()
        if menu_frame_index is not None:
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            if 0 <= menu_frame_index < len(frames):
                driver.switch_to.frame(frames[menu_frame_index])
        for css in STATUS_SELECTORS.values():
            try:
                el = driver.find_element(By.CSS_SELECTOR, css)
                if el.is_displayed():
                    ActionChains(driver).move_to_element(el).pause(0.05).perform()
                    driver.execute_script("arguments[0].focus && arguments[0].focus();", el)
                    time.sleep(0.08)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def now_ms() -> float:
    return time.time() * 1000.0

# =========================
# StatusBot
# =========================
class StatusBot:
    def __init__(self, intervals: Intervals):
        self.driver: WebDriver | None = None
        self.menu_frame_index: Optional[int] = None
        self.intervals = intervals
        self.manual_stop = False
        self._open_in_progress = False
        self._last_open_ts = 0.0
        self._open_debounce_ms = 850.0

    # ---- Selenium setup
    def _make_driver(self) -> WebDriver:
        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        driver = uc.Chrome(options=options, version_main=CHROME_VERSION_MAIN)
        return driver

    # ---- checks
    def session_alive(self) -> bool:
        if not self.driver:
            return False
        try:
            self.driver.execute_script("return 1")
            return True
        except Exception:
            return False

    def _is_menu_open(self) -> bool:
        candidates = list(STATUS_SELECTORS.values())
        self.driver.switch_to.default_content()
        for css in candidates:
            try:
                if self.driver.find_element(By.CSS_SELECTOR, css).is_displayed():
                    self.menu_frame_index = None
                    return True
            except Exception:
                pass
        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for idx, fr in enumerate(frames):
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame(fr)
                for css in candidates:
                    try:
                        if self.driver.find_element(By.CSS_SELECTOR, css).is_displayed():
                            self.menu_frame_index = idx
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        self.driver.switch_to.default_content()
        return False

    def _switch_to_menu_frame(self) -> None:
        self.driver.switch_to.default_content()
        if self.menu_frame_index is not None:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            if 0 <= self.menu_frame_index < len(frames):
                self.driver.switch_to.frame(frames[self.menu_frame_index])

    def _avatar_click_debounced(self, el) -> bool:
        t = now_ms()
        if t - self._last_open_ts < self._open_debounce_ms:
            return True
        ok = robust_click_element(self.driver, el, retries=2, pause=0.1)
        if ok:
            self._last_open_ts = t
        return ok

    def _open_via_avatar_once(self) -> bool:
        send_escape_and_clear(self.driver, esc_times=1)
        nav_back_to_primary_if_present(self.driver)

        target_el = None
        for xp in AVATAR_XPATHS:
            frame_idx, el = find_in_any_frame(self.driver, By.XPATH, xp)
            if el:
                log.info("Нашёл аватар по %s (iframe=%s)", xp, frame_idx if frame_idx is not None else "root")
                target_el = el
                break
        if not target_el:
            return False

        if not self._avatar_click_debounced(target_el):
            return False

        end = time.time() + 1.2
        while time.time() < end:
            if self._is_menu_open():
                _anchor_on_menu(self.driver, self.menu_frame_index)
                return True
            time.sleep(0.05)
        return False

    def _ensure_menu_open_retry(self, stabilize_checks: int = 3, check_delay: float = 0.18) -> bool:
        stable = 0
        while True:
            if not self._is_menu_open():
                if self._open_in_progress:
                    time.sleep(0.1)
                    stable = 0
                    continue
                try:
                    self._open_in_progress = True
                    ok = self._open_via_avatar_once()
                    if not ok:
                        time.sleep(0.25)
                    stable = 0
                finally:
                    self._open_in_progress = False
                continue

            _anchor_on_menu(self.driver, self.menu_frame_index)
            time.sleep(check_delay)
            if self._is_menu_open():
                stable += 1
                if stable >= stabilize_checks:
                    log.info("Меню статусов открыто стабильно")
                    return True
            else:
                stable = 0

    def _select_status(self, status: Status) -> bool:
        self._ensure_menu_open_retry()
        self._switch_to_menu_frame()

        css_inner = STATUS_SELECTORS[status]
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, css_inner)
        except Exception as e:
            log.error("Не нашёл пункт меню по CSS (%s): %s", css_inner, e)
            return False

        try:
            try:
                btn = el.find_element(By.XPATH, "./ancestor::button[1]")
            except NoSuchElementException:
                btn = el
            if not robust_click_element(self.driver, btn, retries=8, pause=0.2):
                log.error("Не удалось кликнуть по кнопке статуса %s", status.value)
                return False
        except Exception as e:
            log.error("Ошибка при выборе статуса %s: %s", status.value, e)
            return False

        time.sleep(0.3)
        log.info("Статус выбран: %s", status.value)
        return True

    # ---- Public API on running driver
    def force_status(self, status: Status):
        if not self.driver:
            tg_send_text("Драйвер не запущен.")
            return
        try:
            self._ensure_menu_open_retry()
            self._select_status(status)
            tg_send_text(f"Forced: {status.value}")
            time.sleep(3)
            os_screenshot_and_send(f"{status.value} (forced)")
        except Exception as e:
            tg_send_text(f"Force status error: {e}")

    def request_stop(self):
        self.manual_stop = True
        try:
            if self.driver:
                self.driver.quit()
        finally:
            self.driver = None

    # ---- Main sequence
    def _run_once(self) -> bool:
        try:
            self.driver = self._make_driver()
            self.driver.get(GENESYS_URL)
            self.driver.maximize_window()
            time.sleep(2.0)
            tg_send_text("Script started.")

            self._ensure_menu_open_retry()
            self._select_status(Status.AVAILABLE)
            tg_send_text("Login successfully. Status set to Available.")

            if self.intervals.start_on_shift > 0:
                time.sleep(self.intervals.start_on_shift)
            tg_send_text("Shift has been started.")
            time.sleep(3)
            os_screenshot_and_send("Available (start)")

            total_waited = 0
            for status, wait_before, duration in sequence_plan(self.intervals):
                if wait_before > 0:
                    time.sleep(wait_before)
                    total_waited += wait_before

                if status is not Status.AVAILABLE:
                    self._select_status(status)
                    tg_send_text(f"Status set to {status.value}.")
                    time.sleep(3)
                    os_screenshot_and_send(status.value)

                if duration > 0:
                    time.sleep(duration)
                    total_waited += duration
                    self._select_status(Status.AVAILABLE)
                    tg_send_text("Status set to Ready.")
                    time.sleep(3)
                    os_screenshot_and_send("Ready")

            remain = max(self.intervals.close_after - total_waited, 0)
            if remain:
                time.sleep(remain)

            tg_send_text("Shift is over.")
            return True

        except Exception as e:
            if not self.manual_stop:
                tg_send_text(f"Error: {e}")
                try:
                    os_screenshot_and_send("Error screen")
                except Exception:
                    pass
            return False

    def run(self):
        tries = 0
        try_complete = False
        while tries <= RESTART_MAX_TRIES:
            if self.manual_stop:
                break
            if tries > 0:
                tg_send_text(f"Retry {tries}/{RESTART_MAX_TRIES} in {RESTART_BACKOFF}s…")
                for _ in range(RESTART_BACKOFF):
                    if self.manual_stop:
                        break
                    time.sleep(1)
                if self.manual_stop:
                    break
            tries += 1
            try_complete = self._run_once()
            if try_complete:
                break

        if try_complete and self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

# =========================
# Controller (single run)
# =========================
_controller_lock = threading.Lock()
_controller: Optional[StatusBot] = None
_worker_thread: Optional[threading.Thread] = None

def _reset_state() -> None:
    global _controller, _worker_thread
    with _controller_lock:
        try:
            if _controller and _controller.driver:
                _controller.close()
        except Exception:
            pass
        _controller = None
        _worker_thread = None

def is_running() -> bool:
    global _worker_thread, _controller
    if _worker_thread is not None and _worker_thread.is_alive():
        return True
    _worker_thread = None

    ctrl = _controller
    if ctrl and ctrl.driver is not None:
        if ctrl.session_alive():
            return True
        try:
            ctrl.close()
        except Exception:
            pass
        _controller = None
    return False

def start_sequence_with(intervals: Intervals) -> None:
    """Start sequence in background with given snapshot."""
    global _controller, _worker_thread
    if is_running():
        tg_send_text("Already running.")
        return

    with _controller_lock:
        _controller = StatusBot(intervals)
        _controller.manual_stop = False

    def _run():
        try:
            _controller.run()
        finally:
            pass

    _worker_thread = threading.Thread(target=_run, daemon=True, name="status_sequence")
    _worker_thread.start()

# =========================
# Snapshot for TG "Test"
# =========================
CURRENT_SNAPSHOT: Optional[Intervals] = None

def set_snapshot(snap: Intervals) -> None:
    global CURRENT_SNAPSHOT
    CURRENT_SNAPSHOT = snap

def get_snapshot() -> Optional[Intervals]:
    return CURRENT_SNAPSHOT

# =========================
# TG helpers for current controller
# =========================
def _get_controller() -> Optional[StatusBot]:
    global _controller
    return _controller

def force_status_cmd(status: Status) -> None:
    """Force status using the **running** controller."""
    ctrl = _get_controller()
    if not is_running() or ctrl is None:
        tg_send_text("Не запущено: сначала Start/Test.")
        return
    threading.Thread(target=lambda: ctrl.force_status(status), daemon=True, name=f"force_{status.value}").start()

def request_stop_and_reset() -> None:
    """Stop current run (if any) and fully reset state."""
    try:
        ctrl = _get_controller()
        if ctrl:
            ctrl.request_stop()
    except Exception:
        pass
    _reset_state()
    time.sleep(1.0)
    os_screenshot_and_send("Stopped (desktop view)")


# ===== scheduler.py =====
# -*- coding: utf-8 -*-
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple

def compute_target_from_hhmm(h: int, m: int) -> tuple[datetime, int]:
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # край минуты: если выбрана текущая минута и до конца <10с — перенос на завтра
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
        # Важно: RLock вместо Lock, чтобы не ловить дедлок при schedule_dt -> cancel
        self._timer: Optional[threading.Timer] = None
        self._target: Optional[datetime] = None
        self._lock = threading.RLock()

    def schedule_dt(self, when: datetime, fn, *args, **kwargs) -> Tuple[int, datetime]:
        with self._lock:
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


# ===== tg_bot.py =====
# -*- coding: utf-8 -*-
"""
Telegram bot (reply keyboard + inline timepad for Start).
- Start in TG: enabled with 4-digit HHMM keypad (inline)
- Test: uses latest snapshot from GUI
- Stop: cancels schedule and stops current run
- Break/Lunch/Ready: act on current running controller
"""
import asyncio
import logging
from typing import Dict

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# Import after CONFIG is ready
import logic
from scheduler import scheduler, compute_target_from_hhmm, fmt_td

log = logging.getLogger("tg")

# Use values from CONFIG so there is a single source of truth
BOT_TOKEN = CONFIG.bot_token
ALLOWED_USERS = set(CONFIG.allowed_users)

# ===== Reply keyboard (as before) =====
TG_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Start"), KeyboardButton("Test"), KeyboardButton("🛑 Stop")],
        [KeyboardButton("Check status")],
        [KeyboardButton("Break"), KeyboardButton("Lunch"), KeyboardButton("Ready")],
    ],
    resize_keyboard=True,
)

# ===== Inline Timepad state =====
# per-user session: { user_id: {"buf": "HHMM_partial", "chat_id": int, "msg_id": int} }
timepad_sessions: Dict[int, Dict[str, int | str]] = {}

async def _gate(update: Update) -> bool:
    if ALLOWED_USERS and (not update.effective_user or update.effective_user.id not in ALLOWED_USERS):
        if update.message:
            await update.message.reply_text("Access denied")
        elif update.callback_query:
            await update.callback_query.answer("Access denied", show_alert=True)
        return False
    return True

def _fmt_buf(buf: str) -> str:
    s = (buf + "____")[:4]
    return f"{s[0]}{s[1]}:{s[2]}{s[3]}"

def _valid_time(buf: str) -> tuple[bool, int, int]:
    if len(buf) != 4 or not buf.isdigit():
        return False, -1, -1
    hh = int(buf[:2]); mm = int(buf[2:])
    return (0 <= hh <= 23 and 0 <= mm <= 59), hh, mm

def _timepad_markup(buf: str) -> InlineKeyboardMarkup:
    ok, hh, mm = _valid_time(buf)
    ok_label = f"✅ OK {hh:02d}:{mm:02d}" if ok else "✅ OK"
    rows = [
        [InlineKeyboardButton("1", callback_data="tp:1"),
         InlineKeyboardButton("2", callback_data="tp:2"),
         InlineKeyboardButton("3", callback_data="tp:3")],
        [InlineKeyboardButton("4", callback_data="tp:4"),
         InlineKeyboardButton("5", callback_data="tp:5"),
         InlineKeyboardButton("6", callback_data="tp:6")],
        [InlineKeyboardButton("7", callback_data="tp:7"),
         InlineKeyboardButton("8", callback_data="tp:8"),
         InlineKeyboardButton("9", callback_data="tp:9")],
        [InlineKeyboardButton("0", callback_data="tp:0"),
         InlineKeyboardButton("⌫", callback_data="tp:bksp"),
         InlineKeyboardButton("✖ Cancel", callback_data="tp:cancel")],
        [InlineKeyboardButton(ok_label, callback_data="tp:ok")],
    ]
    return InlineKeyboardMarkup(rows)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update): return
    await update.message.reply_text(
        "Control ready. Кнопки ниже.\n"
        "Start - запланировать по времени (введите 4 цифры), "
        "Test - старт сразу, 🛑 Stop - аварийно закрыть.",
        reply_markup=TG_KB
    )

async def _start_timepad_flow(update: Update):
    uid = update.effective_user.id
    timepad_sessions[uid] = {"buf": "", "chat_id": update.effective_chat.id}
    text = "Введите время запуска (HHMM). Примеры: 0908, 1745, 0000.\nТекущее: " + _fmt_buf("")
    msg = await update.message.reply_text(text, reply_markup=_timepad_markup(""))
    timepad_sessions[uid]["msg_id"] = msg.message_id

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate(update): return
    txt = (update.message.text or "").strip().lower()

    if txt == "start":
        return await _start_timepad_flow(update)

    if txt == "test":
        if logic.is_running():
            return await update.message.reply_text("Уже запущено.", reply_markup=TG_KB)
        snap = logic.get_snapshot()
        if snap is None:
            return await update.message.reply_text("Снапшот ещё не готов. Откройте GUI.", reply_markup=TG_KB)
        logic.start_sequence_with(snap)
        return await update.message.reply_text("Стартую сейчас…", reply_markup=TG_KB)

    if txt in ("🛑 stop", "stop"):
        scheduler.cancel()
        logic.request_stop_and_reset()
        return await update.message.reply_text("🛑 Stopped. Готов к новому Start/Test.", reply_markup=TG_KB)

    if txt == "check status":
        logic.os_screenshot_and_send("Current desktop")
        return await update.message.reply_text("Скрин отправлен.", reply_markup=TG_KB)

    if txt == "break":
        logic.force_status_cmd(logic.Status.BREAK)
        return await update.message.reply_text("Setting Break…", reply_markup=TG_KB)

    if txt == "lunch":
        logic.force_status_cmd(logic.Status.MEAL)
        return await update.message.reply_text("Setting Lunch…", reply_markup=TG_KB)

    if txt == "ready":
        logic.force_status_cmd(logic.Status.AVAILABLE)
        return await update.message.reply_text("Setting Ready…", reply_markup=TG_KB)

    return await update.message.reply_text("Не понял. Используй кнопки ниже.", reply_markup=TG_KB)

async def handle_timepad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    if not cq:
        return
    if not await _gate(update):
        return

    uid = cq.from_user.id
    sess = timepad_sessions.get(uid)
    if not sess:
        return await cq.answer("Сессия не активна", show_alert=False)

    buf = str(sess.get("buf", ""))  # type: ignore
    data = cq.data or ""
    key = data.split(":", 1)[1] if ":" in data else ""

    if key.isdigit():
        if len(buf) < 4:
            buf += key
        await cq.answer()
    elif key == "bksp":
        buf = buf[:-1]
        await cq.answer()
    elif key == "cancel":
        await cq.answer("Отменено")
        timepad_sessions.pop(uid, None)
        try:
            await cq.edit_message_text("Запланированный старт отменён.", reply_markup=None)
        except Exception:
            pass
        return
    elif key == "ok":
        ok, hh, mm = _valid_time(buf)
        if not ok:
            await cq.answer("Введите 4 цифры HHMM", show_alert=True)
        else:
            await cq.answer("Планирую…")
            snap = logic.get_snapshot()
            if snap is None:
                try:
                    await cq.edit_message_text(
                        "Снапшот интервалов ещё не готов. Откройте GUI, измените любое поле и попробуйте снова.",
                        reply_markup=None
                    )
                finally:
                    timepad_sessions.pop(uid, None)
                return

            target, delay = compute_target_from_hhmm(hh, mm)
            scheduler.schedule_dt(target, logic.start_sequence_with, snap)

            from datetime import timedelta as _td
            msg = f"Запланировано на {target.strftime('%H:%M')} (через {fmt_td(_td(seconds=delay))})."
            logic.tg_send_text(msg)

            try:
                await cq.edit_message_text(
                    f"✅ {msg}\nТекущее: {hh:02d}:{mm:02d}",
                    reply_markup=None
                )
            except Exception:
                pass
            timepad_sessions.pop(uid, None)
        sess["buf"] = buf  # type: ignore
        return
    else:
        await cq.answer()

    sess["buf"] = buf  # type: ignore
    try:
        await cq.edit_message_text(
            f"Введите время запуска (HHMM).\nТекущее: {_fmt_buf(buf)}",
            reply_markup=_timepad_markup(buf)
        )
    except Exception:
        pass

def run_in_thread():
    """Start Telegram bot in a daemon thread."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
        app.add_handler(CallbackQueryHandler(handle_timepad, pattern=r"^tp:"))

        async def whoami():
            me = await app.bot.get_me()
            log.info("TG ready as @%s (id=%s)", me.username, me.id)
        loop.run_until_complete(whoami())

        app.run_polling()

    import threading
    threading.Thread(target=_run, daemon=True, name="tg_bot").start()
    log.info("Telegram bot thread started")


# ===== gui_app.py =====
# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk

import logic
from logic import Intervals
from scheduler import scheduler, compute_target_from_hhmm, fmt_td
import tg_bot  # запускаем бота в фоне

# DEBUG HOOKS

log = logging.getLogger("gui")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

first_break_after_var = None
first_break_duration_var = None
lunch_after_var = None
lunch_duration_var = None
second_break_after_var = None
second_break_duration_var = None
close_after_var = None
start_on_shift_var = None

schedule_hour_var = None
schedule_minute_var = None

total_time_label = None

def build_intervals_from_gui() -> Intervals:
    return Intervals(
        first_break_after=int(first_break_after_var.get()),
        first_break_duration=int(first_break_duration_var.get()),
        lunch_after=int(lunch_after_var.get()),
        lunch_duration=int(lunch_duration_var.get()),
        second_break_after=int(second_break_after_var.get()),
        second_break_duration=int(second_break_duration_var.get()),
        close_after=int(close_after_var.get()),
        start_on_shift=int(start_on_shift_var.get()),
    )

def _update_snapshot_and_total(*_):
    try:
        snap = build_intervals_from_gui()
        logic.set_snapshot(snap)
        total = (
            snap.first_break_after + snap.first_break_duration +
            snap.lunch_after + snap.lunch_duration +
            snap.second_break_after + snap.second_break_duration +
            snap.close_after
        )
        h = int(total // 3600); mm = int((total % 3600) // 60); ss = int(total % 60)
        total_time_label.config(text=f"Total Time: {total:.0f} sec ({h}h {mm}m {ss}s)")
    except Exception:
        total_time_label.config(text="Error in input values")

def start_program_at_scheduled_time():
    try:
        hh = int(schedule_hour_var.get()); mm = int(schedule_minute_var.get())
        snap = build_intervals_from_gui()
        logic.set_snapshot(snap)

        target, delay = compute_target_from_hhmm(hh, mm)
        scheduler.schedule_dt(target, logic.start_sequence_with, snap)
        logic.tg_send_text(f"Запланировано на {target.strftime('%H:%M')} (через {fmt_td(timedelta(seconds=delay))}).")
    except Exception as e:
        logic.tg_send_text(f"Ошибка расписания: {e}")

def test_program():
    snap = build_intervals_from_gui()
    logic.set_snapshot(snap)
    logic.start_sequence_with(snap)

def create_interface():
    global first_break_after_var, first_break_duration_var, lunch_after_var, lunch_duration_var
    global second_break_after_var, second_break_duration_var, close_after_var, start_on_shift_var
    global schedule_hour_var, schedule_minute_var, total_time_label

    root = tk.Tk()
    root.title("Task Scheduler")
    root.configure(bg="#f0f0f0")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TFrame", background="#f0f0f0")
    style.configure("TLabel", background="#f0f0f0", font=("Arial", 9))
    style.configure("TEntry", font=("Arial", 9))
    style.configure("TButton", font=("Arial", 9), padding=5)
    style.configure("TotalTime.TLabel", font=("Arial", 11, "bold"))

    now = datetime.now()
    schedule_hour_var = tk.StringVar(value=f"{now.hour:02d}")
    schedule_minute_var = tk.StringVar(value=f"{now.minute:02d}")

    start_on_shift_var = tk.StringVar(value="180")
    first_break_after_var = tk.StringVar(value="7200")
    first_break_duration_var = tk.StringVar(value="899")
    lunch_after_var = tk.StringVar(value="4500")
    lunch_duration_var = tk.StringVar(value="1799")
    second_break_after_var = tk.StringVar(value="9000")
    second_break_duration_var = tk.StringVar(value="899")
    close_after_var = tk.StringVar(value="6404")

    for v in (
        first_break_after_var, first_break_duration_var,
        lunch_after_var, lunch_duration_var,
        second_break_after_var, second_break_duration_var,
        close_after_var, start_on_shift_var,
    ):
        v.trace("w", _update_snapshot_and_total)

    schedule_frame = ttk.Frame(root, padding=8)
    schedule_frame.grid(row=0, column=0, columnspan=2, pady=8, sticky="nsew")
    tk.Label(schedule_frame, text="Schedule Time (hh:mm):", bg="#f0f0f0").grid(row=0, column=0, pady=5)
    ttk.Combobox(schedule_frame, textvariable=schedule_hour_var, values=[f"{i:02d}" for i in range(24)], width=5).grid(row=0, column=1, pady=5)
    ttk.Combobox(schedule_frame, textvariable=schedule_minute_var, values=[f"{i:02d}" for i in range(60)], width=5).grid(row=0, column=2, pady=5)

    input_frame = ttk.Frame(root, padding=8)
    input_frame.grid(row=1, column=0, columnspan=2, pady=8, sticky="nsew")
    entries = [
        ("First Break After (sec):", first_break_after_var),
        ("First Break Duration (sec):", first_break_duration_var),
        ("Lunch After (sec):",        lunch_after_var),
        ("Lunch Duration (sec):",     lunch_duration_var),
        ("Second Break After (sec):", second_break_after_var),
        ("Second Break Duration (sec):", second_break_duration_var),
        ("Close After (sec):",        close_after_var),
        ("Start on Shift (sec):",     start_on_shift_var),
    ]
    for idx, (label, var) in enumerate(entries):
        tk.Label(input_frame, text=label, bg="#f0f0f0").grid(row=idx, column=0, pady=5)
        ttk.Entry(input_frame, textvariable=var, width=10).grid(row=idx, column=1, pady=5)

    btns = ttk.Frame(root, padding=8)
    btns.grid(row=2, column=0, columnspan=2, pady=8, sticky="nsew")
    ttk.Button(btns, text="Start", command=start_program_at_scheduled_time).grid(row=0, column=0, padx=5)
    ttk.Button(btns, text="Test", command=test_program).grid(row=0, column=1, padx=5)

    total_time_label = ttk.Label(root, text="Total Time: Calculating...", style="TotalTime.TLabel")
    total_time_label.grid(row=3, column=0, columnspan=2, pady=8)

    _update_snapshot_and_total()
    root.mainloop()

if __name__ == "__main__":
    # Bot uses CONFIG.bot_token and CONFIG.allowed_users already
    tg_bot.run_in_thread()
    create_interface()
