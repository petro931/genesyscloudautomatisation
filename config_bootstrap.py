# -*- coding: utf-8 -*-
"""
config_bootstrap: стартовое модальное окно для ввода BOT_TOKEN, chat_id'ов и allowed_user.
- Сохраняет настройки в %APPDATA%/GenesysBot/config.json (Windows) или ~/.config/GenesysBot/config.json (прочие).
- Если файл уже есть, подставляет значения и показывает диалог (можно сразу жать OK).
- Предоставляет get_runtime_config() для остальных модулей.
"""
from __future__ import annotations
import json, os, sys
from dataclasses import dataclass, asdict
from typing import List, Optional
import tkinter as tk
from tkinter import ttk, messagebox

@dataclass
class RuntimeConfig:
    bot_token: str
    dest_chat_ids: List[int]          # например: [594953162, -1002993626250]
    allowed_users: List[int]          # кто может управлять ботом (например [594953162])
    chrome_profile_dir: str           # директория профиля UC
    chrome_version_main: Optional[int]  # если None — автоопределение

_CFG: Optional[RuntimeConfig] = None

def _config_path() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        base = os.path.join(base, "GenesysBot")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "GenesysBot")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "config.json")

def _load_from_disk() -> Optional[RuntimeConfig]:
    p = _config_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RuntimeConfig(**data)
    except Exception:
        return None

def _save_to_disk(cfg: RuntimeConfig) -> None:
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)

def _parse_int_list(raw: str) -> List[int]:
    out = []
    for part in raw.replace(";", ",").split(","):
        s = part.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except Exception:
            pass
    return out

def _show_modal(defaults: RuntimeConfig) -> RuntimeConfig:
    # Отдельный корень для модалки, чтобы не мешать основному GUI
    root = tk.Tk()
    root.withdraw()  # скрываем на время сборки
    win = tk.Toplevel(root)
    win.title("Initial setup")
    win.resizable(False, False)
    frm = ttk.Frame(win, padding=10)
    frm.grid(sticky="nsew")

    tk.Label(frm, text="Bot API token:").grid(row=0, column=0, sticky="e", pady=4)
    e_token = ttk.Entry(frm, width=56)
    e_token.grid(row=0, column=1, pady=4)
    e_token.insert(0, defaults.bot_token)

    tk.Label(frm, text="DEST_CHAT_IDS (comma-separated):").grid(row=1, column=0, sticky="e", pady=4)
    e_dest = ttk.Entry(frm, width=56)
    e_dest.grid(row=1, column=1, pady=4)
    e_dest.insert(0, ",".join(str(x) for x in defaults.dest_chat_ids))

    tk.Label(frm, text="ALLOWED_USERS (comma-separated):").grid(row=2, column=0, sticky="e", pady=4)
    e_allowed = ttk.Entry(frm, width=56)
    e_allowed.grid(row=2, column=1, pady=4)
    e_allowed.insert(0, ",".join(str(x) for x in defaults.allowed_users))

    tk.Label(frm, text="Chrome profile dir:").grid(row=3, column=0, sticky="e", pady=4)
    e_profile = ttk.Entry(frm, width=56)
    e_profile.grid(row=3, column=1, pady=4)
    e_profile.insert(0, defaults.chrome_profile_dir)

    tk.Label(frm, text="Chrome major version (optional):").grid(row=4, column=0, sticky="e", pady=4)
    e_ver = ttk.Entry(frm, width=10)
    e_ver.grid(row=4, column=1, sticky="w", pady=4)
    e_ver.insert(0, "" if defaults.chrome_version_main is None else str(defaults.chrome_version_main))

    def ok():
        token = e_token.get().strip()
        if not token or ":" not in token:
            messagebox.showerror("Error", "Invalid bot token.")
            return
        dest = _parse_int_list(e_dest.get())
        allowed = _parse_int_list(e_allowed.get())
        prof = e_profile.get().strip()
        ver_txt = e_ver.get().strip()
        ver = int(ver_txt) if ver_txt.isdigit() else None
        cfg = RuntimeConfig(
            bot_token=token,
            dest_chat_ids=dest or defaults.dest_chat_ids,
            allowed_users=allowed or defaults.allowed_users,
            chrome_profile_dir=prof or defaults.chrome_profile_dir,
            chrome_version_main=ver
        )
        _save_to_disk(cfg)
        nonlocal_result["cfg"] = cfg
        win.destroy()
        root.destroy()

    def cancel():
        # Если пользователь закрыл модалку — оставим дефолты, но сохраним их, чтобы дальше всё работало
        _save_to_disk(defaults)
        nonlocal_result["cfg"] = defaults
        win.destroy()
        root.destroy()

    nonlocal_result = {"cfg": defaults}

    btns = ttk.Frame(frm)
    btns.grid(row=5, column=0, columnspan=2, pady=10)
    ttk.Button(btns, text="OK", command=ok).grid(row=0, column=0, padx=5)
    ttk.Button(btns, text="Cancel", command=cancel).grid(row=0, column=1, padx=5)

    root.deiconify()
    win.grab_set()
    root.mainloop()
    return nonlocal_result["cfg"]

def load_or_prompt_config(defaults: RuntimeConfig) -> RuntimeConfig:
    global _CFG
    # 1) попробуем с диска
    disk = _load_from_disk()
    if disk:
        _CFG = disk
        return _CFG
    # 2) покажем модалку
    _CFG = _show_modal(defaults)
    return _CFG

def get_runtime_config() -> RuntimeConfig:
    if _CFG is None:
        # В крайнем случае вернём безопасные "пустые" значения, чтобы не падало при раннем импорте
        return RuntimeConfig(bot_token="YOUR_TOKEN_HERE", dest_chat_ids=[], allowed_users=[],
                             chrome_profile_dir=os.path.join(os.path.expanduser("~"), ".uc_profile"),
                             chrome_version_main=None)
    return _CFG
