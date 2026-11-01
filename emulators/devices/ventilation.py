"""Приточная вентиляция (тип Tion) — семейство «Вентиляция».

Свойства: температура притока, PM2.5, CO2; возможности: скорость вентилятора,
режим; настройки: целевой CO2 и порог авто-режима.
"""
from __future__ import annotations

from core import Device, Signal

OFF, AUTO_M, MANUAL = 0, 1, 2


class Ventilation(Device):
    model = "emul_ventilation"
    title = "Вентиляция"

    def __init__(self, *args, pm25_in: float = 12.0, co2_in: float = 420.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.pm25_in = pm25_in
        self.co2_in = co2_in
        self._speed = 1
        self.add_signal(Signal("supply_temp", "Температура притока", "graph", "r",
                               "property", 21.0, "°C"))
        self.add_signal(Signal("pm25", "PM2.5 на входе", "graph", "r", "property",
                               pm25_in, "мкг/м³"))
        self.add_signal(Signal("co2", "CO2 в помещении", "graph", "r", "property",
                               co2_in, "ppm"))
        self.add_signal(Signal("fan_speed", "Скорость вентилятора", "range", "rw",
                               "capability", 1, "", 0, 6))
        self.add_signal(Signal("mode", "Режим", "list", "rw", "capability", AUTO_M,
                               layout={OFF: "выкл", AUTO_M: "авто", MANUAL: "ручной"}))
        self.add_signal(Signal("co2_target", "Целевой CO2", "counter", "rw",
                               "setting", 800, "ppm", 400, 1400))
        self.add_signal(Signal("heat_pct", "Нагрев", "graph", "r", "property", 30, "%"))

    def on_command(self, name, value) -> None:
        if name == "fan_speed":
            self._speed = int(value)

    def tick(self, dt: int) -> None:
        mode = int(self.signals["mode"].value)
        speed = int(self.signals["fan_speed"].value)
        if mode == OFF:
            speed = 0
        if mode == AUTO_M:
            target = self.signals["co2_target"].value
            co2 = self.signals["co2"].value
            speed = 0 if co2 < target * 0.6 else min(6, int((co2 / target) * 3))
            self.set_value("fan_speed", speed)
        self._speed = speed
        if speed > 0:
            new_co2 = max(self.co2_in, self.signals["co2"].value - speed * 12 * dt)
            new_pm = max(self.pm25_in * 0.2,
                         self.signals["pm25"].value - speed * 0.8 * dt)
        else:
            new_co2 = min(2000.0, self.signals["co2"].value + 25 * dt)
            new_pm = min(self.pm25_in * 3, self.signals["pm25"].value + 0.5 * dt)
        self.set_value("co2", round(new_co2, 0))
        self.set_value("pm25", round(new_pm, 1))
        self.set_value("heat_pct", 0 if speed == 0 else min(100, 10 + speed * 14))
        self.set_value("supply_temp", round(18.0 + self.signals["heat_pct"].value * 0.06, 1))
