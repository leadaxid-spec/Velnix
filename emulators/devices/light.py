"""Свет/реле (тип dinrelay_m4, sdimmer_m32) — семейство «Свет».

Свойства: состояние, мощность; возможность: вкл/выкл, яркость; настройка:
таймер авто-выключения.
"""
from __future__ import annotations

from core import Device, Signal


class Light(Device):
    model = "emul_light"
    title = "Светильник"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timer = 0
        self.add_signal(Signal("lamp", "Свет", "switch", "rw", "capability", 0,
                               layout={0: "выкл", 1: "вкл"}))
        self.add_signal(Signal("brightness", "Яркость", "range", "rw", "capability",
                               100, "%", 1, 100))
        self.add_signal(Signal("power_w", "Мощность", "graph", "r", "property", 0.0, "Вт"))
        self.add_signal(Signal("energy_kwh", "Энергия", "counter", "r", "property",
                               0.0, "кВт·ч"))
        self.add_signal(Signal("offline_timeout", "Авто-выкл через, с (0=выкл)",
                               "counter", "rw", "setting", 0, "с", 0, 3600))

    def on_command(self, name, value) -> None:
        if name == "lamp":
            if int(value):
                timeout = int(self.signals["offline_timeout"].value)
                self._timer = timeout if timeout > 0 else 0
            else:
                self._timer = 0

    def tick(self, dt: int) -> None:
        on = int(self.signals["lamp"].value) == 1
        if on:
            self.set_value("power_w", round(12 * self.signals["brightness"].value / 100, 1))
            kwh = self.signals["energy_kwh"].value + self.signals["power_w"].value * dt / 3.6e6
            self.set_value("energy_kwh", round(kwh, 6))
            if self._timer > 0:
                self._timer -= dt
                if self._timer <= 0:
                    self.set_value("lamp", 0)
                    self.log(f"[{self.model}] авто-выключение по таймеру")
        else:
            self.set_value("power_w", 0.0)
