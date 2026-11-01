"""Админ-бот VelNix: эмуляторы работают прямо в Python, без HTTP-порта."""
import json
import math
import sys
import threading
from datetime import datetime
from pathlib import Path

import telebot
from telebot import types

TOKEN = "ВСТАВЬ_ТОКЕН"
ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "admin_bot" / "journal.log"
sys.path.insert(0, str(ROOT / "emulators"))
from devices import REGISTRY
from ujin_client import UjinClient

with open(ROOT / "доступы.json", encoding="utf-8") as f:
    settings = json.load(f)

DEVICES = {"Датчик протечки": "leak", "Шлагбаум": "barrier",
           "Термостат": "thermostat", "Счётчик": "counter", "Освещение": "light"}
bot = telebot.TeleBot(TOKEN, threaded=False)
lock = threading.Lock()
stop_flag = threading.Event()
thread = None
device = None
name = ""
error_text = ""


def journal(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"BOT {datetime.now():%d.%m %H:%M:%S} | {text}\n")


def readable(signal):
    value = signal.value
    if signal.layout and value in signal.layout:
        value = signal.layout[value]
    return f"{signal.title}: {value} {signal.measure}".strip()


def state():
    if device is None:
        return "Эмулятор ещё не запускался."
    with lock:
        return "\n".join(readable(s) for s in device.signals.values() if s.name != "fw")


def simulate():
    global error_text
    try:
        with lock:
            device.announce()
        while not stop_flag.is_set():
            with lock:
                old = {n: s.value for n, s in device.signals.items()
                       if s.control in ("list", "switch")}
                device.step(1)
                changes = [readable(device.signals[n]) for n in old
                           if old[n] != device.signals[n].value]
            for change in changes:
                journal(f"{name} | {change}")
            stop_flag.wait(2)
    except Exception as e:
        error_text = str(e)
        journal(f"Ошибка {name}: {e}")


def start(chat, selected):
    global device, thread, name, error_text
    if thread is not None and thread.is_alive():
        bot.send_message(chat, "Уже работает: " + name + ". Сначала останови.")
        return
    family = DEVICES[selected]
    info = next((x for x in settings["devices"] if x["family"] == family), None)
    if info is None:
        bot.send_message(chat, "Нет такого устройства в доступах.")
        return
    resident = next((x for x in settings["residents"]
                     if str(x["apartment_id"]) == str(info["apartment_id"])), None)
    if resident is None:
        bot.send_message(chat, "Не найдена авторизация квартиры.")
        return

    base = settings["api_base"].rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    client = UjinClient(base, dry_run=False, cls=info["publish_class"],
                        signal_prefix=info["publish_class"] + "_",
                        ust=resident["ust"], apartment_id=info["apartment_id"])
    device = REGISTRY[family](serial=info["ext"], client=client)
    device.model = info["model"]
    name, error_text = selected, ""
    stop_flag.clear()
    journal("Запуск: " + name)
    thread = threading.Thread(target=simulate, daemon=True)
    thread.start()
    bot.send_message(chat, f"Запуск эмуляции: {name}\nКвартира: {info['apartment_id']}\n"
                           "Показания: /state\nПередача в Ujin OS включена.")


def stop(chat):
    if thread is None or not thread.is_alive():
        bot.send_message(chat, "Нет работающего эмулятора.")
        return
    stop_flag.set()
    thread.join(timeout=7)
    if thread.is_alive():
        bot.send_message(chat, "Останавливается, ожидаем завершения запроса.")
    else:
        journal("Остановка: " + name)
        bot.send_message(chat, "Остановлен: " + name)


def results(chat):
    if thread is not None and thread.is_alive():
        status = "Работает: " + name
    else:
        status = "Эмуляция остановлена" if device else "Эмуляция не запускалась"
    if error_text:
        status += "\nОшибка: " + error_text
    events = []
    if LOG.exists():
        with open(LOG, encoding="utf-8", errors="replace") as f:
            events = [line[4:].strip() for line in f if line.startswith("BOT ")]
    journal_text = "\n".join(events[-6:]) or "Нет событий"
    ujin = ("Ошибка отправки" if error_text else
            "Передача включена, получение не подтверждено")
    bot.send_message(chat, (f"{status}\n\n{state()}\n\nСобытия:\n{journal_text}"
                            f"\n\nUjin OS: {ujin}")[:3900])


def command(chat, text):
    if thread is None or not thread.is_alive():
        bot.send_message(chat, "Сначала запусти эмулятор.")
        return
    parts = text.split()
    if len(parts) != 3:
        bot.send_message(chat, "Формат: /cmd сигнал значение")
        return
    try:
        value = float(parts[2])
    except ValueError:
        bot.send_message(chat, "Значение должно быть числом.")
        return
    with lock:
        signal = device.signals.get(parts[1])
        if signal is None or not signal.writable:
            answer = "Такой управляемый сигнал отсутствует."
        elif (not math.isfinite(value)
              or signal.minimum is not None and value < signal.minimum
              or signal.maximum is not None and value > signal.maximum):
            answer = "Значение за пределами допустимого диапазона."
        elif signal.layout and value not in signal.layout:
            answer = "Допустимые значения: " + ", ".join(map(str, signal.layout))
        elif signal.control == "switch" and value not in (0, 1):
            answer = "Для переключателя нужны 0 или 1."
        else:
            value = int(value) if value.is_integer() else value
            device.handle_recv({"recv": [{"command": "management", parts[1]: value}]})
            journal(f"{name} | Команда: {parts[1]}={value}")
            answer = "Команда передана эмулятору.\n" + readable(signal)
    bot.send_message(chat, answer)


def menu(chat, choose=False):
    kb = types.ReplyKeyboardMarkup()
    if choose:
        for label in DEVICES:
            kb.add(label)
    else:
        kb.add("Тестирование", "Результаты испытаний")
    kb.add("Состояние", "Управление")
    kb.add("Остановить эмуляцию", "Главное меню")
    bot.send_message(chat, "Выбери устройство:" if choose else "Главное меню VelNix",
                     reply_markup=kb)


@bot.message_handler(content_types=["text"])
def handle(message):
    text, chat = message.text.strip(), message.chat.id
    if text in ("/start", "/menu", "Главное меню"):
        menu(chat)
    elif text in ("/test", "Тестирование"):
        menu(chat, True)
    elif text in DEVICES:
        start(chat, text)
    elif text in ("/state", "Состояние"):
        bot.send_message(chat, (name + "\n" + state())[:3900])
    elif text in ("/results", "Результаты испытаний"):
        results(chat)
    elif text in ("/signals", "Управление"):
        if thread is None or not thread.is_alive():
            bot.send_message(chat, "Сначала запусти эмулятор.")
        else:
            with lock:
                lines = [f"{s.name} — {s.title}" for s in device.signals.values() if s.writable]
            bot.send_message(chat, "Управление:\n" + "\n".join(lines)
                             + "\n\nФормат: /cmd сигнал значение")
    elif text.startswith("/cmd"):
        command(chat, text)
    elif text in ("/stop", "Остановить эмуляцию"):
        stop(chat)
    else:
        bot.send_message(chat, "Выбери действие в /menu")


try:
    bot.infinity_polling(skip_pending=True)
finally:
    stop_flag.set()
    if thread is not None:
        thread.join(timeout=7)
