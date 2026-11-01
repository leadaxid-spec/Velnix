"""Живой замкнутый цикл: поллинг команды с платформы -> исполнение FSM -> подтверждение.

Команда = signal_task_value (видна в get-device-info только для rw-сигналов;
для словарей-'r' платформа команду не отдаёт — см. README, раздел «блокеры»).

Запуск (реальный стенд):
  $env:UJIN_BASE='https://api-uae-test.ujin.tech'
  python live_loop.py --sn <serialnumber> --ust <ust-token> --ext <device_ext_guid> \
      --model parkcloud_device --class integrations_devices --dict-prefix integrations_devices_

Сухой прогон (по умолчанию): печатает вызовы, сеть не трогает.
"""
from __future__ import annotations

import argparse
import os
import time

from ujin_client import UjinClient


def task_key(info: dict, name: str) -> str:
    """Дедуп-ключ задачи: signal_task_dt, при его отсутствии name=value
    (тот же приём, что main.py:_poll_once — один task исполняется один раз)."""
    for s in (info.get("data") or {}).get("signals", []):
        if (s.get("name") or s.get("signal_name")) == name:
            dt = str(s.get("signal_task_dt") or s.get("task_dt")
                     or s.get("dt") or "")
            break
    return dt


def run_once(client: UjinClient, ust: str, sn: str, ext: str, model: str,
             cls: str, dict_prefix: str, apply_fn, seen: dict = None) -> list:
    """Один такт: прочитал задачи -> отсёк уже исполненные (seen) ->
    исполнил -> подтвердил сигналом. apply_fn(signal_without_prefix) ->
    подтверждается значение '1'."""
    if seen is None:
        seen = {}
    info = client.get_device_info(ust, sn)
    tasks = UjinClient.pending_tasks(info)
    done = []
    for name, value in tasks.items():
        key = task_key(info, name) or f"{name}={value}"
        if seen.get(name) == key:
            continue  # та же задача, что уже исполнена (мина PULL-CLIENT)
        seen[name] = key
        short = name[len(dict_prefix):] if name.startswith(dict_prefix) else name
        apply_fn(short, value)
        client.update_signals(sn, model, [{
            "signal": name, "value": value,
        }])
        done.append(name)
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sn", required=True)
    ap.add_argument("--ust", default="")
    ap.add_argument("--ext", default="")
    ap.add_argument("--model", default="parkcloud_device")
    ap.add_argument("--class", dest="cls", default="integrations_devices")
    ap.add_argument("--dict-prefix", default="integrations_devices_")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    client = UjinClient(base_url=os.environ.get(
                            "UJIN_BASE", "https://api-uae-test.ujin.tech"),
                        device_token="", dry_run=not args.live)

    def apply(short, value):
        print(f"  исполняю команду: {short} = {value}")

    seen: dict = {}  # дедуп между тактами (R-06)
    while True:
        done = run_once(client, args.ust, args.sn, args.ext, args.model,
                        args.cls, args.dict_prefix, apply, seen)
        if done:
            print("подтверждено:", done)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
