"""Датчик протечки: wet-автомат + батарея + самотест. Шаблон — device-templates.md."""
from __future__ import annotations

from core import Device, Signal


class LeakSensor(Device):
    model = "emul_leak_sensor"
    title = "Датчик протечки"

    def __init__(self, *args, battery_drain: float = 0.05, **kwargs):
        super().__init__(*args, **kwargs)
        self.battery_drain = battery_drain
        self.pending_wet = False
        self.add_signal(Signal("leak", "Протечка", "list", "r", "property", 0,
                               layout={0: "нет", 1: "протечка"}))
        self.add_signal(Signal("battery", "Батарея", "counter", "r", "property",
                               100, "%", 0, 100))
        self.add_signal(Signal("tamper", "Вскрытие", "list", "r", "property", 0,
                               layout={0: "закрыт", 1: "вскрыт"}))
        self.add_signal(Signal("self_test", "Самотест", "push_time", "w", "capability", 0))

    def set_wet(self, wet: bool) -> None:
        self.pending_wet = wet

    def on_command(self, name: str, value) -> None:
        if name == "self_test" and value:
            self.pending_wet = False
            self.log(f"[{self.model}] самотест: датчик сухой")

    def tick(self, dt: int) -> None:
        wet_now = 1 if self.pending_wet else 0
        if self.signals["leak"].value != wet_now:
            self.set_value("leak", wet_now)
            self.log(f"[{self.model}] протечка={wet_now}")
        battery = round(self.signals["battery"].value - self.battery_drain * dt, 2)
        self.set_value("battery", self.clamp("battery", battery))
