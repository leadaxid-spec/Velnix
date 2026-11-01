"""Генератор заготовки устройства: `python new_device.py valve --title "Клапан" [--clone light]`.

Создаёт devices/<mod>.py с тремя группами сигналов (📊 свойство / 🎛 возможность /
⚙️ настройка), регистрирует в devices/__init__.py и печатает следующую команду запуска.
С --clone берёт сигналы существующего семейства как основу.
"""
from __future__ import annotations

import argparse
import keyword
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICES = os.path.join(HERE, "devices")

_EXAMPLE = [
    {"name": "level", "title": "Уровень (показание)", "control": "graph",
     "management": "r", "kind": "property", "value": 0.0, "measure": "",
     "minimum": None, "maximum": None},
    {"name": "enabled", "title": "Включено", "control": "switch",
     "management": "rw", "kind": "capability", "value": 0, "measure": "",
     "minimum": 0, "maximum": 1},
    {"name": "threshold", "title": "Порог", "control": "counter",
     "management": "rw", "kind": "setting", "value": 50, "measure": "",
     "minimum": 0, "maximum": 100},
]


def _ident(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or keyword.iskeyword(name):
        raise SystemExit(f"имя '{name}' — нужно строчными latin/цифрами, с буквы, без дефисов")
    return name


def _class_name(mod: str) -> str:
    return "".join(p.title() for p in mod.split("_")) + "Device"


def _sig_line(s) -> str:
    parts = [repr(s["name"]), repr(s["title"]), repr(s.get("control", "text")),
             repr(s.get("management", "r")), repr(s.get("kind", "property")),
             repr(s.get("value", 0))]
    parts.append(repr(s.get("measure", "")))
    if s.get("minimum") is not None or s.get("maximum") is not None:
        parts += [repr(s.get("minimum")), repr(s.get("maximum"))]
    return "        self.add_signal(Signal(" + ", ".join(parts) + "))"


def build_source(mod: str, title: str, signals: list) -> str:
    cls = _class_name(mod)
    body = "\n".join(_sig_line(s) for s in signals)
    return (
        f'"""{title} — семейство `{mod}` (заготовка new_device.py).\n\n'
        'Три группы: 📊 свойство (читаю) / 🎛 возможность (управляю) / ⚙️ настройка.\n'
        'Правь сигналы, `tick()` и `on_command()` под своё железо.\n"""\n'
        'from __future__ import annotations\n\n'
        'from core import Device, Signal\n\n\n'
        f'class {cls}(Device):\n'
        f'    model = "emul_{mod}"\n'
        f'    title = "{title}"\n\n'
        '    def __init__(self, *args, **kwargs):\n'
        '        super().__init__(*args, **kwargs)\n'
        f'{body}\n\n'
        '    def on_command(self, name, value) -> None:\n'
        '        """TODO: реакция на команды (изменения rw/w сигналов)."""\n'
        '        pass\n\n'
        '    def tick(self, dt: int) -> None:\n'
        '        """TODO: твоя физика. Пример: числовые свойства дрейфуют."""\n'
        '        for s in self.signals.values():\n'
        '            if s.group == "property" and isinstance(s.value, (int, float)):\n'
        '                self.set_value(s.name, s.value + dt * 0)\n'
    )


def scaffold(mod: str, title: str, clone: str | None = None,
             dest_dir: str | None = None) -> tuple:
    """Чистая генерация: пишет devices/<mod>.py, возвращает (путь, код)."""
    mod = _ident(mod)
    dest = dest_dir or DEVICES
    path = os.path.join(dest, f"{mod}.py")
    if os.path.exists(path):
        raise SystemExit(f"уже существует: {path}")
    signals = _clone_signals(clone) if clone else [dict(s) for s in _EXAMPLE]
    code = build_source(mod, title, signals)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path, code


def _clone_signals(clone: str) -> list:
    import sys
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from devices import REGISTRY
    if clone not in REGISTRY:
        raise SystemExit(f"нет семейства '{clone}' в реестре: {sorted(REGISTRY)}")
    dev = REGISTRY[clone](serial="0")
    return [{"name": s.name, "title": s.title, "control": s.control,
             "management": s.management, "kind": s.kind,
             "value": s.value, "measure": s.measure,
             "minimum": s.minimum, "maximum": s.maximum}
            for s in dev.signals.values() if s.name != "fw"]


def register(mod: str) -> bool:
    """Добавить import + запись REGISTRY в devices/__init__.py. True если изменил."""
    init = os.path.join(DEVICES, "__init__.py")
    src = open(init, encoding="utf-8").read()
    cls = _class_name(mod)
    imp = f"from .{mod} import {cls}"
    if imp in src:
        return False
    lines = src.splitlines()
    last_imp = max(i for i, l in enumerate(lines) if l.startswith("from ."))
    lines.insert(last_imp + 1, imp)
    src = "\n".join(lines)
    src = src.replace("REGISTRY = {", f'REGISTRY = {{\n    "{mod}": {cls},', 1)
    open(init, "w", encoding="utf-8").write(src.rstrip() + "\n")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Заготовка нового семейства эмуляторов")
    p.add_argument("name", help="имя семейства, строчными (напр. valve)")
    p.add_argument("--title", default="", help="человеческое название")
    p.add_argument("--clone", default=None, help="взять сигналы существующего семейства")
    args = p.parse_args()
    mod = _ident(args.name)
    title = args.title or mod.title()
    path, _ = scaffold(mod, title, args.clone)
    changed = register(mod)
    print(f"создан: {os.path.relpath(path, HERE)}")
    print("регистрация:", "обновлён devices/__init__.py" if changed else "уже был")
    print("запуск:    python main.py " + mod + " --port 8099")
    print("физика:    открой devices/%s.py — tick() и on_command()" % mod)


if __name__ == "__main__":
    main()
