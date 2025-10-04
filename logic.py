# -*- coding: utf-8 -*-
"""
Core logic:
- Selenium StatusBot (Genesys statuses)
- Telegram helpers (send text/photo)
- Run controller (start/stop, is_running)
- Snapshot of Intervals for GUI/TG
"""
from __future__ import annotations
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

# (необязательно) маркер подключения дебагера
try:
    import debug_probe as dbg  # noqa
    dbg.mark_event("LOGIC_LOADED")
except Exception:
    pass


# Telegram
BOT_TOKEN = "8359055783:AAEAt9TpqlLrWQwyAvDTQFvhIClBUikoFS0"
DEST_CHAT_IDS = [594953162, -1002993626250]
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
CHROME_VERSION_MAIN = 140

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
