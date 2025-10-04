# -*- coding: utf-8 -*-
"""
Telegram bot (reply keyboard + inline timepad for Start).
- Start in TG: enabled with 4-digit HHMM keypad (inline)
- Test: uses latest snapshot from GUI
- Stop: cancels schedule and stops current run
- Break/Lunch/Ready: act on current running controller
"""
from __future__ import annotations
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

import logic
from scheduler import scheduler, compute_target_from_hhmm, fmt_td

log = logging.getLogger("tg")

# === your token/user ===
BOT_TOKEN = "8359055783:AAEAt9TpqlLrWQwyAvDTQFvhIClBUikoFS0"
ALLOWED_USERS = {594953162}

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
    """Pretty print 'HHMM' buffer with underscores for missing digits."""
    s = (buf + "____")[:4]
    return f"{s[0]}{s[1]}:{s[2]}{s[3]}"


def _valid_time(buf: str) -> tuple[bool, int, int]:
    """Return (ok, hh, mm) for a 4-digit buffer."""
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
    """‘/start’ command (same as before): shows reply keyboard."""
    if not await _gate(update): return
    await update.message.reply_text(
        "Control ready. Кнопки ниже.\n"
        "Start — запланировать по времени (введите 4 цифры), "
        "Test — старт сразу, 🛑 Stop — аварийно закрыть.",
        reply_markup=TG_KB
    )


async def _start_timepad_flow(update: Update):
    """Entry point when user presses 'Start' (reply keyboard)."""
    uid = update.effective_user.id
    timepad_sessions[uid] = {"buf": "", "chat_id": update.effective_chat.id}
    text = "Введите время запуска (HHMM). Примеры: 0908, 1745, 0000.\nТекущее: " + _fmt_buf("")
    msg = await update.message.reply_text(text, reply_markup=_timepad_markup(""))
    timepad_sessions[uid]["msg_id"] = msg.message_id


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply-кнопки: Start/Test/Stop/..."""
    if not await _gate(update): return
    txt = (update.message.text or "").strip().lower()

    if txt == "start":
        # open timepad inline keyboard
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
    """Inline keypad handler for Start(HHMM)."""
    cq = update.callback_query
    if not cq:
        return
    if not await _gate(update):
        return

    uid = cq.from_user.id
    sess = timepad_sessions.get(uid)
    if not sess:
        # stale / no session — just dismiss
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
        # clear session and edit message
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
            # plan using snapshot from GUI
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

            # broadcast like GUI does & edit the keypad message
            msg = f"Запланировано на {target.strftime('%H:%M')} (через {fmt_td(target - target.now())})."
            # NB: fmt_td принимает timedelta; тут эквивалент: delay секунд
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
        # keep showing keypad if not scheduled
        sess["buf"] = buf  # type: ignore
        return
    else:
        await cq.answer()

    # update session + message (digits/backspace)
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
