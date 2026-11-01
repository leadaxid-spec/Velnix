import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from room_emulators.runtime import RoomRuntime
from client_bridge import BridgePublisher


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")

API_URL = f"https://api.telegram.org/bot{TOKEN}"
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "velnix_client_bot")
VENTILATION_EMULATOR_URL = os.environ.get(
    "VENTILATION_EMULATOR_URL",
    "http://127.0.0.1:8099",
)
THERMOSTAT_EMULATOR_URL = os.environ.get(
    "THERMOSTAT_EMULATOR_URL",
    "http://127.0.0.1:8100",
)

THERMOSTAT_STATE = {
    "temperature": 20.0,
    "target_temp": 22,
    "mode": "auto",
    "output": 0,
    "humidity": 45,
    "fault": 0,
}

WAITING_FOR_CUSTOM_TEMPERATURE = set()

VENTILATION_STATE = {
    "supply_temp": 21.0,
    "pm25": 12.0,
    "co2": 420.0,
    "fan_speed": 1,
    "mode": "auto",
    "co2_target": 800,
    "heat_pct": 30,
}

WAITING_FOR_CUSTOM_CO2 = set()

ACTIVE_PROGRAM = None
PROGRAM_SNAPSHOT = None
CUSTOM_PROGRAMS = {}
PROGRAM_CREATION = {}
PROGRAMS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "programs.json",
)
EVENTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "events.json",
)
EVENTS = {}
USERS = {}
ROOMS = {}
ROOM_SETUP = {}
ROOMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rooms.json")
DEVICE_TYPES = {
    "thermostat": "🌡 Термостат",
    "ventilation": "🌬 Приточная вентиляция",
    "humidifier": "💧 Увлажнитель",
    "air_conditioner": "❄️ Кондиционер",
}
ROOM_RUNTIME = None
ROOM_VALUE_INPUT = {}
ROOM_CLIMATE_VIEW = {}
DEVICE_LABELS = {"thermostat": "Термостат", "ventilation": "Приточная вентиляция",
                 "humidifier": "Увлажнитель", "air_conditioner": "Кондиционер"}
CLIMATE_SECTIONS = {
    "temperature": ("🌡 Температура", {"thermostat", "air_conditioner"},
                    {"temperature", "target_temp", "mode", "output", "fan_speed", "fault"}),
    "humidity": ("💧 Влажность", {"thermostat", "air_conditioner", "humidifier"},
                 {"humidity", "target_humidity", "intensity", "water_level", "output", "fault"}),
    "ventilation": ("🌬 Вентиляция", {"ventilation"},
                    {"mode", "fan_speed", "co2_target", "co2", "supply_temp", "heat_pct"}),
    "air": ("🫧 Качество воздуха", {"ventilation"}, {"co2", "pm25", "co2_target"}),
}
REGISTRATION = {}
USERS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "users.json",
)
ACCESS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "access_config.json",
)
ACCESS_CODES = {}
INVITES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "invites.json",
)
INVITES = {}


def save_json_file(path, data):
    temporary_file = path + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, path)


def load_users():
    global USERS

    if not os.path.exists(USERS_FILE):
        USERS = {}
        return

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as file:
            saved_users = json.load(file)
        USERS = {
            int(chat_id): user
            for chat_id, user in saved_users.items()
            if isinstance(user, dict)
        }
    except (OSError, ValueError, TypeError):
        USERS = {}
        print("Не удалось прочитать users.json")


def save_users():
    save_json_file(USERS_FILE, USERS)


def load_rooms():
    global ROOMS
    if not os.path.exists(ROOMS_FILE):
        ROOMS = {}
        return
    with open(ROOMS_FILE, encoding="utf-8") as file:
        ROOMS = json.load(file)
    if not isinstance(ROOMS, dict):
        raise ValueError("Некорректный rooms.json")
    changed = False
    for rooms in ROOMS.values():
        for room in rooms:
            counts = {}
            for device in room["devices"]:
                kind = device["type"]
                counts[kind] = counts.get(kind, 0) + 1
                label = DEVICE_LABELS.get(kind, kind)
                suffix = f" {counts[kind]}" if counts[kind] > 1 else ""
                name = f"{room['name']} · {label}{suffix}"
                if device["name"] != name:
                    device.setdefault("previous_name", device["name"])
                    device["name"] = name
                    changed = True
    if changed:
        save_json_file(ROOMS_FILE, ROOMS)


def apartment_key(chat_id):
    user = USERS[chat_id]
    return json.dumps(
        [user["home"].strip().casefold(), user["apartment"].strip().casefold()],
        ensure_ascii=False,
    )


def apartment_rooms(chat_id):
    return ROOMS.get(apartment_key(chat_id), [])


def find_room(chat_id, room_id):
    return next((room for room in apartment_rooms(chat_id) if room["id"] == room_id), None)


def find_room_device(chat_id, room_id, device_id):
    if USERS.get(chat_id, {}).get("role") != "resident":
        return None, None
    room = find_room(chat_id, room_id)
    device = next((item for item in room["devices"] if item["id"] == device_id), None) if room else None
    return room, device


def get_room_runtime():
    global ROOM_RUNTIME
    if ROOM_RUNTIME is None:
        ROOM_RUNTIME = RoomRuntime()
    return ROOM_RUNTIME


def show_room_device(chat_id, room, record, prefix="", section=None):
    runtime = get_room_runtime()
    model = runtime.get(record)
    state = runtime.state(record)
    lines = [prefix, record["name"], f"Комната: {room['name']}",
             f"Тип: {DEVICE_TYPES[record['type']]}", "🟢 Локальный эмулятор", ""]
    rows = []
    for name, signal in model.signals.items():
        if name == "fw":
            continue
        if section and name not in climate_fields(section, record["type"]):
            continue
        value = state[name]
        shown = signal.layout.get(value, str(value)) if signal.layout else f"{value:g}" if isinstance(value, (int, float)) else str(value)
        title = "Работа" if name == "output" and record["type"] == "thermostat" else signal.title
        lines.append(f"{title}: {shown} {signal.measure}".strip())
        if section and signal.writable and signal.layout:
            options = [
                {"text": title, "callback_data": f"roomctl:{room['id']}:{record['id']}:{name}:{key}"}
                for key, title in signal.layout.items()
                if not (section in ("humidity", "air") and title.strip().casefold() == "ручной")
            ]
            rows.extend([options[index:index + 2] for index in range(0, len(options), 2)])
        elif section and signal.writable:
            rows.append([{
                "text": "✏️ " + signal.title,
                "callback_data": f"roomval:{room['id']}:{record['id']}:{name}",
            }])
    if section:
        ROOM_CLIMATE_VIEW[chat_id] = (room["id"], record["id"], section)
        rows.extend([
            [{"text": "🔄 Обновить", "callback_data": f"cldev:{section}:{room['id']}:{record['id']}"}],
            [{"text": "⬅️ К приборам комнаты", "callback_data": f"clroom:{section}:{room['id']}"}],
            [{"text": "⬅️ К климату", "callback_data": "climate:menu"}],
        ])
    else:
        ROOM_CLIMATE_VIEW.pop(chat_id, None)
        lines.append("\nДля регулировки откройте раздел «Климат».")
        rows.extend([
            [{"text": "🌿 Климат", "callback_data": "climate:menu"}],
            [{"text": "⬅️ В комнату", "callback_data": "rooms:open:" + room["id"]}],
        ])
    send_message(chat_id, "\n".join(lines).strip(), {"inline_keyboard": rows})


def climate_fields(section, kind):
    fields = set(CLIMATE_SECTIONS[section][2])
    if section == "humidity" and kind in ("humidifier", "air_conditioner"):
        fields.add("mode")
    return fields


def selected_climate_section(chat_id, room, record):
    view = ROOM_CLIMATE_VIEW.get(chat_id)
    return view[2] if view and view[:2] == (room["id"], record["id"]) else None


def show_climate_rooms(chat_id, section):
    allowed_types = CLIMATE_SECTIONS[section][1]
    rooms = [room for room in apartment_rooms(chat_id)
             if any(device["type"] in allowed_types for device in room["devices"])]
    rows = [[{"text": room["name"], "callback_data": f"clroom:{section}:{room['id']}"}] for room in rooms]
    rows.extend([
        [{"text": "🏠 Добавить оборудование", "callback_data": "rooms:list"}],
        [{"text": "⬅️ К климату", "callback_data": "climate:menu"}],
    ])
    text = CLIMATE_SECTIONS[section][0] + "\n\n"
    text += "Выберите комнату:" if rooms else "Подходящих устройств пока нет. Добавьте прибор в разделе «Мой дом»."
    send_message(chat_id, text, {"inline_keyboard": rows})


def process_climate_navigation(chat_id, data):
    section = data.split(":")[1] if ":" in data else ""
    category = data.startswith("climate:") and section in CLIMATE_SECTIONS
    if not category and not data.startswith(("clroom:", "cldev:")):
        return False
    if USERS.get(chat_id, {}).get("role") != "resident":
        send_message(chat_id, "Управление климатом квартиры доступно только жильцу.")
        return True
    ROOM_VALUE_INPUT.pop(chat_id, None)
    ROOM_SETUP.pop(chat_id, None)
    ROOM_CLIMATE_VIEW.pop(chat_id, None)
    if section not in CLIMATE_SECTIONS:
        return True
    if category:
        show_climate_rooms(chat_id, section)
        return True
    parts = data.split(":")
    if parts[0] == "clroom" and len(parts) == 3:
        room = find_room(chat_id, parts[2])
        if not room:
            send_message(chat_id, "Комната недоступна.")
            return True
        devices = [device for device in room["devices"] if device["type"] in CLIMATE_SECTIONS[section][1]]
        rows = [[{"text": device["name"], "callback_data": f"cldev:{section}:{room['id']}:{device['id']}"}]
                for device in devices]
        rows.append([{"text": "⬅️ К комнатам", "callback_data": "climate:" + section}])
        send_message(chat_id, f"{CLIMATE_SECTIONS[section][0]} · {room['name']}\nВыберите прибор:", {"inline_keyboard": rows})
    elif parts[0] == "cldev" and len(parts) == 4:
        room, record = find_room_device(chat_id, parts[2], parts[3])
        if record and record["type"] in CLIMATE_SECTIONS[section][1]:
            show_room_device(chat_id, room, record, section=section)
        else:
            send_message(chat_id, "Подходящее устройство недоступно.")
    return True


def process_room_control(chat_id, data):
    if not data.startswith(("roomctl:", "roomval:")):
        return False
    parts = data.split(":")
    if len(parts) not in (4, 5):
        return True
    room, record = find_room_device(chat_id, parts[1], parts[2])
    if record is None:
        send_message(chat_id, "Устройство недоступно для вашего профиля.")
        return True
    section = selected_climate_section(chat_id, room, record)
    if section is None or parts[3] not in climate_fields(section, record["type"]):
        send_message(chat_id, "Откройте прибор через раздел «Климат».")
        return True
    ROOM_VALUE_INPUT.pop(chat_id, None)
    runtime = get_room_runtime()
    model = runtime.get(record)
    signal = model.signals.get(parts[3])
    if signal is None or not signal.writable:
        send_message(chat_id, "Параметр недоступен для изменения.")
        return True
    if parts[0] == "roomval" and len(parts) == 4:
        ROOM_VALUE_INPUT[chat_id] = {"room_id": room["id"], "device_id": record["id"], "signal": parts[3], "section": section}
        send_message(chat_id, f"{signal.title}: введите число от {signal.minimum} до {signal.maximum}.\nБез единиц измерения. Для отмены: /cancel.")
    elif parts[0] == "roomctl" and len(parts) == 5:
        try:
            value = runtime.command(record, parts[3], parts[4])
            save_json_file(ROOMS_FILE, ROOMS)
            log_event(chat_id, f"{room['name']} · {record['name']} · {signal.title}: {value}")
            show_room_device(chat_id, room, record, "✅ Команда выполнена.", section=section)
        except (ValueError, OSError) as error:
            log_event(chat_id, f"{record['name']}: команда не выполнена", False)
            send_message(chat_id, str(error))
    return True


def process_room_value(chat_id, text):
    pending = ROOM_VALUE_INPUT.get(chat_id)
    if pending is None:
        return False
    if text in ("/cancel", "/menu", "/start", "🏠 Мой дом", "🌿 Климат"):
        ROOM_VALUE_INPUT.pop(chat_id, None)
        if text == "🌿 Климат":
            send_message(chat_id, "Выберите параметр климата:", climate_keyboard())
        else:
            send_message(chat_id, rooms_text(chat_id), rooms_keyboard(chat_id))
        return True
    room, record = find_room_device(chat_id, pending["room_id"], pending["device_id"])
    if record is None:
        ROOM_VALUE_INPUT.pop(chat_id, None)
        send_message(chat_id, "Устройство недоступно.")
        return True
    try:
        value = get_room_runtime().command(record, pending["signal"], text)
        save_json_file(ROOMS_FILE, ROOMS)
    except ValueError as error:
        send_message(chat_id, str(error))
        return True
    except OSError:
        send_message(chat_id, "Не удалось сохранить настройки. Попробуйте ещё раз.")
        return True
    ROOM_VALUE_INPUT.pop(chat_id, None)
    log_event(chat_id, f"{room['name']} · {record['name']} · {pending['signal']}: {value}")
    show_room_device(chat_id, room, record, "✅ Значение установлено.", section=pending["section"])
    return True


def rooms_text(chat_id):
    user = USERS[chat_id]
    rooms = apartment_rooms(chat_id)
    return (
        f"🏠 {user['home']} · квартира {user['apartment']}\n\n"
        + ("Выберите комнату:" if rooms else "Комнат пока нет. Добавьте первую комнату.")
    )


def rooms_keyboard(chat_id):
    rows = [[{"text": "🚪 " + room["name"], "callback_data": "rooms:open:" + room["id"]}]
            for room in apartment_rooms(chat_id)]
    rows.extend([
        [{"text": "➕ Добавить комнату", "callback_data": "rooms:add"}],
        [{"text": "⬅️ Главное меню", "callback_data": "menu:main"}],
    ])
    return {"inline_keyboard": rows}


def room_keyboard(room):
    rows = [[{"text": device["name"], "callback_data": f"rooms:device:{room['id']}:{device['id']}"}]
            for device in room["devices"]]
    rows.extend([
        [{"text": "➕ Добавить устройство", "callback_data": "rooms:add_device:" + room["id"]}],
        [{"text": "⬅️ К комнатам", "callback_data": "rooms:list"}],
    ])
    return {"inline_keyboard": rows}


def show_room(chat_id, room):
    send_message(
        chat_id,
        f"🚪 {room['name']}\n\nУстройств: {len(room['devices'])}.\nВыберите или добавьте устройство.",
        room_keyboard(room),
    )


def process_room_setup(chat_id, text):
    setup = ROOM_SETUP.get(chat_id)
    if setup is None:
        return False
    if text in ("/cancel", "/menu", "/start"):
        ROOM_SETUP.pop(chat_id, None)
        send_message(chat_id, "Возврат к комнатам.", rooms_keyboard(chat_id))
        return True
    value = text.strip()
    if not 1 <= len(value) <= 40:
        send_message(chat_id, "Введите название от 1 до 40 символов. Для отмены: /cancel.")
        return True
    if setup["step"] == "room_name":
        rooms = ROOMS.setdefault(apartment_key(chat_id), [])
        if any(room["name"].casefold() == value.casefold() for room in rooms):
            send_message(chat_id, "Такая комната уже есть. Введите другое название.")
            return True
        room = {"id": secrets.token_hex(4), "name": value, "devices": []}
        rooms.append(room)
        save_json_file(ROOMS_FILE, ROOMS)
        ROOM_SETUP.pop(chat_id, None)
        log_event(chat_id, f"Добавлена комната «{value}»")
        show_room(chat_id, room)
    elif setup["step"] == "device_name":
        room = find_room(chat_id, setup["room_id"])
        if room is None:
            ROOM_SETUP.pop(chat_id, None)
            send_message(chat_id, "Комната не найдена.")
            return True
        if any(device["name"].casefold() == value.casefold() for device in room["devices"]):
            send_message(chat_id, "В комнате уже есть такое название. Введите другое.")
            return True
        room["devices"].append({
            "id": secrets.token_hex(4), "name": value,
            "type": setup["type"], "connected": True,
        })
        save_json_file(ROOMS_FILE, ROOMS)
        ROOM_SETUP.pop(chat_id, None)
        log_event(chat_id, f"Добавлено устройство «{value}» в комнату «{room['name']}»")
        get_room_runtime().get(room["devices"][-1])
        send_message(chat_id, "Добавлен отдельный локальный эмулятор. Откройте прибор для управления.")
        show_room(chat_id, room)
    return True


def process_rooms_callback(chat_id, data):
    if not data.startswith("rooms:"):
        return False
    if USERS[chat_id]["role"] != "resident":
        send_message(chat_id, "Комнаты квартиры доступны только жильцу.")
        return True
    ROOM_SETUP.pop(chat_id, None)
    ROOM_VALUE_INPUT.pop(chat_id, None)
    if data == "rooms:list":
        send_message(chat_id, rooms_text(chat_id), rooms_keyboard(chat_id))
    elif data == "rooms:add":
        ROOM_SETUP[chat_id] = {"step": "room_name"}
        send_message(chat_id, "Введите название комнаты: спальня, детская, кухня или своё.\nДля отмены: /cancel.")
    elif data == "rooms:emulators":
        send_message(chat_id, "Управление приборами перенесено в «Климат».", climate_keyboard())
    elif data.startswith("rooms:open:"):
        room = find_room(chat_id, data.split(":")[-1])
        if room:
            show_room(chat_id, room)
        else:
            send_message(chat_id, "Комната не найдена.")
    elif data.startswith("rooms:add_device:"):
        room = find_room(chat_id, data.split(":")[-1])
        if not room:
            send_message(chat_id, "Комната не найдена.")
            return True
        rows = [[{"text": title, "callback_data": f"rooms:type:{room['id']}:{kind}"}]
                for kind, title in DEVICE_TYPES.items()]
        rows.append([{"text": "⬅️ Назад", "callback_data": "rooms:open:" + room["id"]}])
        send_message(chat_id, "Выберите тип устройства:", {"inline_keyboard": rows})
    elif data.startswith("rooms:type:"):
        parts = data.split(":")
        room = find_room(chat_id, parts[2]) if len(parts) == 4 else None
        if room and parts[3] in DEVICE_TYPES:
            kind = parts[3]
            count = sum(device["type"] == kind for device in room["devices"]) + 1
            suffix = f" {count}" if count > 1 else ""
            name = f"{room['name']} · {DEVICE_LABELS[kind]}{suffix}"
            record = {"id": secrets.token_hex(4), "name": name, "type": kind, "connected": True}
            get_room_runtime().get(record)
            room["devices"].append(record)
            save_json_file(ROOMS_FILE, ROOMS)
            log_event(chat_id, f"Добавлено устройство «{name}»")
            send_message(chat_id, f"✅ Добавлено: {name}.\nРегулировка доступна в разделе «Климат».")
            show_room(chat_id, room)
        else:
            send_message(chat_id, "Устройство или комната не найдены.")
    elif data.startswith("rooms:device:"):
        parts = data.split(":")
        room = find_room(chat_id, parts[2]) if len(parts) == 4 else None
        device = next((item for item in room["devices"] if item["id"] == parts[3]), None) if room else None
        if device:
            show_room_device(chat_id, room, device)
        else:
            send_message(chat_id, "Устройство не найдено.")
    return True


def load_invites():
    global INVITES

    if not os.path.exists(INVITES_FILE):
        INVITES = {}
        return

    try:
        with open(INVITES_FILE, "r", encoding="utf-8") as file:
            saved_invites = json.load(file)
        INVITES = {
            code: invite
            for code, invite in saved_invites.items()
            if isinstance(invite, dict)
        }
    except (OSError, ValueError, TypeError):
        INVITES = {}
        print("Не удалось прочитать invites.json")


def save_invites():
    save_json_file(INVITES_FILE, INVITES)


def create_resident_invite(created_by):
    while True:
        code = secrets.token_hex(4).upper()
        if code not in INVITES:
            break

    INVITES[code] = {
        "role": "resident",
        "created_by": created_by,
        "created_at": time.strftime("%d.%m.%Y %H:%M:%S"),
        "used_by": None,
    }
    save_invites()
    return code


def active_invite(code):
    invite = INVITES.get(code.upper())
    if invite and invite.get("used_by") is None:
        return invite
    return None


def invite_text(code):
    return (
        "👥 Приглашение жильца\n\n"
        f"Одноразовый код: {code}\n\n"
        f"Ссылка: https://t.me/{BOT_USERNAME}?start={code}\n\n"
        "Перешлите ссылку приглашённому человеку.\n"
        "Он сам введёт имя, дом и квартиру в личном чате с ботом.\n"
        "После регистрации приглашение станет недействительным."
    )


def load_access_codes():
    global ACCESS_CODES

    try:
        if os.path.exists(ACCESS_FILE):
            with open(ACCESS_FILE, "r", encoding="utf-8") as file:
                ACCESS_CODES = json.load(file)
        else:
            ACCESS_CODES = {
                "resident": secrets.token_hex(4).upper(),
                "manager": secrets.token_hex(4).upper(),
            }
            save_json_file(ACCESS_FILE, ACCESS_CODES)
    except (OSError, ValueError, TypeError):
        raise RuntimeError("Не удалось загрузить коды доступа")


def role_name(role):
    return "Управляющий/УК" if role == "manager" else "Жилец"


def profile_text(chat_id):
    user = USERS[chat_id]
    lines = [
        "⚙️ Профиль",
        "",
        f"Имя: {user['name']}",
        f"Роль: {role_name(user['role'])}",
    ]

    if user["role"] == "resident":
        lines.extend(
            [
                f"Дом: {user['home']}",
                f"Квартира: {user['apartment']}",
            ]
        )
    else:
        lines.append(f"Организация: {user['organization']}")

    return "\n".join(lines)


def registration_role_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "👤 Жилец",
                    "callback_data": "register:role:resident",
                },
                {
                    "text": "🏢 Управляющий/УК",
                    "callback_data": "register:role:manager",
                },
            ]
        ]
    }


def start_registration(chat_id, invite_code=None):
    registration = {"step": "name"}

    if invite_code:
        invite = active_invite(invite_code)
        if invite is None:
            send_message(chat_id, "Приглашение недействительно или уже использовано.")
            return
        registration.update(
            {
                "invite_code": invite_code.upper(),
                "role": "resident",
            }
        )

    REGISTRATION[chat_id] = registration
    send_message(
        chat_id,
        "Регистрация в Velnix Client.\n\nВведите ваше имя.",
    )


def complete_registration(chat_id, registration):
    invite_code = registration.get("invite_code")
    if invite_code and active_invite(invite_code) is None:
        REGISTRATION.pop(chat_id, None)
        send_message(chat_id, "Приглашение уже использовано. Получите новое.")
        return

    USERS[chat_id] = {
        key: value
        for key, value in registration.items()
        if key != "step"
    }
    save_users()

    invite_code = registration.get("invite_code")
    if invite_code and invite_code in INVITES:
        INVITES[invite_code]["used_by"] = chat_id
        INVITES[invite_code]["used_at"] = time.strftime(
            "%d.%m.%Y %H:%M:%S"
        )
        save_invites()

    REGISTRATION.pop(chat_id, None)
    log_event(chat_id, f"Регистрация: {role_name(USERS[chat_id]['role'])}")
    send_message(
        chat_id,
        "Регистрация завершена.\n\n" + profile_text(chat_id),
        main_keyboard(chat_id),
    )


def process_registration(chat_id, text):
    registration = REGISTRATION.get(chat_id)
    if registration is None:
        return False

    step = registration["step"]
    value = text.strip()

    if step == "name":
        if not 2 <= len(value) <= 50:
            send_message(chat_id, "Введите имя длиной от 2 до 50 символов.")
            return True
        registration["name"] = value

        if registration.get("invite_code"):
            registration["step"] = "home"
            send_message(chat_id, "Введите название вашего дома или ЖК.")
            return True

        registration["step"] = "role"
        send_message(chat_id, "Выберите вашу роль:", registration_role_keyboard())
        return True

    if step == "code":
        if registration["role"] == "resident":
            invite = active_invite(value)
            if invite is not None:
                registration["invite_code"] = value.upper()
                registration["step"] = "home"
                send_message(chat_id, "Введите название вашего дома или ЖК.")
                return True

        expected_code = ACCESS_CODES[registration["role"]]
        if value.upper() != expected_code:
            send_message(chat_id, "Неверный код доступа. Попробуйте ещё раз.")
            return True
        if registration["role"] == "resident":
            registration["step"] = "home"
            send_message(chat_id, "Введите название дома или ЖК.")
        else:
            registration["step"] = "organization"
            send_message(chat_id, "Введите название управляющей организации.")
        return True

    if step == "home":
        if not value:
            send_message(chat_id, "Название дома не может быть пустым.")
            return True
        registration["home"] = value
        registration["step"] = "apartment"
        send_message(chat_id, "Введите номер квартиры.")
        return True

    if step == "apartment":
        if not value or len(value) > 20:
            send_message(chat_id, "Введите корректный номер квартиры.")
            return True
        registration["apartment"] = value
        complete_registration(chat_id, registration)
        return True

    if step == "organization":
        if not value:
            send_message(chat_id, "Название организации не может быть пустым.")
            return True
        registration["organization"] = value
        complete_registration(chat_id, registration)
        return True

    return True


def load_events():
    global EVENTS

    if not os.path.exists(EVENTS_FILE):
        EVENTS = {}
        return

    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as file:
            saved_events = json.load(file)

        EVENTS = {
            int(chat_id): events
            for chat_id, events in saved_events.items()
            if isinstance(events, list)
        }
    except (OSError, ValueError, TypeError):
        EVENTS = {}
        print("Не удалось прочитать events.json")


def save_events():
    temporary_file = EVENTS_FILE + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(EVENTS, file, ensure_ascii=False, indent=2)

    os.replace(temporary_file, EVENTS_FILE)


def log_event(chat_id, action, success=True):
    event = {
        "time": time.strftime("%d.%m.%Y %H:%M:%S"),
        "action": action,
        "status": "выполнено" if success else "ошибка",
    }
    EVENTS.setdefault(chat_id, []).append(event)
    EVENTS[chat_id] = EVENTS[chat_id][-100:]
    save_events()


def events_text(chat_id):
    events = EVENTS.get(chat_id, [])

    if not events:
        return "⚠️ События\n\nЖурнал пока пуст."

    lines = ["⚠️ Последние события", ""]
    for event in reversed(events[-15:]):
        icon = "✅" if event["status"] == "выполнено" else "❌"
        lines.append(
            f"{icon} {event['time']} — {event['action']}"
        )

    return "\n".join(lines)


def load_custom_programs():
    global CUSTOM_PROGRAMS

    if not os.path.exists(PROGRAMS_FILE):
        CUSTOM_PROGRAMS = {}
        return

    try:
        with open(PROGRAMS_FILE, "r", encoding="utf-8") as file:
            saved_programs = json.load(file)

        CUSTOM_PROGRAMS = {
            int(chat_id): programs
            for chat_id, programs in saved_programs.items()
            if isinstance(programs, list)
        }
    except (OSError, ValueError, TypeError):
        CUSTOM_PROGRAMS = {}
        print("Не удалось прочитать programs.json")


def save_custom_programs():
    temporary_file = PROGRAMS_FILE + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(
            CUSTOM_PROGRAMS,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temporary_file, PROGRAMS_FILE)


def telegram_request(method, data=None):
    encoded_data = urllib.parse.urlencode(data or {}).encode("utf-8")

    with urllib.request.urlopen(
        f"{API_URL}/{method}",
        data=encoded_data,
        timeout=40,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def ventilation_emulator_state():
    try:
        with urllib.request.urlopen(
            f"{VENTILATION_EMULATOR_URL}/state",
            timeout=3,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def thermostat_emulator_state():
    try:
        with urllib.request.urlopen(
            f"{THERMOSTAT_EMULATOR_URL}/state",
            timeout=3,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def ventilation_emulator_command(signal, value):
    data = urllib.parse.urlencode(
        {
            "signal": signal,
            "value": value,
        }
    ).encode("utf-8")

    try:
        with urllib.request.urlopen(
            f"{VENTILATION_EMULATOR_URL}/command",
            data=data,
            timeout=3,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("error") == 0
    except Exception:
        return False


def thermostat_emulator_command(signal, value):
    data = urllib.parse.urlencode(
        {
            "signal": signal,
            "value": value,
        }
    ).encode("utf-8")

    try:
        with urllib.request.urlopen(
            f"{THERMOSTAT_EMULATOR_URL}/command",
            data=data,
            timeout=3,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("error") == 0
    except Exception:
        return False


def current_ventilation_state():
    emulator_state = ventilation_emulator_state()

    if emulator_state is not None:
        for key in VENTILATION_STATE:
            if key in emulator_state:
                VENTILATION_STATE[key] = emulator_state[key]
        return VENTILATION_STATE, True

    return VENTILATION_STATE, False


def current_thermostat_state():
    emulator_state = thermostat_emulator_state()

    if emulator_state is not None:
        for key in THERMOSTAT_STATE:
            if key in emulator_state:
                THERMOSTAT_STATE[key] = emulator_state[key]
        return THERMOSTAT_STATE, True

    return THERMOSTAT_STATE, False


def main_keyboard(chat_id=None):
    user = USERS.get(chat_id, {})

    if user.get("role") == "manager":
        return {
            "keyboard": [
                ["🏢 Объект УК", "⚠️ События"],
                ["👥 Пригласить жильца"],
                ["⚙️ Профиль"],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    return {
        "keyboard": [
            ["🏠 Мой дом", "🌿 Климат"],
            ["🧩 Программы", "⚠️ События"],
            ["👥 Пригласить жильца"],
            ["⚙️ Профиль"],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def home_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🌡 Управлять термостатом",
                    "callback_data": "climate:temperature",
                }
            ],
            [
                {
                    "text": "🌬 Управлять вентиляцией",
                    "callback_data": "climate:ventilation",
                }
            ],
            [
                {
                    "text": "🔄 Обновить состояния",
                    "callback_data": "home:refresh",
                }
            ],
            [
                {
                    "text": "⬅️ Главное меню",
                    "callback_data": "menu:main",
                }
            ],
        ]
    }


def home_text():
    thermostat, thermostat_connected = current_thermostat_state()
    ventilation, ventilation_connected = current_ventilation_state()

    thermostat_status = "🟢 подключён" if thermostat_connected else "🔴 недоступен"
    ventilation_status = "🟢 подключена" if ventilation_connected else "🔴 недоступна"

    thermostat_modes = {
        0: "OFF",
        1: "HEAT",
        2: "COOL",
        3: "AUTO",
    }
    ventilation_modes = {
        0: "OFF",
        1: "AUTO",
        2: "MANUAL",
    }

    try:
        thermostat_mode = thermostat_modes.get(
            int(float(thermostat["mode"])),
            str(thermostat["mode"]).upper(),
        )
    except (TypeError, ValueError):
        thermostat_mode = str(thermostat["mode"]).upper()

    try:
        ventilation_mode = ventilation_modes.get(
            int(float(ventilation["mode"])),
            str(ventilation["mode"]).upper(),
        )
    except (TypeError, ValueError):
        ventilation_mode = str(ventilation["mode"]).upper()

    return (
        "🏠 Мой дом\n\n"
        f"🌡 Термостат — {thermostat_status}\n"
        f"Температура: {float(thermostat['temperature']):g} °C\n"
        f"Цель: {float(thermostat['target_temp']):g} °C\n"
        f"Режим: {thermostat_mode}\n\n"
        f"🌬 Вентиляция — {ventilation_status}\n"
        f"Скорость: {int(float(ventilation['fan_speed']))}/6\n"
        f"CO₂: {float(ventilation['co2']):g} ppm\n"
        f"PM2.5: {float(ventilation['pm25']):g} мкг/м³\n"
        f"Режим: {ventilation_mode}"
    )


def climate_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🌡 Температура",
                    "callback_data": "climate:temperature",
                },
                {
                    "text": "💧 Влажность",
                    "callback_data": "climate:humidity",
                },
            ],
            [
                {
                    "text": "🌬 Вентиляция",
                    "callback_data": "climate:ventilation",
                },
                {
                    "text": "🫧 Качество воздуха",
                    "callback_data": "climate:air",
                },
            ],
            [
                {
                    "text": "⬅️ Главное меню",
                    "callback_data": "menu:main",
                }
            ],
        ]
    }


def back_to_climate_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "⬅️ Назад к климату",
                    "callback_data": "climate:menu",
                }
            ]
        ]
    }


def temperature_keyboard():
    state, _ = current_thermostat_state()
    target_temp = float(state["target_temp"])

    return {
        "inline_keyboard": [
            [
                {
                    "text": "➖ 1 °C",
                    "callback_data": "temperature:change:-1",
                },
                {
                    "text": f"🎯 {target_temp} °C",
                    "callback_data": "temperature:noop",
                },
                {
                    "text": "➕ 1 °C",
                    "callback_data": "temperature:change:1",
                },
            ],
            [
                {
                    "text": "✏️ Своя температура",
                    "callback_data": "temperature:custom",
                }
            ],
            [
                {
                    "text": "AUTO",
                    "callback_data": "temperature:mode:auto",
                },
                {
                    "text": "HEAT",
                    "callback_data": "temperature:mode:heat",
                },
            ],
            [
                {
                    "text": "COOL",
                    "callback_data": "temperature:mode:cool",
                },
                {
                    "text": "OFF",
                    "callback_data": "temperature:mode:off",
                },
            ],
            [
                {
                    "text": "⬅️ Назад к климату",
                    "callback_data": "climate:menu",
                }
            ],
        ]
    }


def temperature_text():
    state, connected = current_thermostat_state()
    raw_mode = state["mode"]

    try:
        mode_number = int(float(raw_mode))
        mode = {0: "OFF", 1: "HEAT", 2: "COOL", 3: "AUTO"}.get(
            mode_number,
            str(raw_mode).upper(),
        )
    except (TypeError, ValueError):
        mode = str(raw_mode).upper()

    output = {
        0: "нет",
        1: "нагрев",
        2: "охлаждение",
    }.get(int(float(state["output"])), str(state["output"]))
    fault = "нет" if int(float(state["fault"])) == 0 else "нет данных"
    source = (
        "эмулятор http://localhost:8100"
        if connected
        else "локальные данные — эмулятор недоступен"
    )

    return (
        "🌡 Температура\n\n"
        f"Текущая: {float(state['temperature']):g} °C\n"
        f"Целевая: {float(state['target_temp']):g} °C\n"
        f"Влажность: {float(state['humidity']):g} %\n"
        f"Режим: {mode}\n"
        f"Работа: {output}\n"
        f"Авария датчика: {fault}\n\n"
        f"Источник: {source}."
    )


def humidity_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 Обновить",
                    "callback_data": "humidity:refresh",
                }
            ],
            [
                {
                    "text": "⬅️ Назад к климату",
                    "callback_data": "climate:menu",
                }
            ],
        ]
    }


def humidity_text():
    state, connected = current_thermostat_state()
    source = "эмулятор" if connected else "локальные данные"

    return (
        "💧 Влажность\n\n"
        f"Текущее значение: {float(state['humidity']):g} %\n"
        f"Температура: {float(state['temperature']):g} °C\n\n"
        f"Источник: {source}."
    )


def ventilation_keyboard():
    state, _ = current_ventilation_state()
    fan_speed = int(float(state["fan_speed"]))

    return {
        "inline_keyboard": [
            [
                {
                    "text": "➖ Скорость",
                    "callback_data": "ventilation:speed:-1",
                },
                {
                    "text": f"🌬 {fan_speed}/6",
                    "callback_data": "ventilation:noop",
                },
                {
                    "text": "➕ Скорость",
                    "callback_data": "ventilation:speed:1",
                },
            ],
            [
                {
                    "text": "AUTO",
                    "callback_data": "ventilation:mode:auto",
                },
                {
                    "text": "MANUAL",
                    "callback_data": "ventilation:mode:manual",
                },
                {
                    "text": "OFF",
                    "callback_data": "ventilation:mode:off",
                },
            ],
            [
                {
                    "text": "✏️ Целевой CO₂",
                    "callback_data": "ventilation:co2_target",
                }
            ],
            [
                {
                    "text": "🔄 Обновить показания",
                    "callback_data": "ventilation:refresh",
                }
            ],
            [
                {
                    "text": "🌙 Тихий режим — программа",
                    "callback_data": "ventilation:program:quiet",
                }
            ],
            [
                {
                    "text": "🚀 Boost — программа",
                    "callback_data": "ventilation:program:boost",
                }
            ],
            [
                {
                    "text": "⬅️ Назад к климату",
                    "callback_data": "climate:menu",
                }
            ],
        ]
    }


def ventilation_text():
    state, connected = current_ventilation_state()
    raw_mode = state["mode"]

    try:
        mode_number = int(float(raw_mode))
        mode = {0: "OFF", 1: "AUTO", 2: "MANUAL"}.get(
            mode_number,
            str(raw_mode).upper(),
        )
    except (TypeError, ValueError):
        mode = str(raw_mode).upper()

    source = (
        "эмулятор http://localhost:8099"
        if connected
        else "локальные данные — эмулятор недоступен"
    )

    return (
        "🌬 Вентиляция\n\n"
        f"Режим: {mode}\n"
        f"Скорость: {int(float(state['fan_speed']))}/6\n"
        f"Целевой CO₂: {state['co2_target']} ppm\n"
        f"CO₂: {state['co2']:g} ppm\n"
        f"PM2.5: {state['pm25']:g} мкг/м³\n"
        f"Температура притока: {state['supply_temp']:g} °C\n"
        f"Нагрев: {state['heat_pct']} %\n\n"
        f"Источник: {source}."
    )


def air_quality_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 Обновить",
                    "callback_data": "air:refresh",
                }
            ],
            [
                {
                    "text": "⬅️ Назад к климату",
                    "callback_data": "climate:menu",
                }
            ],
        ]
    }


def air_quality_text():
    state, connected = current_ventilation_state()
    source = "эмулятор" if connected else "локальные данные"

    return (
        "🫧 Качество воздуха\n\n"
        f"CO₂: {state['co2']:g} ppm\n"
        f"PM2.5: {state['pm25']:g} мкг/м³\n"
        f"Температура притока: {state['supply_temp']:g} °C\n\n"
        f"Источник: {source}."
    )


def programs_keyboard(chat_id):
    rows = [
            [
                {
                    "text": "🌙 Сон",
                    "callback_data": "programs:run:sleep",
                },
                {
                    "text": "🎯 Фокус",
                    "callback_data": "programs:run:focus",
                },
            ],
            [
                {
                    "text": "🍳 После готовки",
                    "callback_data": "programs:run:cooking",
                }
            ],
            [
                {
                    "text": "🚪 Ушёл из дома",
                    "callback_data": "programs:run:away",
                }
            ],
            [
                {
                    "text": "➕ Создать свою программу",
                    "callback_data": "programs:create",
                }
            ],
        ]

    for index, program in enumerate(CUSTOM_PROGRAMS.get(chat_id, [])):
        rows.append(
            [
                {
                    "text": f"▶️ {program['name']}",
                    "callback_data": f"programs:custom:{index}",
                }
            ]
        )

    rows.extend(
        [
            [
                {
                    "text": "📊 Состояние программы",
                    "callback_data": "programs:status",
                }
            ],
            [
                {
                    "text": "⏹ Остановить и восстановить",
                    "callback_data": "programs:stop",
                }
            ],
            [
                {
                    "text": "⬅️ Главное меню",
                    "callback_data": "menu:main",
                }
            ],
        ]
    )

    return {"inline_keyboard": rows}


def program_title(program_name):
    if isinstance(program_name, str) and program_name.startswith("custom:"):
        return program_name.split(":", 1)[1]

    return {
        "sleep": "Сон",
        "focus": "Фокус",
        "cooking": "После готовки",
        "away": "Ушёл из дома",
    }.get(program_name, "не запущена")


def programs_text(prefix=""):
    thermostat, thermostat_connected = current_thermostat_state()
    ventilation, ventilation_connected = current_ventilation_state()
    active_title = program_title(ACTIVE_PROGRAM)
    status = (
        "термостат и вентиляция подключены"
        if thermostat_connected and ventilation_connected
        else "одно из устройств недоступно"
    )

    body = (
        "🧩 Программы микроклимата\n\n"
        f"Активная программа: {active_title}\n"
        f"Целевая температура: {float(thermostat['target_temp']):g} °C\n"
        f"Скорость вентиляции: "
        f"{int(float(ventilation['fan_speed']))}/6\n"
        f"Целевой CO₂: {int(float(ventilation['co2_target']))} ppm\n"
        f"Связь: {status}."
    )

    return f"{prefix}\n\n{body}" if prefix else body


def capture_program_snapshot():
    thermostat, _ = current_thermostat_state()
    ventilation, _ = current_ventilation_state()

    return {
        "thermostat": {
            "target_temp": thermostat["target_temp"],
            "mode": thermostat["mode"],
        },
        "ventilation": {
            "fan_speed": ventilation["fan_speed"],
            "mode": ventilation["mode"],
            "co2_target": ventilation["co2_target"],
        },
    }


def apply_program(program_name):
    global ACTIVE_PROGRAM, PROGRAM_SNAPSHOT
    results = []

    if ACTIVE_PROGRAM is None:
        PROGRAM_SNAPSHOT = capture_program_snapshot()

    if program_name == "sleep":
        results.append(thermostat_emulator_command("mode", 3))
        results.append(thermostat_emulator_command("target_temp", 20))
        results.append(ventilation_emulator_command("mode", 2))
        results.append(ventilation_emulator_command("fan_speed", 1))

    elif program_name == "focus":
        results.append(thermostat_emulator_command("mode", 3))
        results.append(thermostat_emulator_command("target_temp", 22))
        results.append(ventilation_emulator_command("co2_target", 700))
        results.append(ventilation_emulator_command("mode", 1))

    elif program_name == "cooking":
        results.append(ventilation_emulator_command("mode", 2))
        results.append(ventilation_emulator_command("fan_speed", 6))

    elif program_name == "away":
        results.append(thermostat_emulator_command("mode", 3))
        results.append(thermostat_emulator_command("target_temp", 18))
        results.append(ventilation_emulator_command("mode", 0))
        results.append(ventilation_emulator_command("fan_speed", 0))

    ACTIVE_PROGRAM = program_name
    return bool(results) and all(results)


def apply_custom_program(program):
    global ACTIVE_PROGRAM, PROGRAM_SNAPSHOT

    if ACTIVE_PROGRAM is None:
        PROGRAM_SNAPSHOT = capture_program_snapshot()

    results = [
        thermostat_emulator_command("mode", 3),
        thermostat_emulator_command("target_temp", program["target_temp"]),
        ventilation_emulator_command("co2_target", program["co2_target"]),
        ventilation_emulator_command("mode", 2),
        ventilation_emulator_command("fan_speed", program["fan_speed"]),
    ]
    ACTIVE_PROGRAM = f"custom:{program['name']}"
    return all(results)


def custom_program_summary(program):
    return (
        f"Название: {program['name']}\n"
        f"Температура: {program['target_temp']:g} °C\n"
        f"Скорость вентиляции: {program['fan_speed']}/6\n"
        f"Целевой CO₂: {program['co2_target']} ppm"
    )


def process_program_creation(chat_id, text):
    creation = PROGRAM_CREATION.get(chat_id)

    if creation is None:
        return False

    if text == "/cancel":
        PROGRAM_CREATION.pop(chat_id, None)
        send_message(
            chat_id,
            "Создание программы отменено.",
            programs_keyboard(chat_id),
        )
        return True

    step = creation["step"]

    if step == "name":
        name = text.strip()

        if not name or len(name) > 30:
            send_message(
                chat_id,
                "Название должно содержать от 1 до 30 символов.",
            )
            return True

        creation["name"] = name
        creation["step"] = "temperature"
        send_message(
            chat_id,
            "Введите целевую температуру числом от 5 до 35.\n"
            "Например: 22.5",
        )
        return True

    if step == "temperature":
        try:
            target_temp = float(text.strip().replace(",", "."))
        except ValueError:
            send_message(chat_id, "Введите температуру числом.")
            return True

        if not 5 <= target_temp <= 35:
            send_message(chat_id, "Допустимый диапазон: 5–35 °C.")
            return True

        creation["target_temp"] = round(target_temp, 1)
        creation["step"] = "fan_speed"
        send_message(
            chat_id,
            "Введите скорость вентиляции от 0 до 6.",
        )
        return True

    if step == "fan_speed":
        try:
            fan_speed = int(text.strip())
        except ValueError:
            send_message(chat_id, "Введите скорость целым числом.")
            return True

        if not 0 <= fan_speed <= 6:
            send_message(chat_id, "Допустимая скорость: 0–6.")
            return True

        creation["fan_speed"] = fan_speed
        creation["step"] = "co2_target"
        send_message(
            chat_id,
            "Введите целевой CO₂ от 400 до 1400 ppm.\n"
            "Например: 800",
        )
        return True

    if step == "co2_target":
        try:
            co2_target = int(float(text.strip().replace(",", ".")))
        except ValueError:
            send_message(chat_id, "Введите CO₂ числом.")
            return True

        if not 400 <= co2_target <= 1400:
            send_message(chat_id, "Допустимый диапазон: 400–1400 ppm.")
            return True

        program = {
            "name": creation["name"],
            "target_temp": creation["target_temp"],
            "fan_speed": creation["fan_speed"],
            "co2_target": co2_target,
        }
        CUSTOM_PROGRAMS.setdefault(chat_id, []).append(program)
        save_custom_programs()
        log_event(chat_id, f"Создана программа «{program['name']}»")
        PROGRAM_CREATION.pop(chat_id, None)
        send_message(
            chat_id,
            "Программа сохранена.\n\n" + custom_program_summary(program),
            programs_keyboard(chat_id),
        )
        return True

    return False


def stop_program():
    global ACTIVE_PROGRAM, PROGRAM_SNAPSHOT

    if PROGRAM_SNAPSHOT is None:
        ACTIVE_PROGRAM = None
        return False

    thermostat = PROGRAM_SNAPSHOT["thermostat"]
    ventilation = PROGRAM_SNAPSHOT["ventilation"]

    results = [
        thermostat_emulator_command("target_temp", thermostat["target_temp"]),
        thermostat_emulator_command("mode", thermostat["mode"]),
        ventilation_emulator_command("co2_target", ventilation["co2_target"]),
        ventilation_emulator_command("mode", ventilation["mode"]),
        ventilation_emulator_command("fan_speed", ventilation["fan_speed"]),
    ]

    ACTIVE_PROGRAM = None
    PROGRAM_SNAPSHOT = None
    return all(results)


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False,
        )

    telegram_request("sendMessage", data)


def answer_callback_query(callback_query_id):
    telegram_request(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id},
    )


def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False,
        )

    telegram_request("editMessageText", data)


def process_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if chat_id not in USERS:
        if text.startswith("/start "):
            start_registration(chat_id, text.split(maxsplit=1)[1].strip())
            return

        if text in ("/start", "/register") and chat_id not in REGISTRATION:
            start_registration(chat_id)
            return

        if process_registration(chat_id, text):
            return

        send_message(chat_id, "Для регистрации отправьте /start.")
        return

    if process_room_value(chat_id, text):
        return

    if process_room_setup(chat_id, text):
        return

    if text == "👥 Пригласить жильца":
        code = create_resident_invite(chat_id)
        log_event(chat_id, "Создано приглашение для регистрации жильца")
        send_message(chat_id, invite_text(code), main_keyboard(chat_id))
        return

    if process_program_creation(chat_id, text):
        return

    if chat_id in WAITING_FOR_CUSTOM_TEMPERATURE:
        normalized_value = text.strip().replace(",", ".")

        try:
            target_temp = float(normalized_value)
        except ValueError:
            send_message(
                chat_id,
                "Введите температуру числом, например 23 или 22.5.",
            )
            return

        if not 5 <= target_temp <= 35:
            send_message(
                chat_id,
                "Допустимый диапазон: от 5 до 35 °C. "
                "Введите другое значение.",
            )
            return

        THERMOSTAT_STATE["target_temp"] = round(target_temp, 1)
        success = thermostat_emulator_command(
            "target_temp",
            THERMOSTAT_STATE["target_temp"],
        )
        log_event(
            chat_id,
            f"Температура установлена: {target_temp:g} °C",
            success,
        )
        WAITING_FOR_CUSTOM_TEMPERATURE.discard(chat_id)
        send_message(
            chat_id,
            "Температура установлена.\n\n" + temperature_text(),
            temperature_keyboard(),
        )
        return

    if chat_id in WAITING_FOR_CUSTOM_CO2:
        normalized_value = text.strip().replace(",", ".")

        try:
            co2_target = int(float(normalized_value))
        except ValueError:
            send_message(
                chat_id,
                "Введите целевой CO₂ числом, например 800.",
            )
            return

        if not 400 <= co2_target <= 1400:
            send_message(
                chat_id,
                "Допустимый диапазон: от 400 до 1400 ppm. "
                "Введите другое значение.",
            )
            return

        VENTILATION_STATE["co2_target"] = co2_target
        success = ventilation_emulator_command("co2_target", co2_target)
        log_event(
            chat_id,
            f"Целевой CO₂ установлен: {co2_target} ppm",
            success,
        )
        WAITING_FOR_CUSTOM_CO2.discard(chat_id)
        send_message(
            chat_id,
            "Целевой CO₂ установлен.\n\n" + ventilation_text(),
            ventilation_keyboard(),
        )
        return

    if text in ("/start", "/menu"):
        send_message(
            chat_id,
            "Добро пожаловать в Velnix Client!\n"
            "Выберите нужный раздел:",
            main_keyboard(chat_id),
        )

    elif text == "/help":
        send_message(
            chat_id,
            "Используйте кнопки главного меню.\n"
            "Команда /menu открывает меню.",
            main_keyboard(chat_id),
        )

    elif text == "🏠 Мой дом":
        if USERS[chat_id]["role"] == "resident":
            send_message(chat_id, rooms_text(chat_id), rooms_keyboard(chat_id))
        else:
            send_message(chat_id, "Раздел доступен только жильцу.")

    elif text == "🏢 Объект УК":
        if USERS[chat_id]["role"] == "manager":
            send_message(chat_id, home_text(), home_keyboard())
        else:
            send_message(chat_id, "Недостаточно прав для раздела УК.")

    elif text == "🌿 Климат":
        if USERS[chat_id]["role"] == "resident":
            send_message(
                chat_id,
                "Раздел «Климат».\nВыберите параметр:",
                climate_keyboard(),
            )
        else:
            send_message(chat_id, "Раздел доступен только жильцу.")

    elif text == "🧩 Программы":
        if USERS[chat_id]["role"] == "resident":
            send_message(
                chat_id,
                programs_text(),
                programs_keyboard(chat_id),
            )
        else:
            send_message(chat_id, "Раздел доступен только жильцу.")

    elif text == "⚠️ События":
        send_message(
            chat_id,
            events_text(chat_id),
            main_keyboard(chat_id),
        )

    elif text == "⚙️ Профиль":
        send_message(
            chat_id,
            profile_text(chat_id),
            main_keyboard(chat_id),
        )

    else:
        send_message(
            chat_id,
            "Неизвестная команда. Выберите раздел:",
            main_keyboard(chat_id),
        )


def process_callback(callback_query):
    callback_query_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")

    answer_callback_query(callback_query_id)

    if chat_id not in USERS:
        registration = REGISTRATION.get(chat_id)
        if data.startswith("register:role:") and registration:
            role = data.rsplit(":", 1)[1]
            if registration.get("step") == "role" and role in (
                "resident",
                "manager",
            ):
                registration["role"] = role
                registration["step"] = "code"
                send_message(
                    chat_id,
                    f"Введите код доступа для роли «{role_name(role)}».",
                )
                return

        send_message(chat_id, "Для регистрации отправьте /start.")
        return

    if process_climate_navigation(chat_id, data):
        return

    if process_room_control(chat_id, data):
        return

    if process_rooms_callback(chat_id, data):
        return

    if data == "home:refresh":
        edit_message(
            chat_id,
            message_id,
            home_text(),
            home_keyboard(),
        )

    elif data == "climate:menu":
        send_message(
            chat_id,
            "Раздел «Климат».\nВыберите параметр:",
            climate_keyboard(),
        )

    elif data == "climate:temperature":
        send_message(
            chat_id,
            temperature_text(),
            temperature_keyboard(),
        )

    elif data.startswith("temperature:change:"):
        change = int(data.rsplit(":", 1)[1])
        state, _ = current_thermostat_state()
        current_value = float(state["target_temp"])
        THERMOSTAT_STATE["target_temp"] = max(
            5,
            min(35, current_value + change),
        )
        success = thermostat_emulator_command(
            "target_temp",
            THERMOSTAT_STATE["target_temp"],
        )
        log_event(
            chat_id,
            f"Температура изменена: {THERMOSTAT_STATE['target_temp']:g} °C",
            success,
        )
        edit_message(
            chat_id,
            message_id,
            temperature_text(),
            temperature_keyboard(),
        )

    elif data == "temperature:custom":
        WAITING_FOR_CUSTOM_TEMPERATURE.add(chat_id)
        send_message(
            chat_id,
            "Введите желаемую температуру от 5 до 35 °C.\n"
            "Можно использовать дробное значение, например 22.5.",
        )

    elif data.startswith("temperature:mode:"):
        mode = data.rsplit(":", 1)[1]
        mode_numbers = {"off": 0, "heat": 1, "cool": 2, "auto": 3}

        if mode in ("off", "heat", "cool", "auto"):
            THERMOSTAT_STATE["mode"] = mode
            success = thermostat_emulator_command(
                "mode",
                mode_numbers[mode],
            )
            log_event(
                chat_id,
                f"Режим термостата: {mode}",
                success,
            )

        edit_message(
            chat_id,
            message_id,
            temperature_text(),
            temperature_keyboard(),
        )

    elif data == "temperature:noop":
        pass

    elif data == "climate:humidity":
        send_message(
            chat_id,
            humidity_text(),
            humidity_keyboard(),
        )

    elif data == "humidity:refresh":
        edit_message(
            chat_id,
            message_id,
            humidity_text(),
            humidity_keyboard(),
        )

    elif data == "climate:ventilation":
        send_message(
            chat_id,
            ventilation_text(),
            ventilation_keyboard(),
        )

    elif data.startswith("ventilation:speed:"):
        change = int(data.rsplit(":", 1)[1])
        state, _ = current_ventilation_state()
        current_speed = int(float(state["fan_speed"]))
        VENTILATION_STATE["fan_speed"] = max(
            0,
            min(6, current_speed + change),
        )
        VENTILATION_STATE["mode"] = "manual"
        mode_success = ventilation_emulator_command("mode", 2)
        speed_success = ventilation_emulator_command(
            "fan_speed",
            VENTILATION_STATE["fan_speed"],
        )
        log_event(
            chat_id,
            f"Скорость вентиляции: {VENTILATION_STATE['fan_speed']}/6",
            mode_success and speed_success,
        )
        edit_message(
            chat_id,
            message_id,
            ventilation_text(),
            ventilation_keyboard(),
        )

    elif data.startswith("ventilation:mode:"):
        mode = data.rsplit(":", 1)[1]
        mode_numbers = {"off": 0, "auto": 1, "manual": 2}

        if mode in ("off", "auto", "manual"):
            VENTILATION_STATE["mode"] = mode
            success = ventilation_emulator_command(
                "mode",
                mode_numbers[mode],
            )

        if mode == "off":
            VENTILATION_STATE["fan_speed"] = 0
            success = (
                ventilation_emulator_command("fan_speed", 0)
                and success
            )

        if mode in ("off", "auto", "manual"):
            log_event(
                chat_id,
                f"Режим вентиляции: {mode}",
                success,
            )

        edit_message(
            chat_id,
            message_id,
            ventilation_text(),
            ventilation_keyboard(),
        )

    elif data == "ventilation:co2_target":
        WAITING_FOR_CUSTOM_CO2.add(chat_id)
        send_message(
            chat_id,
            "Введите целевой уровень CO₂ от 400 до 1400 ppm.\n"
            "Например: 800.",
        )

    elif data == "ventilation:program:quiet":
        VENTILATION_STATE["mode"] = "manual"
        VENTILATION_STATE["fan_speed"] = 1
        success = ventilation_emulator_command("mode", 2)
        success = ventilation_emulator_command("fan_speed", 1) and success
        log_event(chat_id, "Запущен тихий режим", success)
        edit_message(
            chat_id,
            message_id,
            "Программа «Тихий режим» включена.\n\n"
            + ventilation_text(),
            ventilation_keyboard(),
        )

    elif data == "ventilation:program:boost":
        VENTILATION_STATE["mode"] = "manual"
        VENTILATION_STATE["fan_speed"] = 6
        success = ventilation_emulator_command("mode", 2)
        success = ventilation_emulator_command("fan_speed", 6) and success
        log_event(chat_id, "Запущен режим Boost", success)
        edit_message(
            chat_id,
            message_id,
            "Программа Boost включена.\n\n" + ventilation_text(),
            ventilation_keyboard(),
        )

    elif data == "ventilation:noop":
        pass

    elif data == "ventilation:refresh":
        edit_message(
            chat_id,
            message_id,
            ventilation_text(),
            ventilation_keyboard(),
        )

    elif data == "climate:air":
        send_message(
            chat_id,
            air_quality_text(),
            air_quality_keyboard(),
        )

    elif data == "air:refresh":
        edit_message(
            chat_id,
            message_id,
            air_quality_text(),
            air_quality_keyboard(),
        )

    elif data == "programs:menu":
        send_message(
            chat_id,
            programs_text(),
            programs_keyboard(chat_id),
        )

    elif data == "programs:create":
        PROGRAM_CREATION[chat_id] = {"step": "name"}
        send_message(
            chat_id,
            "Введите название своей программы.\n"
            "Для отмены отправьте /cancel.",
        )

    elif data.startswith("programs:custom:"):
        try:
            index = int(data.rsplit(":", 1)[1])
            program = CUSTOM_PROGRAMS.get(chat_id, [])[index]
        except (ValueError, IndexError):
            send_message(chat_id, "Программа не найдена.")
            log_event(chat_id, "Запуск пользовательской программы", False)
        else:
            success = apply_custom_program(program)
            log_event(
                chat_id,
                f"Запущена программа «{program['name']}»",
                success,
            )
            edit_message(
                chat_id,
                message_id,
                programs_text(
                    f"Программа «{program['name']}» запущена."
                ),
                programs_keyboard(chat_id),
            )

    elif data.startswith("programs:run:"):
        program_name = data.rsplit(":", 1)[1]

        if program_name in ("sleep", "focus", "cooking", "away"):
            success = apply_program(program_name)
            log_event(
                chat_id,
                f"Запущена программа «{program_title(program_name)}»",
                success,
            )
            edit_message(
                chat_id,
                message_id,
                programs_text(
                    f"Программа «{program_title(program_name)}» запущена."
                ),
                programs_keyboard(chat_id),
            )

    elif data == "programs:status":
        edit_message(
            chat_id,
            message_id,
            programs_text(),
            programs_keyboard(chat_id),
        )

    elif data == "programs:stop":
        restored = stop_program()
        log_event(
            chat_id,
            "Остановка программы и восстановление настроек",
            restored,
        )
        prefix = (
            "Программа остановлена. Предыдущие настройки восстановлены."
            if restored
            else "Активной программы нет."
        )
        edit_message(
            chat_id,
            message_id,
            programs_text(prefix),
            programs_keyboard(chat_id),
        )

    elif data == "climate:filter":
        send_message(
            chat_id,
            "🧰 Фильтр\n\n"
            "Контроль фильтра — дополнительная идея. "
            "В базовом эмуляторе такого сигнала нет.",
            back_to_climate_keyboard(),
        )

    elif data == "menu:main":
        send_message(
            chat_id,
            "Главное меню:",
            main_keyboard(chat_id),
        )


def main():
    offset = 0
    load_access_codes()
    load_users()
    load_rooms()
    for rooms in ROOMS.values():
        for room in rooms:
            for device in room["devices"]:
                get_room_runtime().get(device)
    load_invites()
    load_custom_programs()
    load_events()
    bridge = BridgePublisher(ROOMS, lambda record: get_room_runtime().state(record))
    bridge.start()
    print("Velnix Client запущен")
    print("Код жильца:", ACCESS_CODES["resident"])
    print("Код УК:", ACCESS_CODES["manager"])

    while True:
        try:
            result = telegram_request(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                },
            )

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                if "message" in update:
                    process_message(update["message"])

                elif "callback_query" in update:
                    process_callback(update["callback_query"])

        except Exception as error:
            print("Ошибка:", error)
            time.sleep(2)


if __name__ == "__main__":
    main()
