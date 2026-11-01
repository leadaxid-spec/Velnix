"""Read-only snapshots for the second bot; no organizer source changes."""
import json
import os
import threading
import time
from pathlib import Path


class BridgePublisher:
    def __init__(self, rooms, state_reader, path=None):
        self.rooms = rooms
        self.state_reader = state_reader
        self.path = Path(path or os.environ.get(
            "VELNIX_BRIDGE_FILE", "~/velnix_shared/state.json"
        )).expanduser()
        self.stop = threading.Event()

    def publish(self):
        # A deep snapshot avoids iterating room collections during later reads.
        rooms = json.loads(json.dumps(self.rooms, ensure_ascii=False))
        devices = []
        for apartment_key, apartment_rooms in rooms.items():
            home, apartment = json.loads(apartment_key)
            for room in apartment_rooms:
                for record in room["devices"]:
                    devices.append({
                        "id": record["id"], "kind": record["type"],
                        "home": home, "apartment": apartment,
                        "room": room["name"], "name": record["name"],
                        "state": self.state_reader(record),
                    })
        payload = {"schema": 1, "source": "local_simulation",
                   "generated_at": time.time(), "devices": devices}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
        os.replace(str(temporary), str(self.path))
        return payload

    def start(self):
        def loop():
            last_failure = False
            while not self.stop.is_set():
                try:
                    self.publish()
                    last_failure = False
                except Exception:
                    if not last_failure:
                        print("Не удалось обновить снимок для Warning-бота.")
                    last_failure = True
                self.stop.wait(5)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread
