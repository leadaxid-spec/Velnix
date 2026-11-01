"""Шлагбаум: открытие по команде, авто-закрытие через hold_time, реверс по препятствию."""
from __future__ import annotations

from core import Device, Signal

CLOSED, OPENING, OPEN, CLOSING, HOLD = 0, 1, 2, 3, 4
LAYOUT = {CLOSED: "закрыто", OPENING: "открывается", OPEN: "открыто",
          CLOSING: "закрывается", HOLD: "удержание"}
MOVE_TICKS = 2


class Barrier(Device):
    model = "emul_barrier"
    title = "Шлагбаум"

    def __init__(self, *args, default_hold: int = 10, jam_limit: int = 30, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_hold = default_hold
        self.jam_limit = jam_limit
        self._move_left = 0
        self._open_timer = None
        self._stuck_ticks = 0
        self.add_signal(Signal("open_cmd", "Открыть", "switch", "rw", "capability", 0))
        self.add_signal(Signal("close_cmd", "Закрыть", "push_time", "w", "capability", 0))
        self.add_signal(Signal("hold_time", "Держать открытым, c", "counter", "rw",
                               "setting", default_hold, "c", 0, 300))
        self.add_signal(Signal("state", "Состояние стрелы", "list", "r", "property",
                               CLOSED, layout=LAYOUT))
        self.add_signal(Signal("obstacle", "Предмет под стрелой", "list", "r",
                               "property", 0, layout={0: "нет", 1: "есть"}))
        self.add_signal(Signal("alarm", "Авария", "list", "r", "property", 0,
                               layout={0: "нет", 1: "застревание"}))
        self.add_signal(Signal("cycles", "Циклов открытий", "counter", "r", "property", 0))

    def set_obstacle(self, has: bool) -> None:
        self.set_value("obstacle", 1 if has else 0)

    def on_command(self, name: str, value) -> None:
        if name == "open_cmd" and value == 1:
            self._start_open()
            self.set_value("open_cmd", 0)
        elif name == "close_cmd" and value:
            self._start_close()

    def _start_open(self) -> None:
        if self.signals["state"].value in (CLOSED,):
            self._move_left = MOVE_TICKS
            self._open_timer = None
            self.set_value("state", OPENING)
            self.set_value("alarm", 0)

    def _start_close(self) -> None:
        if self.signals["state"].value in (OPEN, HOLD):
            if self.signals["obstacle"].value == 1:
                self.set_value("state", HOLD)
                return
            self._move_left = MOVE_TICKS
            self.set_value("state", CLOSING)

    def tick(self, dt: int) -> None:
        state = self.signals["state"].value
        if state in (OPENING, CLOSING):
            if self.signals["obstacle"].value == 1 and state == CLOSING:
                self.set_value("state", OPEN)
                self._open_timer = self.signals["hold_time"].value
                self._move_left = 0
                self.log(f"[{self.model}] препятствие — реверс в открыто")
                return
            self._stuck_ticks += dt
            if self._stuck_ticks > self.jam_limit:
                self.set_value("alarm", 1)
                self.set_value("state", HOLD)
                self._move_left = 0
                return
            if self._move_left > 0:
                self._move_left -= 1
            elif self._move_left == 0:
                if state == OPENING:
                    self.set_value("state", OPEN)
                    self.set_value("cycles", self.signals["cycles"].value + 1)
                    self._open_timer = self.signals["hold_time"].value
                else:
                    self.set_value("state", CLOSED)
                self._move_left = -1
                self._stuck_ticks = 0
        elif state == OPEN and self._open_timer is not None:
            self._open_timer -= dt
            if self._open_timer <= 0:
                self._start_close()
                if self.signals["state"].value == OPEN:
                    self._open_timer = None
