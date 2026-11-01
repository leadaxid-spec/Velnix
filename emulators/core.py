"""Ядро эмулятора: сигналы Ujin, конечный автомат устройства, приём команд."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse, parse_qs

META_KEYS = ("command", "id", "uniq_id", "token")


def wire_value(sig: "Signal") -> Any:
    """Мина №1 PULL-CLIENT: платформу 0 не пишет (empty()) — на провод 0.001."""
    v = sig.value
    if v == 0 and isinstance(v, (int, float)) and not sig.zero_safe:
        return 0.001
    return v


@dataclass
class Signal:
    name: str
    title: str
    control: str = "text"
    management: str = "r"
    kind: str = "property"
    value: Any = 0
    measure: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    layout: Optional[dict] = None
    zero_safe: bool = False

    @property
    def group(self) -> str:
        """Верхнеуровневая группа для людей: свойство / возможность / настройка."""
        if self.kind == "setting":
            return "setting"
        return "capability" if self.writable else "property"

    @property
    def writable(self) -> bool:
        return self.management in ("rw", "w")

    @property
    def publishable(self) -> bool:
        return self.management in ("r", "rw")

    def as_declaration(self) -> dict:
        out = {
            "name": self.name,
            "title": self.title,
            "control": self.control,
            "management": self.management,
            "kind": self.kind,
            "group": self.group,
        }
        if self.measure:
            out["measure"] = self.measure
        if self.minimum is not None:
            out["min"] = self.minimum
        if self.maximum is not None:
            out["max"] = self.maximum
        if self.layout:
            out["layout"] = self.layout
        return out


class Device:
    """Базовый эмулятор: тики (детерминированные, целые) + очередь грязных сигналов."""

    model = "universal_device"
    title = "Устройство"

    def __init__(self, serial: str = "0", client=None, logger: Callable[[str], None] = print):
        self.serial = str(serial)
        self.client = client
        self.log = logger
        self.signals: dict = {}
        self._dirty: set = set()
        self.tick_count = 0
        self.add_signal(Signal("fw", "Прошивка", "text", "r", "system", "emul-1.0"))

    def add_signal(self, sig: Signal) -> Signal:
        self.signals[sig.name] = sig
        return sig

    def set_value(self, name: str, value: Any) -> None:
        sig = self.signals[name]
        if sig.value != value:
            sig.value = value
            self._dirty.add(name)

    def emit_event(self, name: str, value: Any) -> None:
        sig = self.signals[name]
        sig.value = value
        self._dirty.add(name)

    def clamp(self, name: str, value):
        sig = self.signals[name]
        if sig.minimum is not None and value < sig.minimum:
            value = sig.minimum
        if sig.maximum is not None and value > sig.maximum:
            value = sig.maximum
        return value

    def tick(self, dt: int) -> None:
        raise NotImplementedError

    def on_command(self, name: str, value: Any) -> None:
        pass

    def apply_ws_signal(self, name: str, value: Any) -> None:
        """Сигнал из WS (контракт МП): управляемый — как команда, иначе — удалённое показание."""
        sig = self.signals.get(name)
        if sig is None:
            self.log(f"[{self.model}] ws: неизвестный сигнал {name}")
            return
        if sig.writable:
            try:
                value = float(value) if isinstance(value, str) and "." in value else int(float(value))
            except (TypeError, ValueError):
                pass
            sig.value = value
            self.log(f"[{self.model}] ws-команда: {name}={value}")
            self.on_command(name, value)
        else:
            if not hasattr(self, "remote_values"):
                self.remote_values = {}
            self.remote_values[name] = value

    def handle_recv(self, payload: dict) -> None:
        for item in payload.get("recv", []):
            if item.get("command") != "management":
                continue
            for name, value in item.items():
                if name in META_KEYS:
                    continue
                sig = self.signals.get(name)
                if sig is None:
                    self.log(f"[{self.model}] неизвестный сигнал команды: {name}")
                    continue
                if not sig.writable:
                    self.log(f"[{self.model}] сигнал неуправляем: {name}")
                    continue
                sig.value = value
                if sig.management == "rw":
                    self._dirty.add(name)
                self.log(f"[{self.model}] команда: {name}={value}")
                print("JOURNAL " + json.dumps(
                    {"call": "command", "name": name, "value": value},
                    ensure_ascii=False), flush=True)
                self.on_command(name, value)

    def step(self, dt: int = 1) -> None:
        self.tick_count += 1
        self.tick(dt)
        self.flush()

    def announce(self) -> None:
        for name, sig in self.signals.items():
            if sig.publishable:
                self._dirty.add(name)
        self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        batch = []
        for name in sorted(self._dirty):
            sig = self.signals[name]
            if sig.publishable:
                batch.append({"signal": name,
                              "value": wire_value(sig)})
        self._dirty.clear()
        if batch and self.client is not None:
            self.client.update_signals(self.serial, self.model, batch)

    def declaration(self) -> list:
        return [s.as_declaration() for s in self.signals.values()]


class CommandServer:
    """HTTP-эндпоинт формата сервера опроса Ujin: GET /?data={"recv":[...]}.

    Плюс веб-панель: GET / — таблица сигналов и форма команды, GET /state — JSON.
    """

    def __init__(self, device: Device, port: int = 0, host: str = "0.0.0.0"):
        self.device = device
        dev = device
        srv = self

        def convert(raw: str):
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
            try:
                return float(raw)
            except (TypeError, ValueError):
                return raw

        class Handler(BaseHTTPRequestHandler):
            def _send(self, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, html: str):
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                raw = qs.get("data", [None])[0]
                if raw is not None:
                    try:
                        dev.handle_recv(json.loads(raw))
                        self._send({"error": 0})
                    except Exception as exc:
                        self._send({"error": 2, "message": str(exc)})
                    return
                if parsed.path == "/state":
                    self._send({s.name: s.value for s in dev.signals.values()})
                    return
                if parsed.path == "/declaration":
                    self._send(dev.declaration())
                    return
                if parsed.path == "/shutdown":
                    self._send({"ok": 1})
                    threading.Thread(target=srv.stop, daemon=True).start()
                    return
                if parsed.path == "/":
                    self._send_html(self_dashboard(dev))
                    return
                self._send({"error": 1, "message": "no data"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) or b"{}"
                try:
                    if self.path == "/command":
                        form = parse_qs(body.decode("utf-8"))
                        name = (form.get("signal") or [""])[0]
                        value = convert((form.get("value") or [""])[0])
                        dev.handle_recv({"recv": [{"command": "management",
                                                   "id": dev.serial,
                                                   name: value}]})
                        self._send({"error": 0})
                        return
                    dev.handle_recv(json.loads(body))
                    self._send({"error": 0})
                except Exception as exc:
                    self._send({"error": 2, "message": str(exc)})

            def log_message(self, fmt, *args):
                dev.log(f"[cmd] {fmt % args}")

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self.port = self._httpd.server_address[1]
        self.stopped = False

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start_thread(self) -> threading.Thread:
        th = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        th.start()
        return th

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self._httpd.shutdown()
        self._httpd.server_close()


def self_dashboard(dev: Device) -> str:
    rows = []
    options = []
    for s in dev.signals.values():
        rows.append(f"<tr><td>{s.title}</td><td><code>{s.name}</code></td>"
                    f"<td>{s.value}</td><td>{s.management}</td></tr>")
        if s.writable:
            options.append(f"<option value='{s.name}'>{s.title}</option>")
    form = ""
    if options:
        form = ("<form method='POST' action='/command'>"
                f"<select name='signal'>{''.join(options)}</select>"
                "<input name='value' placeholder='значение'>"
                "<button>Отправить</button></form>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='refresh' content='2'>"
            f"<title>{dev.title} {dev.serial}</title></head><body>"
            f"<h2>{dev.title} <small>{dev.serial}</small></h2>"
            "<table border=1 cellpadding=4><tr><th>title</th><th>signal</th>"
            "<th>value</th><th>mgmt</th></tr>" + "".join(rows) +
            "</table>" + form + "</body></html>")
