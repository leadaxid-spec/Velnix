"""CLI эмулятора: заведение устройства, цикл тиков, приём команд по HTTP.

Единый контракт «чёрного ящика» для общих тестов (ports/* соблюдают его же):
  флаги: <семейство> --ext --sn --ust --ticks --interval --port --live
         --base --token --new
  stdout: LISTENING <порт> — сервер поднят;
          JOURNAL {"call":"publish|command|...","data":[{sn,s,v,c}]} — каждый
          вызов платформы (dry и live) и каждая применённая команда;
  HTTP:   GET /state, GET /declaration, GET /?data={"recv":[...]},
          POST /command (form signal&value), GET /shutdown — остановить.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from devices import REGISTRY
from core import CommandServer
from ujin_client import UjinClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Эмулятор устройства Ujin")
    p.add_argument("device", choices=sorted(REGISTRY), nargs="?",
                   default="leak", help="семейство (default: leak)")
    p.add_argument("--family", dest="family", default="",
                   choices=[""] + sorted(REGISTRY),
                   help="то же, что позиционный аргумент (единый CLI портов)")
    p.add_argument("--serial", default="", help="серийник на платформе")
    p.add_argument("--ext", default="", help="device_ext_guid (wire sn)")
    p.add_argument("--sn", default="", help="serialnumber для поллинга")
    p.add_argument("--ust", default="", help="ust резидента: поллинг команд")
    p.add_argument("--base-url", "--base", dest="base_url",
                   default=os.environ.get("UJIN_BASE",
                                          "https://api-uae-test.ujin.tech"))
    p.add_argument("--token", default="dev-0-REDACTED", help="dev-token якоря-шлюза")
    p.add_argument("--live", action="store_true", help="реальные HTTP-вызовы (по умолчанию dry-run)")
    p.add_argument("--register", action="store_true", help="завести устройство через create-update-device")
    p.add_argument("--apartment-id", type=int, default=None)
    p.add_argument("--title", default="")
    p.add_argument("--port", type=int, default=0, help="HTTP-порт для команд платформы (0 — выкл)")
    p.add_argument("--interval", type=float, default=1.0, help="секунд на тик")
    p.add_argument("--ticks", type=int, default=0, help="0 — бесконечно")
    p.add_argument("--new", action="append", default=[],
                   help="короткое имя нового сигнала: создать словарём (2 фазы)")
    p.add_argument("--dict-prefix", default="integrations_devices_")
    p.add_argument("--cls", default="",
                   help="класс модели для публикации (integrations_devices) — "
                        "тогда имена сигналов с префиксом словаря")
    p.add_argument("--ust-auth", action="store_true",
                   help="uae-стенд: авторизация import_device под --ust "
                        "+ --apartment-id (вместо dev-token) — устройства "
                        "команды уже созданы орг-нами/провижингом")
    p.add_argument("--model", default="", help="модель (напр. parkcloud_device)")
    p.add_argument("--floors", type=int, default=5, help="лифт: этажей")
    p.add_argument("--resource", default="electricity",
                   choices=["electricity", "cold", "hot", "heat"],
                   help="счётчик: какой ресурс")
    return p


def _poll_once(client, device, args, seen: dict) -> None:
    """Поллинг команд платформы; дедуп по task_dt (или name+value)."""
    info = client.get_device_info(args.ust, args.sn)
    for name, value in UjinClient.pending_tasks(info).items():
        sig = (info.get("data") or {}).get("signals", [])
        dt = ""
        for s in sig:
            if (s.get("name") or s.get("signal_name")) == name:
                dt = str(s.get("signal_task_dt") or s.get("task_dt")
                         or s.get("dt") or "")
                break
        key = dt or f"{name}={value}"
        if seen.get(name) == key:
            continue
        seen[name] = key
        short = (name[len(args.dict_prefix):]
                 if name.startswith(args.dict_prefix) else name)
        device.handle_recv({"recv": [{"command": "management", "id": name,
                                      short: value}]})


def main() -> None:
    args = build_parser().parse_args()
    family = args.family or args.device
    client = UjinClient(args.base_url, args.token, dry_run=not args.live,
                        cls=args.cls,
                        signal_prefix=args.cls + "_" if args.cls else "",
                        ust=args.ust if args.ust_auth else "",
                        apartment_id=(args.apartment_id or "")
                        if args.ust_auth else "")
    kwargs = {}
    if family == "elevator":
        kwargs["floors"] = args.floors
    if family == "counter":
        kwargs["resource"] = args.resource
    device = REGISTRY[family](serial=args.serial or args.ext or "0",
                              client=client, **kwargs)
    if args.model:
        device.model = args.model

    if args.register:
        resp = client.create_update_device(model=device.model,
                                           title=args.title or device.title,
                                           apartment_id=args.apartment_id,
                                           device_ext_id=args.ext or args.serial,
                                           serialnumber=(args.ext or args.serial)
                                           if args.ust_auth else None)
        if isinstance(resp, dict) and resp.get("result"):
            device.serial = str(resp["result"])
            print(f"серийник на платформе: {device.serial}")

    for name in args.new:
        client.publish_group([], new_short=[
            {"sn": args.ext or device.serial, "s": name, "v": 0.001,
             "c": args.cls or device.model}])
        if args.live:
            time.sleep(3)  # memcache-кэш нуля (PULL-CLIENT, мина №2)
    device.announce()

    server = None
    if args.port:
        server = CommandServer(device, port=args.port)
        server.start_thread()
        print(f"LISTENING {server.port}", flush=True)

    wire_sn = args.ext or device.serial
    seen: dict = {}
    try:
        done = 0
        while True:
            device.step(1)
            if args.ust and args.sn:
                try:
                    _poll_once(client, device, args, seen)
                except Exception as exc:
                    print(f"[poll] {exc}")
            done += 1
            if args.ticks and done >= args.ticks:
                break
            if server and server.stopped:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.stop()


if __name__ == "__main__":
    main()
