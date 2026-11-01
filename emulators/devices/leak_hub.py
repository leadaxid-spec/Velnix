"""Композит: хаб протечки + N внутренних датчиков (внутренняя коммутация вне Ujin).

Датчики шлют хабу сообщения через внутреннюю очередь процесса; в Ujin публикуется
один агрегат: leak (max по датчикам), sensors_online, battery (min).
"""
from __future__ import annotations

from collections import deque

from core import Device, Signal


class InternalSensor:
    def __init__(self, idx: int, hub: "LeakHub"):
        self.idx = idx
        self.hub = hub
        self.wet = False
        self.online = True

    def set_wet(self, wet: bool) -> None:
        self.wet = wet
        self.hub.internal_queue.append((self.idx, "wet", wet))

    def set_online(self, online: bool) -> None:
        self.online = online
        self.hub.internal_queue.append((self.idx, "online", online))


class LeakHub(Device):
    model = "emul_leak_hub"
    title = "Хаб протечки"

    def __init__(self, *args, sensors: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.internal_queue = deque()
        self.sensors = [InternalSensor(i + 1, self) for i in range(sensors)]
        self.add_signal(Signal("leak", "Протечка (агрегат)", "list", "r", "property", 0,
                               layout={0: "нет", 1: "протечка"}))
        self.add_signal(Signal("leak_sensors", "Датчики (битовая маска)", "text", "r",
                               "property", "0"))
        self.add_signal(Signal("sensors_online", "Датчиков онлайн", "counter", "r",
                               "property", sensors, "", 0, sensors))
        self.add_signal(Signal("sensors_total", "Датчиков всего", "counter", "r",
                               "system", sensors))
        self.add_signal(Signal("mute", "Заглушить сирену", "switch", "rw", "capability", 0))
        self.add_signal(Signal("siren", "Сирена", "list", "r", "property", 0,
                               layout={0: "выкл", 1: "звучит"}))

    def sensor(self, idx: int) -> InternalSensor:
        return self.sensors[idx - 1]

    def on_command(self, name: str, value) -> None:
        if name == "mute":
            self.set_value("siren", 0 if int(value) == 1 else self._siren_should())

    def _siren_should(self) -> int:
        if self.signals["mute"].value == 1:
            return 0
        return 1 if self.signals["leak"].value == 1 else 0

    def tick(self, dt: int) -> None:
        while self.internal_queue:
            idx, kind, value = self.internal_queue.popleft()
            self.log(f"[{self.model}] внутренняя коммутация: датчик {idx} {kind}={value}")
        wet_mask = "".join("1" if s.wet else "0" for s in self.sensors)
        online = sum(1 for s in self.sensors if s.online)
        self.set_value("leak", 1 if any(s.wet for s in self.sensors) else 0)
        self.set_value("leak_sensors", wet_mask)
        self.set_value("sensors_online", online)
        self.set_value("siren", self._siren_should())
