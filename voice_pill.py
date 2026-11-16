# -*- coding: utf-8 -*-
r"""
Голосовой ввод в стиле Windows 11 (плашка сверху по центру).

Win+H  - показать плашку и начать запись
Win+H  - ещё раз: остановить, распознать, вставить текст в активное поле
Клик по плашке - то же самое, что Win+H (фокус не забирает)

Откат: удалить папку D:\AI\voice-input, ярлык на рабочем столе
и ярлык в shell:startup. Ни одного изменения в реестре.
"""
import ctypes
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
stream_obj = None


def beep(freq, dur=110):
    threading.Thread(target=lambda: winsound.Beep(freq, dur), daemon=True).start()


def load_model():
    global model
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")


# =====================  GUI  =====================
import tkinter as tk

W, H = 330, 46
TRANSP = "#abc123"

root = tk.Tk()
root.title("voice")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.attributes("-transparentcolor", TRANSP)
root.config(bg=TRANSP)
root.geometry(f"{W}x{H}+{(root.winfo_screenwidth() - W) // 2}+10")

canvas = tk.Canvas(root, width=W, height=H, bg=TRANSP, highlightthickness=0)
canvas.pack()

r = H // 2
PILL = "#1f1f1f"
DOT = "#4cc2ff"
canvas.create_oval(0, 0, 2 * r, H, fill=PILL, outline=PILL)
canvas.create_oval(W - 2 * r, 0, W, H, fill=PILL, outline=PILL)
canvas.create_rectangle(r, 0, W - r, H, fill=PILL, outline=PILL)
icon_id = canvas.create_text(24, H // 2, text="🎙", font=("Segoe UI Emoji", 13), fill="white")
text_id = canvas.create_text(46, H // 2, anchor="w", text="", font=("Segoe UI", 11), fill="#f0f0f0")

canvas.bind("<Button-1>", lambda e: toggle())


def set_pill(icon, text, color="#f0f0f0"):
    root.after(0, lambda: (canvas.itemconfig(icon_id, text=icon),
                           canvas.itemconfig(text_id, text=text, fill=color)))


# окно не забирает фокус и не видно в панели задач
def noactivate():
    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)

root.after(50, noactivate)


# =====================  запись  =====================
def start_rec():
    global state, audio_chunks, stream_obj
    audio_chunks = []
    state = "rec"

    def callback(indata, frames, t, status):
        if state == "rec":
            audio_chunks.append(indata.copy())

    stream_obj = sd.InputStream(samplerate=samplerate, channels=1, dtype="float32", callback=callback)
    stream_obj.start()
    beep(880)
    animate_rec()


def animate_rec(i=0):
    if state != "rec":
        return
    dots = ["●", "● ●", "● ● ●", "● ●", "●"][i % 5]
    set_pill("🎙", f"Говорите  {dots}", DOT)
    root.after(220, lambda: animate_rec(i + 1))


def stop_rec():
    global state, stream_obj
    state = "proc"
    stream_obj.stop()
    stream_obj.close()
    beep(440)
    set_pill("⏳", "Распознаю...")
    audio = np.concatenate(audio_chunks)[:, 0] if audio_chunks else None
    threading.Thread(target=transcribe_and_paste, args=(audio,), daemon=True).start()


def transcribe_and_paste(audio):
    global state
    try:
        if audio is None or len(audio) < samplerate * 0.3:
            finish("🤷", "Ничего не услышал", hide_after=1200)
            return
        segments, _ = model.transcribe(audio, language=LANGUAGE, beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            finish("🤷", "Ничего не услышал", hide_after=1200)
            return
        old = None
        try:
            old = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        import keyboard
        keyboard.send("ctrl+v")
        time.sleep(0.35)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
        short = text[:46] + "..." if len(text) > 46 else text
        finish("✅", short, hide_after=1600)
    except Exception as e:
        finish("❌", str(e)[:40], hide_after=2000)


def finish(icon, text, hide_after):
    global state
    state = "idle"
    set_pill(icon, text)
    root.after(hide_after, hide)


def hide():
    root.withdraw()


def toggle():
    global state
    if state == "rec":
        stop_rec()
    elif state == "idle":
        if model is None:
            root.deiconify()
            set_pill("⏳", "Загружаю Whisper...")
            return
        root.deiconify()
        start_rec()
    # state == "proc" -> игнорируем нажатия


# =====================  хоткей  =====================
import keyboard

keyboard.add_hotkey("win+h", toggle, suppress=True)   # перехватываем виндовый Win+H
keyboard.add_hotkey("ctrl+alt+q", toggle, suppress=True)

root.withdraw()   # стартует скрытой, Win+H показывает
threading.Thread(target=load_model, daemon=True).start()
root.mainloop()
