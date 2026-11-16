# -*- coding: utf-8 -*-
r"""
Voice Pill — локальный голосовой ввод в стиле Windows 11.

Win+H          - показать плашку и начать запись
Win+H ещё раз  - остановить, распознать, вставить текст в окно, где ты был
Клик по плашке - то же самое (фокус она не забирает)

Откат: удалить папку D:\AI\voice-input, ярлыки на рабочем столе и в shell:startup.
"""
import ctypes
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

MODEL_SIZE = "base"
LANGUAGE = "ru"

user32 = ctypes.windll.user32

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
target_hwnd = None      # окно, куда вставлять текст


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
    if not target_hwnd:
        return
    if user32.GetForegroundWindow() == target_hwnd:
        return
    user32.keybd_event(0x12, 0, 0, 0)          # ALT down: обход блокировки смены фокуса
    user32.SetForegroundWindow(target_hwnd)
    user32.keybd_event(0x12, 0, 2, 0)          # ALT up
    time.sleep(0.15)


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
        canvas.itemconfig(bid, state="normal", fill=BLUE)
    root.after(110, animate_rec)


def animate_proc(i=0):
    if state != "proc":
        return
    set_pill("⏳", "Распознаю" + "." * (i % 4))
    root.after(250, lambda: animate_proc(i + 1))


# окно не забирает фокус и не видно в панели задач
def noactivate():
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id())
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)

root.after(50, noactivate)


# =====================  запись и распознавание  =====================
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


def transcribe_and_paste(audio):
    global state
    try:
        if audio is None or len(audio) < samplerate * 0.3:
            state = "idle"
            set_pill("🤷", "Ничего не услышал")
            fade_out(1200)
            return
        segments, _ = model.transcribe(
            audio, language=LANGUAGE, beam_size=1,
            without_condition_on_previous_text=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            state = "idle"
            set_pill("🤷", "Ничего не услышал")
            fade_out(1200)
            return
        old = None
        try:
            old = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        restore_target()
        time.sleep(0.05)
        import keyboard
        keyboard.send("ctrl+v")
        time.sleep(0.35)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
        state = "idle"
        short = text[:42] + "..." if len(text) > 42 else text
        set_pill("✅", short, GREEN)
        fade_out(1600)
    except Exception as e:
        state = "idle"
        set_pill("❌", str(e)[:42], RED)
        fade_out(2000)


def toggle():
    global state
    # трюк против меню Пуск: Win видит "другую" клавишу и не открывает Пуск на отпускании
    threading.Thread(target=lambda: keyboard.press_and_release("f15"), daemon=True).start()
    if state == "rec":
        stop_rec()
    elif state == "idle":
        if model is None:
            fade_in()
            set_pill("⏳", "Загружаю Whisper...", BLUE)
            return
        fade_in()
        start_rec()


# =====================  хоткеи  =====================
import keyboard

keyboard.add_hotkey("win+h", toggle, suppress=True)
keyboard.add_hotkey("ctrl+alt+q", toggle, suppress=True)

root.withdraw()
threading.Thread(target=load_model, daemon=True).start()
root.mainloop()
