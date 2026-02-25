import json
import os
from config import STATE_FILE

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_alerts": {}}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def recently_alerted(token, cooldown_hours=6):
    from datetime import datetime, timedelta

    state = load_state()
    last_alerts = state.get("last_alerts", {})

    if token not in last_alerts:
        return False

    last_time = datetime.fromisoformat(last_alerts[token])
    if datetime.utcnow() - last_time < timedelta(hours=cooldown_hours):
        return True

    return False

def mark_alerted(token):
    from datetime import datetime

    state = load_state()
    state.setdefault("last_alerts", {})
    state["last_alerts"][token] = datetime.utcnow().isoformat()
    save_state(state)
