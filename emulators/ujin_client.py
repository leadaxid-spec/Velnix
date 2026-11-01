"""HTTP-клиент API Ujin (import_device). Сухой прогон — без сети, журнал вызовов.

Контракты ручек: docs/swagger/import_device/*.yaml; проверка dev-token:
ihome/import_device.php:50-75 (dev-<serial>-<token>, token = сигнал `token` устройства-якоря).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional


class UjinClient:
    def __init__(self, base_url: str = "https://api-example.ujin.tech",
                 device_token: str = "dev-0-REDACTED",
                 dry_run: bool = True, timeout: float = 5.0, logger=print,
                 cls: str = "", signal_prefix: str = "",
                 ust: str = "", apartment_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.device_token = device_token
        self.dry_run = dry_run
        self.timeout = timeout
        self.log = logger
        self.calls: list = []
        # cls непустой -> публикация group-ом ключами {sn,s,v,c} (evidence §7),
        # имена сигналов с префиксом словаря (integrations_devices_<имя>).
        self.cls = cls
        self.signal_prefix = signal_prefix
        # uae-режим (31.10): честный путь import_device — ust пользователя +
        # apartment_id в QUERY (гейт verif_crm_user_apartment,
        # import_device.php:9-46,81-99). apartment_id в теле гейт НЕ видит —
        # только в query (проверено живьём). ust+apartment_id вместе = uae.
        self.ust = ust
        self.apartment_id = str(apartment_id or "")

    def _uae(self) -> bool:
        return bool(self.ust) and bool(self.apartment_id)

    def _auth_qs(self, serialnumber: str = "") -> str:
        """Строка авторизации import_device: uae — token=ust&apartment_id=…
        (+serialnumber: гейт п.143 требует sn, а serial_sw из ust не приходит);
        preprod — device-token якоря."""
        if self._uae():
            qs = ("token=" + urllib.parse.quote(self.ust)
                  + "&apartment_id=" + urllib.parse.quote(self.apartment_id))
            if serialnumber:
                qs += "&serialnumber=" + urllib.parse.quote(str(serialnumber))
            return qs
        return "device-token=" + urllib.parse.quote(self.device_token)

    def _journal(self, call: str, path: str, rows: list) -> None:
        """Общая с портами строка журнала: JOURNAL {"call",...,"data":[{sn,s,v,c}]}."""
        print("JOURNAL " + json.dumps(
            {"call": call, "path": path, "data": rows}, ensure_ascii=False))

    @staticmethod
    def _norm_rows(path: str, params: dict) -> list:
        if "group-update-device-signal" in path:
            data = params.get("data") or []
            return [{"sn": r.get("serialnumber", r.get("sn", "")),
                     "s": r.get("signal", r.get("s", "")),
                     "v": r.get("value", r.get("v")),
                     "c": r.get("model", r.get("c", ""))} for r in data]
        if "update-device-signal" in path:
            return [{"sn": params.get("serialnumber", ""),
                     "s": params.get("signal", ""),
                     "v": params.get("value"),
                     "c": params.get("model", "")}]
        return []

    def _post(self, path: str, params: dict) -> dict:
        if self._uae():
            # auth в query; device-token/apartment_id из тела вон (гейт body
            # не читает, а device-token=x-y-z — дыра, письмо п.20, не трогать)
            params = {k: v for k, v in params.items()
                      if k not in ("device-token", "apartment_id") and v is not
                      None}
            url = self.base_url + path + "?" + self._auth_qs(
                params.get("serialnumber") or "")
        else:
            params = {"device-token": self.device_token, **{
                k: v for k, v in params.items() if v is not None}}
            url = self.base_url + path
        self.calls.append({"path": path, "params": params})
        call = ("publish" if "update-device-signal" in path
                else "create" if "create-update-device" in path
                else "hide" if "hide-device" in path else "call")
        self._journal(call, path, self._norm_rows(path, params))
        if self.dry_run:
            self.log(f"[dry-run] POST {path} {json.dumps(params, ensure_ascii=False)}")
            return {"error": 0, "dry_run": True}
        data = urllib.parse.urlencode(
            {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
             for k, v in params.items()}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def create_update_device(self, model: str, title: str = "",
                             apartment_id: Optional[int] = None,
                             building_id: Optional[int] = None,
                             device_ext_id: Optional[str] = None,
                             path: Optional[str] = None,
                             mask: Optional[str] = None,
                             custom_data: Optional[dict] = None,
                             serialnumber: Optional[str] = None) -> dict:
        return self._post("/api/import_device/create-update-device/", {
            "model": model,
            "title": title or None,
            "apartment_id": apartment_id,
            "building_id": building_id,
            "device_ext_id": device_ext_id,
            "serialnumber": serialnumber,  # uae: обязателен (гейт п.143)
            "path": path,
            "mask": mask,
            "custom_data": custom_data,
        })

    def _post_json(self, path: str, payload: dict) -> dict:
        rows0 = payload.get("data") or []
        first = rows0[0] if rows0 and isinstance(rows0[0], dict) else {}
        url = (self.base_url + path + "?" + self._auth_qs(
            str(first.get("sn") or first.get("serialnumber") or "")))
        self.calls.append({"path": path, "payload": payload})
        rows = payload.get("data") or []
        call = ("publish" if "update-device-signal" in path
                else "hide" if "hide-device" in path else "call")
        self._journal(call, path, [
            {"sn": r.get("sn", ""), "s": r.get("s", ""),
             "v": r.get("v"), "c": r.get("c", "")}
            for r in rows if isinstance(r, dict)])
        if self.dry_run:
            self.log(f"[dry-run] POST(json) {path} "
                     f"{json.dumps(payload, ensure_ascii=False)}")
            return {"error": 0, "dry_run": True}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def publish_group(self, rows: list, new_short: list = ()) -> dict:
        """Двухфазная публикация group-ом (PULL-CLIENT, мины): сначала
        новые сигналы короткими именами (создают словарь <класс>_<имя>),
        потом полные имена со значениями. c = класс модели, не выдумка."""
        path = "/api/import_device/group-update-device-signal/"
        if new_short:
            self._post_json(path, {"data": list(new_short)})
            if not self.dry_run:
                import time
                time.sleep(2)  # memcache-кэш нуля (мина №2)
        if not rows:
            return {"error": 0}
        return self._post_json(path, {"data": list(rows)})

    def update_signal(self, serialnumber: str, model: str, signal: str, value) -> dict:
        return self._post("/api/import_device/update-device-signal/", {
            "serialnumber": serialnumber,
            "model": model,
            "signal": signal,
            "value": value,
        })

    def update_signals(self, serialnumber: str, model: str, batch: list) -> dict:
        """batch: [{"signal": name, "value": v}, ...] — пачкой."""
        if self.cls:
            return self._post_json(
                "/api/import_device/group-update-device-signal/",
                {"data": [{"sn": serialnumber, "s": self.signal_prefix + i["signal"],
                           "v": i["value"], "c": self.cls} for i in batch]})
        if len(batch) == 1:
            return self.update_signal(serialnumber, model,
                                      batch[0]["signal"], batch[0]["value"])
        return self._post("/api/import_device/group-update-device-signal/", {
            "data": [
                {"serialnumber": serialnumber, "model": model,
                 "signal": item["signal"], "value": item["value"]}
                for item in batch
            ],
        })

    def _get(self, path: str, params: dict) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        self.calls.append({"path": path, "params": params, "method": "GET"})
        if self.dry_run:
            self.log(f"[dry-run] GET {path} {json.dumps(params, ensure_ascii=False)}")
            return {"error": 0, "dry_run": True}
        url = self.base_url + path + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # --- сценарии (autoscripts): сценарий = условие when + действия set_signal ---

    def autoscripts_signals(self, ust_token: str) -> dict:
        """Справочник сигналов квартиры: triggers, buttons, actions, capabilities."""
        return self._get("/api/autoscripts/signals/", {"token": ust_token})

    def autoscripts_edit(self, ust_token: str, scenario_id: int, scenario_type: str,
                         condition: str = "", set_signal: str = "",
                         schedule: str = "", execution_interval: str = "",
                         title: str = "") -> dict:
        """scenario_type: trigger|schedule|press|mode;
        condition: 'when:device-<sn>:<signal>=<value>';
        set_signal: 'device-<sn>:<signal>:<value>[:hold]'."""
        return self._post("/api/autoscripts/edit/", {
            "token": ust_token,
            "id": scenario_id,
            "scenario_type": scenario_type,
            "condition": condition or None,
            "set_signal": set_signal or None,
            "schedule": schedule or None,
            "execution_interval": execution_interval or None,
            "title": title or None,
        })

    def autoscripts_activate(self, ust_token: str, scenario_id: int) -> dict:
        return self._post("/api/autoscripts/activate/", {
            "token": ust_token, "id": scenario_id,
        })

    def autoscripts_list(self, ust_token: str) -> dict:
        return self._get("/api/autoscripts/list/", {"token": ust_token})

    def hide_device(self, serialnumber: str) -> dict:
        return self._post("/api/import_device/hide-device/", {
            "serialnumber": serialnumber,
        })

    # --- приём управляющих сигналов с платформы (поллинг, вариант (c) gap-анализа) ---
    # Платформа пишет команду в signal.signal_task_value; наружу она видна в
    # bmsdevice/get-device-info только для сигналов signal_management<>'r'
    # (HardwareSignalsTrait.php:178,370). Для словарей-'r' команда не доходит —
    # это блокер стенда, не клиента.

    def get_device_info(self, ust_token: str, serialnumber: str) -> dict:
        return self._get("/api/bmsdevice/get-device-info/", {
            "token": ust_token, "serialnumber": serialnumber,
        })

    @staticmethod
    def pending_tasks(info: dict) -> dict:
        """Из ответа get-device-info: {signal_name: task_value} для pending-команд."""
        out = {}
        for s in (info.get("data") or {}).get("signals", []):
            task = s.get("task")
            if task is None:
                task = (s.get("state") or {}).get("task")
            if task is None or task == "":
                continue
            name = s.get("name")
            if name and str(task) != str((s.get("state") or {}).get("value")):
                out[name] = task
        return out
