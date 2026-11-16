# -*- coding: utf-8 -*-
r"""
Voice Pill — локальный голосовой ввод в стиле Windows 11.

Win+H          - показать плашку и начать запись
Win+H ещё раз  - остановить, распознать, вставить текст в окно, где ты был
Клик по плашке - то же самое (фокус она не забирает)
Ctrl+Alt+Q     - запасное сочетание

Свой низкоуровневый хук клавиатуры: Пуск от Win+H не открывается,
Win-комбинации не ломаются (библиотека keyboard не используется).

Откат: удалить папку D:\AI\voice-input, ярлыки на рабочем столе и в shell:startup.
"""
import os
os.environ.setdefault("HF_HOME", r"D:\AI\huggingface")   # модели качаются на D:

import ctypes
import ctypes.wintypes as wintypes
import random
import socket
import sys
import threading
import time
import winsound

import numpy as np
import pyperclip
import sounddevice as sd
from faster_whisper import WhisperModel

MODEL_SIZE = "small"
LANGUAGE = None    # None = авто-определение (рус + англ), или "ru"/"en"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32")

# ---------- защита от второго запуска ----------
try:
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock.bind(("127.0.0.1", 58123))
except OSError:
    sys.exit(0)

model = None
state = "idle"          # idle | rec | proc
audio_chunks = []
samplerate = 16000
target_hwnd = None


def beep(freq, dur=110):
    threading.Thread(target=lambda: winsound.Beep(freq, dur), daemon=True).start()


def load_model():
    global model
    import os
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8",
                         cpu_threads=max(4, os.cpu_count() or 4))


# =====================  фокус целевого окна  =====================
def remember_target():
    global target_hwnd
    target_hwnd = user32.GetForegroundWindow()


def restore_target():
    if not target_hwnd or user32.GetForegroundWindow() == target_hwnd:
        return
    user32.keybd_event(0x12, 0, 0, 0)          # ALT down: обход блокировки фокуса
    user32.SetForegroundWindow(target_hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.15)


def send_ctrl_v():
    user32.keybd_event(0x11, 0, 0, 0)          # Ctrl down
    user32.keybd_event(0x56, 0, 0, 0)          # V down
    user32.keybd_event(0x56, 0, 2, 0)          # V up
    user32.keybd_event(0x11, 0, 2, 0)          # Ctrl up


# =====================  GUI  =====================
import tkinter as tk

W, H = 360, 52
TRANSP = "#abc123"
PILL = "#1c1c1e"
FG = "#f5f5f7"
BLUE = "#4cc2ff"
GREEN = "#34d399"
RED = "#ff5f57"

root = tk.Tk()
root.title("voice")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.attributes("-transparentcolor", TRANSP)
root.attributes("-alpha", 0.0)
root.config(bg=TRANSP)
root.geometry(f"{W}x{H}+{(root.winfo_screenwidth() - W) // 2}+12")

canvas = tk.Canvas(root, width=W, height=H, bg=TRANSP, highlightthickness=0)
canvas.pack()

r = H // 2
canvas.create_oval(0, 0, 2 * r, H, fill=PILL, outline="#3a3a3c")
canvas.create_oval(W - 2 * r, 0, W, H, fill=PILL, outline="#3a3a3c")
canvas.create_rectangle(r, 0, W - r, H, fill=PILL, outline=PILL)
canvas.create_rectangle(r, 1, W - r, 3, fill="#3a3a3c", outline="#3a3a3c")

icon_id = canvas.create_text(26, H // 2, text="🎙", font=("Segoe UI Emoji", 13))
text_id = canvas.create_text(48, H // 2, anchor="w", text="", font=("Segoe UI", 11), fill=FG)
bar_ids = [canvas.create_rectangle(0, 0, 0, 0, fill=BLUE, state="hidden") for _ in range(5)]

canvas.bind("<Button-1>", lambda e: toggle())


def set_pill(icon, text, color=FG):
    root.after(0, lambda: (canvas.itemconfig(icon_id, text=icon),
                           canvas.itemconfig(text_id, text=text, fill=color)))


def fade_in():
    root.deiconify()
    for a in range(0, 11):
        root.after(a * 12, lambda a=a: root.attributes("-alpha", a / 10))


def fade_out(delay=0):
    def hide():
        for a in range(10, -1, -1):
            root.after((10 - a) * 12, lambda a=a: root.attributes("-alpha", a / 10))
        root.after(130, root.withdraw)
    root.after(delay, hide)


def animate_rec():
    if state != "rec":
        for b in bar_ids:
            canvas.itemconfig(b, state="hidden")
        return
    cy = H // 2
    x0 = W - 96
    for i, bid in enumerate(bar_ids):
        h = random.randint(5, 24)
        canvas.coords(bid, x0 + i * 11, cy - h / 2, x0 + i * 11 + 6, cy + h / 2)
        canvas.itemconfig(b, state="normal", fill=BLUE)
    root.after(110, animate_rec)


def animate_proc(i=0):
    if state != "proc":
        return
    set_pill("⏳", "Распознаю" + "." * (i % 4))
    root.after(250, lambda: animate_proc(i + 1))


def noactivate():
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id())
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)

root.after(50, noactivate)


# =====================  логика записи  =====================
def start_rec():
    global state, audio_chunks, stream_obj
    remember_target()
    audio_chunks = []
    state = "rec"

    def callback(indata, frames, t, status):
        if state == "rec":
            audio_chunks.append(indata.copy())

    stream_obj = sd.InputStream(samplerate=samplerate, channels=1, dtype="float32", callback=callback)
    stream_obj.start()
    beep(880)
    animate_rec()
    set_pill("🎙", "Говорите...", BLUE)


def stop_rec():
    global state, stream_obj
    state = "proc"
    stream_obj.stop()
    stream_obj.close()
    beep(440)
    animate_proc()
    audio = np.concatenate(audio_chunks)[:, 0] if audio_chunks else None
    threading.Thread(target=transcribe_and_paste, args=(audio,), daemon=True).start()


def transcribe_once(audio, lang):
    """Распознаёт с принудительным языком, возвращает текст и уверенность."""
    segments, info = model.transcribe(
        audio, language=lang, beam_size=1,
        without_condition_on_previous_text=True,
    )
    parts, logprobs = [], []
    for s in segments:
        t = s.text.strip()
        if t:
            parts.append(t)
            logprobs.append(s.avg_logprob)
    text = " ".join(parts).strip()
    score = sum(logprobs) / len(logprobs) if logprobs else -99.0
    return text, score, info


def transcribe_and_paste(audio):
    try:
        if audio is None or len(audio) < samplerate * 0.3:
            _done("🤷", "Ничего не услышал")
            return
        text, score, info = transcribe_once(audio, LANGUAGE)
        # низкая уверенность → пробуем второй язык, берём того, кто увереннее
        if LANGUAGE is None and score < -0.55 and len(audio) > samplerate:
            alt = "en" if info.language == "ru" else "ru"
            text2, score2, info2 = transcribe_once(audio, alt)
            if text2 and score2 > score:
                text, info = text2, info2
        if not text:
            _done("🤷", "Ничего не услышал")
            return
        old = None
        try:
            old = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        restore_target()
        time.sleep(0.05)
        send_ctrl_v()
        time.sleep(0.35)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
        short = text[:42] + "..." if len(text) > 42 else text
        icon = "🇬🇧" if (LANGUAGE is None and info.language == "en") else ("🇷🇺" if LANGUAGE is None else "✅")
        _done(icon, short, GREEN)
    except Exception as e:
        _done("❌", str(e)[:42], RED)


def _done(icon, text, color=FG):
    global state
    state = "idle"
    set_pill(icon, text, color)
    fade_out(1600)


def toggle():
    root.after(0, _toggle)   # всё действие — только в главном потоке Tk


def _toggle():
    global state
    if state == "rec":
        stop_rec()
    elif state == "idle":
        if model is None:
            fade_in()
            set_pill("⏳", "Загружаю Whisper...", BLUE)
            return
        fade_in()
        start_rec()


# =====================  свой низкоуровневый хук клавиатуры  =====================
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
VK_LWIN, VK_RWIN, VK_H, VK_Q = 0x5B, 0x5C, 0x48, 0x51
VK_CONTROL, VK_MENU = 0x11, 0x12

win_down = [False]
combo_used = [False]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM,
                              ctypes.POINTER(KBDLLHOOKSTRUCT))


def ll_handler(nCode, wParam, lParam):
    if nCode >= 0:
        vk = lParam.contents.vkCode
        is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

        if vk in (VK_LWIN, VK_RWIN):
            if is_up:
                if combo_used[0]:
                    combo_used[0] = False
                    return 1   # глушим отпускание Win — Пуск не откроется
                win_down[0] = False
            else:
                win_down[0] = True

        elif vk == VK_H and win_down[0]:
            if not is_up:
                combo_used[0] = True
                threading.Thread(target=toggle, daemon=True).start()
            return 1   # H при зажатом Win целиком глушим

        elif vk == VK_Q and not is_up:
            ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
            alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
            if ctrl and alt:
                threading.Thread(target=toggle, daemon=True).start()
                return 1

    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def hook_thread():
    global _proc
    _proc = HOOKPROC(ll_handler)
    hhk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _proc, kernel32.GetModuleHandleW(None), 0)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        pass


threading.Thread(target=hook_thread, daemon=True).start()

root.withdraw()
threading.Thread(target=load_model, daemon=True).start()
root.mainloop()
