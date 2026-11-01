import requests
import json
from typing import Optional, Dict, Any

# --- НАСТРОЙКИ (для теста на стенд тест-ОАЭ) ---
BASE_URL = "https://api-uae-test.ujin.tech/api"

# UST резидента (из твоей таблицы: резидент 1)
UST = "ust-2815113-5a9aee6c4d4ea707aa67a85bec7e5d81"

# ID устройств (из таблицы «Твоё железо»)
THERMOSTAT_EXT_GUID = "velnix_thermostat_01"
VENTILATION_EXT_GUID = "velnix_ventilation_01"  # такого устройства может не быть на стенде

# Serialnumber термостата (для справки, если понадобится в других запросах)
THERMOSTAT_SN = "17037033610008120"


def fetch_device_signals(ext_guid: str) -> Optional[Dict[str, Any]]:
    """
    Получает сигналы устройства по ext_guid.
    Авторизация через параметр ust (как указано в документации стенда).
    """
    url = f"{BASE_URL}/devices/{ext_guid}/signals"
    params = {"ust": UST}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        # Если устройство не найдено — API обычно возвращает 404. Это нормально для теста.
        if resp.status_code == 404:
            print(f"[WARN] Устройство {ext_guid} не найдено на стенде (404).")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Не удалось получить сигналы для устройства {ext_guid}: {e}")
        return None


def print_thermostat_data(data: Dict[str, Any]) -> None:
    print("\n=== ТЕРМОСТАТ ===")
    signals = data.get("signals", {})
    target_temp = signals.get("target_temperature")
    current_temp = signals.get("current_temperature")
    mode = signals.get("mode")
    heating = signals.get("heating")
    power_w = signals.get("power_w")

    print(f"Целевая температура: {target_temp} °C")
    print(f"Текущая температура: {current_temp} °C")
    print(f"Режим: {mode}")
    print(f"Нагрев активен: {heating}")
    print(f"Потребляемая мощность: {power_w} Вт")
    # Вывод всех сигналов в компактном виде
    print("Сигналы (первые 10):")
    for i, (k, v) in enumerate(signals.items()):
        if i >= 10:
            break
        print(f"  {k}: {v}")


def print_ventilation_data(data: Dict[str, Any]) -> None:
    print("\n=== ВЕНТИЛЯЦИЯ ===")
    signals = data.get("signals", {})
    co2 = signals.get("co2")
    supply_temp = signals.get("supply_temperature")
    fan_speed = signals.get("fan_speed")
    mode = signals.get("mode")
    power_w = signals.get("power_w")

    print(f"CO₂: {co2} ppm")
    print(f"Температура притока: {supply_temp} °C")
    print(f"Скорость вентилятора: {fan_speed} (ступень)")
    print(f"Режим: {mode}")
    print(f"Потребляемая мощность: {power_w} Вт")
    print("Сигналы (первые 10):")
    for i, (k, v) in enumerate(signals.items()):
        if i >= 10:
            break
        print(f"  {k}: {v}")


def main():
    # 1. Пробуем получить данные термостата (он точно есть на стенде)
    thermo_data = fetch_device_signals(THERMOSTAT_EXT_GUID)
    if thermo_data:
        print_thermostat_data(thermo_data)
    else:
        print("[WARN] Данные термостата не получены.")

    # 2. Пробуем получить данные вентиляции (её может не быть — это нормально)
    vent_data = fetch_device_signals(VENTILATION_EXT_GUID)
    if vent_data:
        print_ventilation_data(vent_data)
    else:
        print(
            "[INFO] Вентиляция не найдена на стенде. Чтобы протестировать её, "
            "запусти эмулятор командой:\n"
            "python main.py ventilation --port 8099\n"
            "и открой http://localhost:8099"
        )


if __name__ == "__main__":
    main()
