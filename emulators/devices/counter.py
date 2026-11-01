"""Счётчик ресурса (электричество/вода/тепло) — семейство «Учёт».

Самое живое на preprod: counter_electricity/heat/cold — 130k устройств.
Свойства: тарифы, мощность; настройка: период отчёта; возможность: отключить
реле нагрузки (умный счётчик).
"""
from __future__ import annotations

from core import Device, Signal

RESOURCES = {
    "electricity": ("Электроэнергия", "кВт·ч", 0.7),
    "cold": ("Холодная вода", "м³", 0.012),
    "hot": ("Горячая вода", "м³", 0.010),
    "heat": ("Теплоэнергия", "Гкал", 0.03),
}


class Counter(Device):
    model = "emul_counter"
    title = "Счётчик"

    def __init__(self, *args, resource: str = "electricity", **kwargs):
        super().__init__(*args, **kwargs)
        name, unit, rate = RESOURCES[resource]
        self.resource = resource
        self._rate = rate
        self.title = f"Счётчик: {name}"
        self.add_signal(Signal("total", f"Показание, {unit}", "counter", "r",
                               "property", 1000.0, unit))
        if resource == "electricity":
            self.add_signal(Signal("kvh_t1", "Тариф 1 (пик)", "graph", "r", "property", 400.0, unit))
            self.add_signal(Signal("kvh_t2", "Тариф 2 (ночь)", "graph", "r", "property", 250.0, unit))
            self.add_signal(Signal("power_w", "Мощность сейчас", "graph", "r", "property", 350.0, "Вт"))
            self.add_signal(Signal("voltage", "Напряжение", "graph", "r", "property", 231.0, "В"))
        else:
            self.add_signal(Signal("flow", "Расход сейчас", "graph", "r", "property", 0.0, f"{unit}/ч"))
        self.add_signal(Signal("battery", "Батарея", "graph", "r", "property", 98, "%"))
        self.add_signal(Signal("online", "Связь", "list", "r", "property", 1,
                               layout={0: "нет", 1: "есть"}))
        self.add_signal(Signal("report_period", "Период отчёта, с", "counter", "rw",
                               "setting", 60, "с", 10, 3600))
        self.add_signal(Signal("load_relay", "Реле нагрузки", "switch", "rw",
                               "capability", 1, layout={0: "отключено", 1: "включено"}))
        self._since_report = 0

    def on_command(self, name, value) -> None:
        if name == "load_relay":
            self.log(f"[{self.model}] нагрузка: {'вкл' if int(value) else 'откл'}")

    def tick(self, dt: int) -> None:
        if self.signals["load_relay"].value == 0:
            self.set_value("power_w", 0.0)
            return
        drift = self._rate * (0.5 + (self.tick_count % 7) / 7) * dt
        self.set_value("total", round(self.signals["total"].value + drift, 3))
        if self.resource == "electricity":
            self.set_value("kvh_t1", round(self.signals["kvh_t1"].value + drift * 0.6, 3))
            self.set_value("kvh_t2", round(self.signals["kvh_t2"].value + drift * 0.4, 3))
            self.set_value("power_w", round(200 + 300 * ((self.tick_count % 11) / 10), 1))
            self.set_value("voltage", round(228 + (self.tick_count % 5), 1))
        else:
            self.set_value("flow", round(drift * 60, 3))
