"""Лифт: очередь вызовов, движение этаж-за-этажом, события floor_changed, авария."""
from __future__ import annotations

from core import Device, Signal

STOP, UP, DOWN = 0, 1, 2
DOOR_TICKS = 2


class Elevator(Device):
    model = "emul_elevator"
    title = "Лифт"

    def __init__(self, *args, floors: int = 5, move_ticks: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self.floors = floors
        self.move_ticks = move_ticks
        self._target = None
        self._move_left = 0
        self._door_left = 0
        self.add_signal(Signal("call_requested", "Вызвать (этаж)", "combolist", "w",
                               "capability", 0, layout={0: "нет"}))
        self.add_signal(Signal("floor_current", "Текущий этаж", "counter", "r",
                               "property", 1, "", 1, floors))
        self.add_signal(Signal("floor_destination", "Этаж назначения", "counter", "r",
                               "property", 0, "", 0, floors))
        self.add_signal(Signal("direction", "Направление", "list", "r", "property", STOP,
                               layout={STOP: "стоит", UP: "вверх", DOWN: "вниз"}))
        self.add_signal(Signal("speed", "Скорость", "graph", "r", "property", 0.0, "м/с"))
        self.add_signal(Signal("door", "Двери", "list", "r", "property", 0,
                               layout={0: "закрыты", 1: "открыты"}))
        self.add_signal(Signal("floor_changed", "Событие: этаж пройден", "push_time",
                               "r", "property", 0))
        self.add_signal(Signal("emergency", "Авария", "list", "r", "property", 0,
                               layout={0: "норма", 1: "авария"}))
        self.add_signal(Signal("emergency_btn", "Кнопка аварии", "push_time", "w",
                               "capability", 0))
        self.add_signal(Signal("floors_total", "Этажей в шахте", "counter", "r",
                               "system", floors))

    def on_command(self, name: str, value) -> None:
        if name == "call_requested":
            floor = int(value)
            if 1 <= floor <= self.floors and self.signals["emergency"].value == 0:
                self._target = floor
                self.set_value("floor_destination", floor)
                self.log(f"[{self.model}] вызов на этаж {floor}")
        elif name == "emergency_btn":
            new = 0 if self.signals["emergency"].value == 1 else 1
            self.set_value("emergency", new)
            if new == 1:
                self._target = None
                self._move_left = 0
                self.set_value("floor_destination", 0)
                self.set_value("direction", STOP)
                self.set_value("speed", 0.0)
                self.log(f"[{self.model}] АВАРИЯ")
            else:
                self.log(f"[{self.model}] авария сброшена")

    def tick(self, dt: int) -> None:
        if self.signals["emergency"].value == 1:
            self.set_value("speed", 0.0)
            return
        if self._door_left > 0:
            self._door_left -= dt
            if self._door_left <= 0:
                self.set_value("door", 0)
            return
        cur = int(self.signals["floor_current"].value)
        if self._target is None:
            self.set_value("direction", STOP)
            self.set_value("speed", 0.0)
            return
        if cur == self._target:
            self.set_value("direction", STOP)
            self.set_value("speed", 0.0)
            self.set_value("door", 1)
            self._door_left = DOOR_TICKS
            self._target = None
            self.set_value("floor_destination", 0)
            return
        if self._move_left <= 0:
            self.set_value("direction", UP if self._target > cur else DOWN)
            self.set_value("speed", 1.0)
            self._move_left = self.move_ticks
        self._move_left -= 1
        if self._move_left <= 0:
            direction = int(self.signals["direction"].value)
            if direction == UP:
                cur += 1
            elif direction == DOWN:
                cur -= 1
            self.set_value("floor_current", cur)
            self.emit_event("floor_changed", cur)
