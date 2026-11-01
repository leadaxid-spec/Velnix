"""Термостат: гистерезис ±0.5 от цели, пассивный дрейф к окружающей."""
from __future__ import annotations

from core import Device, Signal

OFF, HEAT, COOL, AUTO = 0, 1, 2, 3
HYSTERESIS = 0.5
AMBIENT = 20.0


class Thermostat(Device):
    model = "emul_thermostat"
    title = "Термостат"

    def __init__(self, *args, start_temp: float = 20.0, ambient_pull: float = 0.05,
                 step_per_tick: float = 0.2, **kwargs):
        super().__init__(*args, **kwargs)
        self.ambient_pull = ambient_pull
        self.step_per_tick = step_per_tick
        self._active = 0
        self.add_signal(Signal("temperature", "Температура", "graph", "r", "property",
                               start_temp, "°C"))
        self.add_signal(Signal("target_temp", "Цель", "range", "rw", "setting",
                               22.0, "°C", 5, 35))
        self.add_signal(Signal("mode", "Режим", "list", "rw", "capability", AUTO,
                               layout={OFF: "выкл", HEAT: "нагрев", COOL: "охлаждение",
                                       AUTO: "авто"}))
        self.add_signal(Signal("output", "Нагрев/охлаждение", "list", "r", "property", 0,
                               layout={0: "нет", 1: "нагрев", 2: "охлаждение"}))
        self.add_signal(Signal("humidity", "Влажность", "graph", "r", "property", 45, "%"))
        self.add_signal(Signal("fault", "Авария датчика", "list", "r", "property", 0,
                               layout={0: "нет", 1: "нет данных"}))

    def set_fault(self, on: bool) -> None:
        self.set_value("fault", 1 if on else 0)

    def on_command(self, name: str, value) -> None:
        if name == "target_temp":
            self.set_value("target_temp", self.clamp("target_temp", float(value)))

    def tick(self, dt: int) -> None:
        if self.signals["fault"].value == 1:
            self._active = 0
            self.set_value("output", 0)
            return
        temp = self.signals["temperature"].value
        target = self.signals["target_temp"].value
        mode = int(self.signals["mode"].value)
        if mode == OFF:
            self._active = 0
        else:
            want_heat = mode in (HEAT, AUTO)
            want_cool = mode in (COOL, AUTO)
            if self._active == HEAT and temp >= target + HYSTERESIS:
                self._active = 0
            elif self._active == COOL and temp <= target - HYSTERESIS:
                self._active = 0
            elif self._active == 0 and want_heat and temp <= target - HYSTERESIS:
                self._active = HEAT
            elif self._active == 0 and want_cool and temp >= target + HYSTERESIS:
                self._active = COOL
        if self._active == HEAT:
            temp += self.step_per_tick * dt
        elif self._active == COOL:
            temp -= self.step_per_tick * dt
        else:
            temp += (AMBIENT - temp) * self.ambient_pull * dt
        self.set_value("output", self._active)
        self.set_value("temperature", round(temp, 2))
