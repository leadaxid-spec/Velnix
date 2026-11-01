"""Пожарный датчик (тип ИП 212/64 по fs_* моделям) — семейство «ПОЖС».

Свойства: задымлённость, дежурный режим, тампер, батарея; возможность: самотест;
настройка: порог срабатывания.
"""
from __future__ import annotations

from core import Device, Signal


class FireDetector(Device):
    model = "emul_fire_detector"
    title = "Пожарный датчик"

    def __init__(self, *args, threshold: int = 60, **kwargs):
        super().__init__(*args, **kwargs)
        self._smoke = 5
        self.add_signal(Signal("smoke", "Задымлённость", "graph", "r", "property",
                               5, "%"))
        self.add_signal(Signal("alarm", "Пожар", "list", "r", "property", 0,
                               layout={0: "нет", 1: "ЕСТЬ"}))
        self.add_signal(Signal("duty", "Дежурный режим", "list", "r", "property", 1,
                               layout={0: "нет", 1: "есть"}))
        self.add_signal(Signal("tamper", "Вскрытие корпуса", "list", "r", "property", 0,
                               layout={0: "нет", 1: "есть"}))
        self.add_signal(Signal("battery", "Батарея", "graph", "r", "property", 100, "%"))
        self.add_signal(Signal("fault", "Неисправность", "list", "r", "property", 0,
                               layout={0: "нет", 1: "есть"}))
        self.add_signal(Signal("threshold", "Порог срабатывания", "counter", "rw",
                               "setting", threshold, "%", 10, 95))
        self.add_signal(Signal("self_test", "Самотест", "button", "w", "capability", 0))

    def set_smoke(self, level: int) -> None:
        """Рука демо: подать дым (0..100)."""
        self._smoke = max(0, min(100, level))

    def on_command(self, name, value) -> None:
        if name == "self_test" and int(value):
            self.log(f"[{self.model}] самотест: {'пройден' if not self.signals['fault'].value else 'ошибка'}")

    def tick(self, dt: int) -> None:
        self.set_value("smoke", self._smoke)
        fire = self._smoke >= int(self.signals["threshold"].value)
        self.set_value("alarm", 1 if fire else 0)
        self.set_value("duty", 0 if fire else 1)
        if self._smoke > 0:
            self._smoke = max(0, self._smoke - 1)
