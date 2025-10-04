# -*- coding: utf-8 -*-
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk

import logic
from logic import Intervals
from scheduler import scheduler, compute_target_from_hhmm, fmt_td
import tg_bot  # запускаем бота в фоне

# DEBUG HOOKS
import debug_probe as dbg
dbg.init("start_debug.log")
dbg.enable_scheduler_probe()

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
        dbg.log_gui_start_click(hh, mm, snap)

        target, delay = compute_target_from_hhmm(hh, mm)
        scheduler.schedule_dt(target, logic.start_sequence_with, snap)

        dbg.log_schedule_result(delay, target)
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

    dbg.attach_gui_watch(root)

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
    tg_bot.run_in_thread()
    create_interface()
