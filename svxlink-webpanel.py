#!/usr/bin/env python3
"""SvxLink status and log web panel. Python standard library only."""
import base64
from collections import deque
import html
import hmac
import json
import logging
import tempfile
import os
import re
import subprocess
import shlex
import ssl
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("SVXLINK_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("SVXLINK_WEB_PORT", "8090"))
VERSION = "1.6.0-dev2"
LOGFILE = os.environ.get("SVXLINK_LOGFILE", "/var/log/svxlink")
CONFIG_FILE = os.environ.get("SVXLINK_CONFIG", "/etc/svxlink/svxlink.conf")
SERVICE = os.environ.get("SVXLINK_SERVICE", "svxlink")
USERNAME = os.environ.get("SVXLINK_WEB_USER", "admin")
PASSWORD = os.environ.get("SVXLINK_WEB_PASSWORD", "")
MAX_LOG_BYTES = 256 * 1024
MAX_LINES = 250
BUTTONS_FILE = os.environ.get("SVXLINK_BUTTONS_FILE", "/var/lib/svxlink/webpanel-buttons.json")
REFLECTOR_PROXY_URL = os.environ.get("SVXLINK_REFLECTOR_PROXY_URL", "https://reflector-sm.svxlink.org/reflector_proxy")
REFLECTOR_STREAM_URL = os.environ.get("SVXLINK_REFLECTOR_STREAM_URL", "https://reflector-sm.svxlink.org/api/stream")
CRONTAB_COMMAND = "crontab"
CRONTAB_START = "# BEGIN SVXLINK-WEBPANEL MANAGED SCHEDULES"
CRONTAB_END = "# END SVXLINK-WEBPANEL MANAGED SCHEDULES"
STATE_PTY_LOCK = threading.Lock()
STATE_PTY_CACHE = {
    "key": None,
    "fd": None,
    "buffer": b"",
    "receivers": {},
    "transmitters": {},
    "seen": False,
}
RADIO_EVENT_CONDITION = threading.Condition()
RADIO_EVENT_SEQUENCE = 0
RADIO_EVENTS = deque(maxlen=256)
RADIO_STATE = {"mode": "unknown", "signal_level": None, "error": "Väntar på status från SvxLink."}
RADIO_READER_STARTED = False
LOG_EVENT_CONDITION = threading.Condition()
LOG_EVENT_SEQUENCE = 0
LOG_EVENTS = deque(maxlen=1024)
LOG_TAIL_LOCK = threading.Lock()
LOG_TAIL_STATE = {
    "initialized": False,
    "file": None,
    "identity": None,
    "offset": 0,
    "buffer": b"",
    "lines": deque(maxlen=MAX_LINES),
    "error": None,
}
CRONTAB_LOCK = threading.Lock()
CONFIG_HELPER = "/usr/local/libexec/svxlink-webpanel-config"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
CONFIG_CACHE_LOCK = threading.Lock()
CONFIG_CACHE_KEY = None
CONFIG_CACHE_SECTIONS = None
SERVICE_STATUS_LOCK = threading.Lock()
SERVICE_STATUS_CACHE = {"service": None, "checked_at": 0.0, "result": ("okänt", False)}
REFLECTOR_EVENT_CONDITION = threading.Condition()
REFLECTOR_EVENT_SEQUENCE = 0
REFLECTOR_EVENTS = deque(maxlen=1024)
REFLECTOR_STREAM_STARTED = False
REFLECTOR_STREAM_STATE = {
    "ok": False,
    "connected": False,
    "error": "Ansluter till reflektorströmmen.",
    "node_count": 0,
    "talkgroups": [],
    "updated_at": None,
}
REFLECTOR_DOCUMENT = {}
REFLECTOR_GENERATION = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WEB_ROOT = os.environ.get("SVXLINK_WEB_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
WEB_ASSETS = {
    "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
    "/assets/common.js": ("common.js", "application/javascript; charset=utf-8"),
    "/assets/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/assets/schedules.js": ("schedules.js", "application/javascript; charset=utf-8"),
    "/assets/config.js": ("config.js", "application/javascript; charset=utf-8"),
    "/assets/node-info.js": ("node-info.js", "application/javascript; charset=utf-8"),
}


def read_web_file(filename):
    with open(os.path.join(WEB_ROOT, filename), "rb") as web_file:
        return web_file.read()


def service_status():
    now = time.monotonic()
    with SERVICE_STATUS_LOCK:
        if (SERVICE_STATUS_CACHE["service"] == SERVICE
                and now - SERVICE_STATUS_CACHE["checked_at"] < 1):
            return SERVICE_STATUS_CACHE["result"]
        try:
            result = subprocess.run(
                ["systemctl", "is-active", SERVICE],
                capture_output=True, text=True, timeout=2, check=False
            )
            value = (result.stdout or result.stderr).strip() or "okänt"
            status = (value, value == "active")
        except (OSError, subprocess.SubprocessError) as exc:
            logging.warning("Kunde inte läsa status för tjänsten %s: %s", SERVICE, exc)
            status = ("okänt", False)
        SERVICE_STATUS_CACHE.update({
            "service": SERVICE,
            "checked_at": time.monotonic(),
            "result": status,
        })
        return status

def invalidate_service_status():
    with SERVICE_STATUS_LOCK:
        SERVICE_STATUS_CACHE["service"] = None

def control_service(action):
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": "Otillåten tjänståtgärd."}, 400
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", action, SERVICE],
            capture_output=True, text=True, timeout=15, check=False
        )
    except subprocess.TimeoutExpired:
        invalidate_service_status()
        return {"ok": False, "error": "Tidsgränsen överskreds när SvxLink skulle styras."}, 504
    except OSError as exc:
        invalidate_service_status()
        return {"ok": False, "error": f"Kunde inte köra tjänstkommandot: {exc}"}, 500
    invalidate_service_status()
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if "a password is required" in details.lower() or "not allowed" in details.lower():
            return {"ok": False, "error": "Panelen saknar behörighet att starta, stoppa eller starta om SvxLink. Kontrollera sudoers-konfigurationen."}, 500
        action_text = {"start": "starta", "stop": "stoppa", "restart": "starta om"}[action]
        return {"ok": False, "error": f"Kunde inte {action_text} SvxLink: {details or 'systemctl misslyckades.'}"}, 500
    action_text = {"start": "startas", "stop": "stoppas", "restart": "startas om"}[action]
    return {"ok": True, "message": f"SvxLink {action_text}."}, 200

def config_helper_request(payload):
    try:
        result = subprocess.run(
            ["sudo", "-n", CONFIG_HELPER],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Tidsgränsen överskreds vid åtkomst till konfigurationsfilen.") from exc
    except OSError as exc:
        raise RuntimeError(f"Kunde inte köra konfigurationshjälpen: {exc}") from exc
    try:
        response = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Konfigurationshjälpen returnerade ett ogiltigt svar: {detail or 'okänt fel'}") from exc
    if result.returncode != 0 or not isinstance(response, dict) or not response.get("ok"):
        detail = response.get("error", "Konfigurationshjälpen misslyckades.") if isinstance(response, dict) else "Konfigurationshjälpen misslyckades."
        raise RuntimeError(detail)
    return response

def reject_json_constant(value):
    raise ValueError(f"Ogiltigt JSON-konstantvärde: {value}.")

def read_log():
    try:
        with open(LOGFILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - MAX_LOG_BYTES))
            raw = f.read(MAX_LOG_BYTES)
        text = raw.decode("utf-8", errors="replace")
        # SvxLink logs may contain NULs or other control bytes; remove them before display.
        cleaned = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in text)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return lines[-MAX_LINES:]
    except Exception as e:
        return [f"Kunde inte läsa loggfilen {LOGFILE}: {e}"]

def clean_log_line(raw_line):
    text = raw_line.decode("utf-8", errors="replace")
    cleaned = "".join(ch if ch in "\t" or ord(ch) >= 32 else " " for ch in text).strip()
    return cleaned

def publish_log_lines(lines):
    global LOG_EVENT_SEQUENCE
    if not lines:
        return
    with LOG_EVENT_CONDITION:
        for line in lines:
            LOG_EVENT_SEQUENCE += 1
            LOG_EVENTS.append((LOG_EVENT_SEQUENCE, "log-line", line))
        LOG_EVENT_CONDITION.notify_all()

def publish_log_snapshot(lines, error=None, event_type="log-snapshot"):
    global LOG_EVENT_SEQUENCE
    with LOG_EVENT_CONDITION:
        LOG_EVENT_SEQUENCE += 1
        LOG_EVENTS.append((
            LOG_EVENT_SEQUENCE,
            event_type,
            {"lines": list(lines), "error": error},
        ))
        LOG_EVENT_CONDITION.notify_all()

def load_log_tail(file_obj, file_size):
    offset = max(0, file_size - MAX_LOG_BYTES)
    file_obj.seek(offset)
    raw = file_obj.read(MAX_LOG_BYTES)
    if offset:
        first_newline = raw.find(b"\n")
        raw = raw[first_newline + 1:] if first_newline >= 0 else b""
    lines = []
    for raw_line in raw.splitlines():
        line = clean_log_line(raw_line)
        if line:
            lines.append(line)
    return file_obj.tell(), deque(lines[-MAX_LINES:], maxlen=MAX_LINES)

def initialize_log_tail():
    with LOG_TAIL_LOCK:
        if LOG_TAIL_STATE["initialized"]:
            return
        try:
            file_obj = open(LOGFILE, "rb")
            stat = os.fstat(file_obj.fileno())
            offset, lines = load_log_tail(file_obj, stat.st_size)
            LOG_TAIL_STATE.update({
                "file": file_obj,
                "identity": (stat.st_dev, stat.st_ino),
                "offset": stat.st_size,
                "buffer": b"",
                "lines": lines,
                "error": None,
                "initialized": True,
            })
        except OSError as exc:
            LOG_TAIL_STATE.update({
                "file": None,
                "identity": None,
                "offset": 0,
                "buffer": b"",
                "error": f"Kunde inte läsa loggfilen {LOGFILE}: {exc}",
                "initialized": True,
            })

def read_log_tail_updates():
    initialize_log_tail()
    new_lines = []
    reset_snapshot = None
    with LOG_TAIL_LOCK:
        try:
            path_stat = os.stat(LOGFILE)
            identity = (path_stat.st_dev, path_stat.st_ino)
            if LOG_TAIL_STATE["file"] is None or identity != LOG_TAIL_STATE["identity"]:
                if LOG_TAIL_STATE["file"] is not None:
                    LOG_TAIL_STATE["file"].close()
                LOG_TAIL_STATE["file"] = open(LOGFILE, "rb")
                LOG_TAIL_STATE["identity"] = identity
                LOG_TAIL_STATE["offset"] = 0
                LOG_TAIL_STATE["buffer"] = b""
                current_size = os.fstat(LOG_TAIL_STATE["file"].fileno()).st_size
                LOG_TAIL_STATE["offset"], LOG_TAIL_STATE["lines"] = load_log_tail(LOG_TAIL_STATE["file"], current_size)
                reset_snapshot = list(LOG_TAIL_STATE["lines"])

            file_obj = LOG_TAIL_STATE["file"]
            current_size = os.fstat(file_obj.fileno()).st_size
            if current_size < LOG_TAIL_STATE["offset"]:
                LOG_TAIL_STATE["offset"], LOG_TAIL_STATE["lines"] = load_log_tail(file_obj, current_size)
                LOG_TAIL_STATE["buffer"] = b""
                reset_snapshot = list(LOG_TAIL_STATE["lines"])
            elif current_size > LOG_TAIL_STATE["offset"]:
                file_obj.seek(LOG_TAIL_STATE["offset"])
                raw = file_obj.read(min(current_size - LOG_TAIL_STATE["offset"], MAX_LOG_BYTES))
                LOG_TAIL_STATE["offset"] = file_obj.tell()
                LOG_TAIL_STATE["buffer"] += raw
                pending = LOG_TAIL_STATE["buffer"].split(b"\n")
                LOG_TAIL_STATE["buffer"] = pending.pop()
                for raw_line in pending:
                    line = clean_log_line(raw_line)
                    if line:
                        LOG_TAIL_STATE["lines"].append(line)
                        new_lines.append(line)
            LOG_TAIL_STATE["error"] = None
        except OSError as exc:
            LOG_TAIL_STATE["error"] = f"Kunde inte läsa loggfilen {LOGFILE}: {exc}"

        if reset_snapshot is not None:
            publish_log_snapshot(reset_snapshot, LOG_TAIL_STATE["error"], "log-reset")
        elif new_lines:
            publish_log_lines(new_lines)

def log_tail_snapshot():
    initialize_log_tail()
    with LOG_TAIL_LOCK:
        return list(LOG_TAIL_STATE["lines"]), LOG_TAIL_STATE["error"]

def latest_match(lines, pattern, formatter):
    rx = re.compile(pattern, re.IGNORECASE)
    for line in reversed(lines):
        m = rx.search(line)
        if m:
            return formatter(m), line[:24].strip()
    return "", ""

DTMF_PTY = os.environ.get("SVXLINK_DTMF_PTY", "/dev/shm/dtmf_ctrl")
ALLOWED_TGS = {"240", "2400", "2401", "2402", "2403", "2404", "2405", "2406", "2407", "24075", "9"}

DEFAULT_BUTTONS = [
    {"label": "TG 240", "command": "91240#"},
    {"label": "TG 2400", "command": "912400#"},
    {"label": "TG 2401", "command": "912401#"},
    {"label": "TG 2402", "command": "912402#"},
    {"label": "TG 2403", "command": "912403#"},
    {"label": "TG 2404", "command": "912404#"},
    {"label": "TG 2405", "command": "912405#"},
    {"label": "TG 2406", "command": "912406#"},
    {"label": "TG 2407", "command": "912407#"},
    {"label": "TG 24075", "command": "9124075#"},
    {"label": "TG 9", "command": "919#"},
]

def validate_buttons(buttons):
    if not isinstance(buttons, list) or len(buttons) > 30:
        raise ValueError("Ange högst 30 knappar.")
    clean = []
    for item in buttons:
        if not isinstance(item, dict):
            raise ValueError("Ogiltig knappdefinition.")
        label = str(item.get("label", "")).strip()
        command = str(item.get("command", "")).strip()
        if not label or len(label) > 40:
            raise ValueError("Knappnamn måste vara 1–40 tecken.")
        if not command or len(command) > 20 or not re.fullmatch(r"[0-9*#]+", command):
            raise ValueError("DTMF måste vara 1–20 tecken: 0–9, * eller #.")
        clean.append({"label": label, "command": command})
    return clean

def load_buttons():
    try:
        with open(BUTTONS_FILE, "r", encoding="utf-8") as f:
            return validate_buttons(json.load(f))
    except FileNotFoundError:
        return DEFAULT_BUTTONS

def save_buttons(buttons):
    clean = validate_buttons(buttons)
    folder = os.path.dirname(BUTTONS_FILE)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".svxlink-buttons-", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o640)
        os.replace(tmp, BUTTONS_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return clean

def validate_cron_field(value, field, minimum, maximum):
    if not isinstance(value, str) or not value or len(value) > 20:
        raise ValueError(f"Cron-fältet {field} måste vara 1–20 tecken.")
    for part in value.split(","):
        match = re.fullmatch(r"(\*|([0-9]+)(?:-([0-9]+))?)(?:/([0-9]+))?", part)
        if not match:
            raise ValueError(f"Ogiltigt cron-fält för {field}. Använd *, värde, intervall, lista eller steg.")
        if match.group(4) is not None and int(match.group(4)) < 1:
            raise ValueError(f"Cron-steget för {field} måste vara minst 1.")
        if match.group(2) is None:
            continue
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) is not None else start
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron-fältet {field} måste ligga mellan {minimum} och {maximum}.")

def validate_schedules(schedules):
    if not isinstance(schedules, list) or len(schedules) > 100:
        raise ValueError("Ange högst 100 schemaläggningar.")
    fields = (
        ("minute", "minut", 0, 59),
        ("hour", "timme", 0, 23),
        ("day", "dag i månaden", 1, 31),
        ("month", "månad", 1, 12),
        ("weekday", "veckodag", 0, 7),
    )
    clean = []
    for schedule in schedules:
        if not isinstance(schedule, dict):
            raise ValueError("Ogiltig schemaläggning.")
        label = schedule.get("label", "")
        dtmf = schedule.get("dtmf", "")
        if not isinstance(label, str) or len(label.strip()) > 40 or "\n" in label or "\r" in label:
            raise ValueError("Schemanamnet får innehålla högst 40 tecken.")
        if not isinstance(dtmf, str) or not 1 <= len(dtmf) <= 20 or not re.fullmatch(r"[0-9*#]+", dtmf):
            raise ValueError("DTMF-koden måste innehålla 1–20 tecken: 0–9, * eller #.")
        entry = {"label": label.strip(), "dtmf": dtmf}
        for key, field, minimum, maximum in fields:
            value = schedule.get(key)
            validate_cron_field(value, field, minimum, maximum)
            entry[key] = value
        clean.append(entry)
    return clean

def quoted_dtmf_target():
    if "\n" in DTMF_PTY or "\r" in DTMF_PTY:
        raise ValueError("SVXLINK_DTMF_PTY får inte innehålla radbrytningar.")
    return shlex.quote(DTMF_PTY)

def cron_job_line(schedule):
    metadata = base64.urlsafe_b64encode(
        json.dumps(schedule, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).decode("ascii")
    expression = " ".join(schedule[field] for field in ("minute", "hour", "day", "month", "weekday"))
    command = f"printf '\\%s\\n' '{schedule['dtmf']}' > {quoted_dtmf_target()}"
    return f"{expression} {command} # SVXLINK-WEBPANEL:{metadata}"

def read_crontab():
    try:
        result = subprocess.run(
            [CRONTAB_COMMAND, "-l"],
            capture_output=True, text=True, timeout=10, check=False,
            encoding="utf-8",
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Kommandot crontab saknas. Installera cron innan du använder schemaläggningen.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Tidsgränsen överskreds när crontab skulle läsas.") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Kunde inte läsa crontab: {exc}") from exc
    if result.returncode != 0:
        if result.returncode == 1 and "no crontab for" in (result.stderr or "").lower():
            return ""
        raise RuntimeError("Kunde inte läsa svxlink-användarens crontab: " + (result.stderr or "crontab -l misslyckades.").strip())
    if len(result.stdout) > 1024 * 1024:
        raise RuntimeError("Crontaben är större än 1 MB och kan inte hanteras av panelen.")
    return result.stdout

def parse_managed_crontab(content):
    lines = content.splitlines()
    starts = [index for index, line in enumerate(lines) if line == CRONTAB_START]
    ends = [index for index, line in enumerate(lines) if line == CRONTAB_END]
    if not starts and not ends:
        return [], lines, None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError("Panelens crontab-markörer saknas eller är felaktiga; crontaben ändrades inte.")
    start, end = starts[0], ends[0]
    schedules = []
    for line in lines[start + 1:end]:
        if not line.strip():
            continue
        match = re.search(r" # SVXLINK-WEBPANEL:([A-Za-z0-9_=-]+)$", line)
        if not match:
            raise RuntimeError("Panelen hittade en okänd rad i sitt crontab-block; crontaben ändrades inte.")
        try:
            metadata = base64.urlsafe_b64decode(match.group(1).encode("ascii"))
            schedule = validate_schedules([json.loads(metadata.decode("ascii"))])[0]
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Panelens crontab-block innehåller ogiltiga schemadata; crontaben ändrades inte.") from exc
        if cron_job_line(schedule) != line:
            raise RuntimeError("En schemalagd panelrad har ändrats manuellt; crontaben ändrades inte.")
        schedule["cron_line"] = line
        schedules.append(schedule)
    other_lines = lines[:start] + lines[end + 1:]
    return schedules, other_lines, (start, end)

def schedules_response_data(content):
    schedules, other_lines, _ = parse_managed_crontab(content)
    return {
        "ok": True,
        "schedules": schedules,
        "other_lines": [line for line in other_lines if line.strip()],
        "cron_target": quoted_dtmf_target(),
    }

def get_crontab_schedules():
    with CRONTAB_LOCK:
        return schedules_response_data(read_crontab())

def save_crontab_schedules(schedules):
    clean = validate_schedules(schedules)
    with CRONTAB_LOCK:
        content = read_crontab()
        _, _, managed_range = parse_managed_crontab(content)
        lines = content.splitlines()
        if managed_range is None:
            outside_lines = lines
        else:
            start, end = managed_range
            outside_lines = lines[:start] + lines[end + 1:]
        if clean:
            managed_lines = [CRONTAB_START] + [cron_job_line(schedule) for schedule in clean] + [CRONTAB_END]
            if outside_lines and outside_lines[-1].strip():
                outside_lines.append("")
            lines = outside_lines + managed_lines
        else:
            lines = outside_lines
        updated = "\n".join(lines).rstrip("\n")
        if updated:
            updated += "\n"
        if updated == content:
            return schedules_response_data(updated)
        command = [CRONTAB_COMMAND, "-"] if updated else [CRONTAB_COMMAND, "-r"]
        try:
            result = subprocess.run(
                command,
                input=updated if updated else None, capture_output=True, text=True, timeout=15, check=False,
                encoding="utf-8",
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Kommandot crontab saknas. Installera cron innan du använder schemaläggningen.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Tidsgränsen överskreds när crontab skulle sparas.") from exc
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"Kunde inte spara crontab: {exc}") from exc
        if result.returncode != 0:
            if command[-1] == "-r" and "no crontab for" in (result.stderr or "").lower():
                return schedules_response_data(updated)
            details = (result.stderr or result.stdout or "crontab misslyckades.").strip()
            raise RuntimeError("Kunde inte spara crontab: " + details[:500])
    return schedules_response_data(updated)

def send_dtmf(command):
    # Only validated DTMF strings are accepted.
    if command == "disconnect":
        digits = "9#"
        message = "Frånkopplingskommando skickat (9#)."
    elif command.startswith("tg:") and (command[3:] in ALLOWED_TGS or re.fullmatch(r"[0-9]{1,10}", command[3:])):
        digits = "91" + command[3:] + "#"
        message = f"Anslutningskommando för TG {command[3:]} skickat."
    elif command.startswith("dtmf:"):
        digits = command[5:]
        if not digits or len(digits) > 20 or not re.fullmatch(r"[0-9*#]+", digits):
            return {"ok": False, "error": "DTMF måste innehålla 1–20 tecken: 0–9, * eller #."}, 400
        message = f"DTMF-kommando skickat: {digits}"
    else:
        return {"ok": False, "error": "Otillåtet kommando."}, 400
    try:
        # PTY is a symlink created by SvxLink. Write directly, without a shell.
        with open(DTMF_PTY, "w", encoding="ascii") as pty:
            pty.write(digits + "\n")
            pty.flush()
        return {"ok": True, "message": message}, 200
    except Exception as exc:
        return {"ok": False, "error": f"Kunde inte skriva till {DTMF_PTY}: {exc}"}, 500

def status_data():
    ensure_radio_reader()
    lines, log_error = log_tail_snapshot()
    if log_error:
        lines.append(log_error)
    tg, tgtime = latest_match(
        lines, r"(Talker start|Talker stop) on TG #([0-9]+)(?::\s*([^\s\x00]+))?",
        lambda m: f"TG {m.group(2)} · {m.group(1)}" + (f" · {m.group(3)}" if m.group(3) else "")
    )
    current_tg, _ = latest_match(
        lines, r"Selecting TG #([0-9]+)",
        lambda m: m.group(1)
    )
    monitor_tgs, monitor_tgs_error = read_monitor_tgs()
    service, active = service_status()
    radio = radio_state_snapshot()
    pty_checks = check_pty_status(active)
    errors = sum(1 for line in lines if re.search(r"ERROR|FATAL|CRITICAL", line, re.I))
    warnings = sum(1 for line in lines if re.search(r"WARNING|UDP frame\\(s\\) lost", line, re.I))
    events = sum(1 for line in lines if re.search(r"ReflectorLogic|Talker start|Talker stop|Node joined|Node left|Selecting TG", line, re.I))
    return {"service_status": service, "active": active, "lines": lines,
            "tg_event": tg, "tg_time": tgtime, "current_tg": current_tg,
            "monitor_tgs": monitor_tgs,
            "monitor_tgs_error": monitor_tgs_error,
            "radio": radio,
            "pty_checks": pty_checks,
            "errors": errors, "warnings": warnings, "events": events}

def read_config_sections():
    global CONFIG_CACHE_KEY, CONFIG_CACHE_SECTIONS
    with CONFIG_CACHE_LOCK:
        with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
            file_stat = os.fstat(config_file.fileno())
            cache_key = (
                file_stat.st_dev,
                file_stat.st_ino,
                file_stat.st_size,
                file_stat.st_mtime_ns,
            )
            if cache_key == CONFIG_CACHE_KEY:
                return CONFIG_CACHE_SECTIONS

            sections = {}
            current_section = None
            for raw_line in config_file:
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and "]" in line:
                    current_section = line[1:line.index("]")].strip()
                    sections.setdefault(current_section, {})
                    continue
                if current_section is None or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                sections[current_section][key.strip()] = value.split("#", 1)[0].split(";", 1)[0].strip()

            CONFIG_CACHE_KEY = cache_key
            CONFIG_CACHE_SECTIONS = sections
            return sections

def read_active_logics(sections):
    active_logics = []
    logic_names = [name.strip() for name in sections.get("GLOBAL", {}).get("LOGICS", "").split(",") if name.strip()]
    for logic_name in logic_names:
        logic = sections.get(logic_name, {})
        if logic.get("TYPE", "").strip('"\'').lower() in ("simplex", "repeater"):
            active_logics.append((logic_name, logic))
    return active_logics

def read_active_logic(sections):
    active_logics = read_active_logics(sections)
    return active_logics[0] if active_logics else None

def read_config_callsign():
    try:
        sections = read_config_sections()
    except (OSError, UnicodeError):
        return ""

    for _, logic in read_active_logics(sections):
        callsign = logic.get("CALLSIGN", "").strip().strip('"\'')
        if callsign:
            return callsign
    return ""

def check_pty_status(service_active):
    try:
        sections = read_config_sections()
    except (OSError, UnicodeError) as exc:
        return [{"variable": "svxlink.conf", "logic": None, "path": CONFIG_FILE,
                 "reason": f"Kunde inte läsa konfigurationen: {exc}"}]

    active_logic = read_active_logic(sections)
    if active_logic is None:
        return [{"variable": "LOGICS", "logic": None, "path": None,
                 "reason": "Ingen aktiv simplex- eller repeaterlogik hittades i [GLOBAL] LOGICS."}]

    logic_name, logic = active_logic
    issues = []
    pty_settings = (
        ("DTMF_CTRL_PTY", DTMF_PTY, os.W_OK),
        ("STATE_PTY", logic.get("STATE_PTY", "").strip().strip('"\'') or None, os.R_OK),
    )
    for variable, configured_path, access_mode in pty_settings:
        if not configured_path:
            issues.append({
                "variable": variable,
                "logic": logic_name,
                "path": None,
                "reason": f"{variable} saknas eller är kommenterad i [{logic_name}].",
            })
            continue
        if variable == "DTMF_CTRL_PTY":
            config_path = logic.get(variable, "").strip().strip('"\'')
            if not config_path:
                issues.append({
                    "variable": variable,
                    "logic": logic_name,
                    "path": configured_path,
                    "reason": f"{variable} saknas eller är kommenterad i [{logic_name}].",
                })
                continue
            if os.path.normpath(config_path) != os.path.normpath(configured_path):
                issues.append({
                    "variable": variable,
                    "logic": logic_name,
                    "path": configured_path,
                    "reason": f"Panelen använder {configured_path}, men [{logic_name}] anger {config_path}. Sökvägarna måste vara samma.",
                })
                continue
        if not service_active:
            continue
        try:
            os.stat(configured_path)
        except OSError as exc:
            issues.append({
                "variable": variable,
                "logic": logic_name,
                "path": configured_path,
                "reason": f"{configured_path} är inte aktiv eller kan inte nås: {exc.strerror or exc}.",
            })
            continue
        if not os.access(configured_path, access_mode):
            permission = "skrivbar" if access_mode == os.W_OK else "läsbar"
            issues.append({
                "variable": variable,
                "logic": logic_name,
                "path": configured_path,
                "reason": f"Paneltjänsten saknar behörighet att använda {configured_path}; sökvägen måste vara {permission} för användaren svxlink.",
            })
    return issues

def reset_state_pty_cache():
    fd = STATE_PTY_CACHE["fd"]
    STATE_PTY_CACHE.update({
        "key": None,
        "fd": None,
        "buffer": b"",
        "receivers": {},
        "transmitters": {},
        "seen": False,
    })
    if fd is not None:
        os.close(fd)

def publish_radio_state(state):
    global RADIO_EVENT_SEQUENCE, RADIO_STATE
    with RADIO_EVENT_CONDITION:
        if state == RADIO_STATE:
            return
        RADIO_STATE = state
        RADIO_EVENT_SEQUENCE += 1
        RADIO_EVENTS.append((RADIO_EVENT_SEQUENCE, state))
        RADIO_EVENT_CONDITION.notify_all()

def current_radio_state(parse_error=None):
    if not STATE_PTY_CACHE["seen"]:
        return {"mode": "unknown", "signal_level": None, "error": parse_error or "Väntar på status från SvxLink."}

    receiving_levels = [
        receiver["siglev"]
        for receiver in STATE_PTY_CACHE["receivers"].values()
        if receiver["receiving"]
    ]
    if any(STATE_PTY_CACHE["transmitters"].values()):
        mode = "tx"
        signal_level = None
    elif receiving_levels:
        mode = "rx"
        signal_level = max(receiving_levels)
    else:
        mode = "idle"
        signal_level = None
    return {"mode": mode, "signal_level": signal_level, "error": parse_error}

def radio_state_snapshot():
    with RADIO_EVENT_CONDITION:
        return RADIO_STATE

def radio_reader_loop():
    service_checked_at = 0
    service_active = False
    while True:
        try:
            now = time.monotonic()
            if now >= service_checked_at:
                _, service_active = service_status()
                service_checked_at = now + 1
            read_radio_state(service_active)
            read_log_tail_updates()
        except Exception:
            logging.exception("Fel i bakgrundsläsaren för SvxLink-status och loggar")
        time.sleep(0.05)

def ensure_radio_reader():
    global RADIO_READER_STARTED
    with RADIO_EVENT_CONDITION:
        if RADIO_READER_STARTED:
            return
        RADIO_READER_STARTED = True
    threading.Thread(target=radio_reader_loop, name="svxlink-state-reader", daemon=True).start()

def read_radio_state(service_active):
    if not service_active:
        with STATE_PTY_LOCK:
            reset_state_pty_cache()
        state = {"mode": "unknown", "signal_level": None, "error": "SvxLink-tjänsten är stoppad."}
        publish_radio_state(state)
        return state

    try:
        sections = read_config_sections()
    except (OSError, UnicodeError) as exc:
        with STATE_PTY_LOCK:
            reset_state_pty_cache()
        state = {"mode": "unknown", "signal_level": None, "error": f"Kunde inte läsa {CONFIG_FILE}: {exc}"}
        publish_radio_state(state)
        return state

    active_logic = read_active_logic(sections)
    if active_logic is None:
        with STATE_PTY_LOCK:
            reset_state_pty_cache()
        state = {"mode": "unknown", "signal_level": None, "error": "Ingen aktiv simplex- eller repeaterlogik hittades i [GLOBAL] LOGICS."}
        publish_radio_state(state)
        return state

    logic_name, logic = active_logic
    state_pty = logic.get("STATE_PTY", "").strip().strip('"\'')
    if not state_pty:
        with STATE_PTY_LOCK:
            reset_state_pty_cache()
        state = {"mode": "unknown", "signal_level": None, "error": f"STATE_PTY saknas i [{logic_name}]."}
        publish_radio_state(state)
        return state

    key = (logic_name, state_pty)
    with STATE_PTY_LOCK:
        if STATE_PTY_CACHE["key"] != key:
            reset_state_pty_cache()
            STATE_PTY_CACHE["key"] = key

        try:
            if STATE_PTY_CACHE["fd"] is None:
                STATE_PTY_CACHE["fd"] = os.open(
                    state_pty,
                    os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0),
                )
            remaining = 65536
            while remaining > 0:
                try:
                    chunk = os.read(STATE_PTY_CACHE["fd"], min(4096, remaining))
                except BlockingIOError:
                    break
                if not chunk:
                    break
                STATE_PTY_CACHE["buffer"] += chunk
                remaining -= len(chunk)
        except OSError as exc:
            reset_state_pty_cache()
            state = {"mode": "unknown", "signal_level": None, "error": f"Kunde inte läsa STATE_PTY {state_pty}: {exc}"}
            publish_radio_state(state)
            return state

        buffer = STATE_PTY_CACHE["buffer"]
        state_lines = buffer.split(b"\n")
        STATE_PTY_CACHE["buffer"] = state_lines.pop()
        parse_error = None
        if len(STATE_PTY_CACHE["buffer"]) > 65536:
            STATE_PTY_CACHE["buffer"] = b""
            parse_error = "Statusraden från STATE_PTY var för lång."

        for raw_line in state_lines:
            try:
                parts = raw_line.decode("utf-8").split(" ", 2)
                if len(parts) != 3:
                    continue
                event_name, payload = parts[1], parts[2]
                if event_name not in ("Rx:sql_state", "Voter:sql_state", "Tx:state"):
                    continue
                data = json.loads(payload)
                if event_name == "Tx:state":
                    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"] or not isinstance(data.get("transmit"), bool):
                        raise ValueError("felaktig sändarstatus")
                    STATE_PTY_CACHE["transmitters"][data["name"]] = data["transmit"]
                elif event_name == "Rx:sql_state":
                    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"] or not isinstance(data.get("sql_open"), bool):
                        raise ValueError("felaktig mottagarstatus")
                    siglev = data.get("siglev")
                    if not isinstance(siglev, int) or isinstance(siglev, bool):
                        raise ValueError("ogiltig signalnivå")
                    STATE_PTY_CACHE["receivers"][data["name"]] = {
                        "receiving": data["sql_open"],
                        "siglev": max(0, min(siglev, 100)),
                    }
                else:
                    if not isinstance(data, list):
                        raise ValueError("felaktig voter-status")
                    for receiver in data:
                        if (not isinstance(receiver, dict) or not isinstance(receiver.get("name"), str) or not receiver["name"]
                                or not isinstance(receiver.get("enabled"), bool)
                                or not isinstance(receiver.get("sql_open"), bool)
                                or not isinstance(receiver.get("siglev"), int) or isinstance(receiver["siglev"], bool)
                                or ("active" in receiver and not isinstance(receiver["active"], bool))):
                            raise ValueError("felaktig voter-mottagarstatus")
                        STATE_PTY_CACHE["receivers"][receiver["name"]] = {
                            "receiving": receiver["enabled"] and (receiver.get("active", False) or receiver["sql_open"]),
                            "siglev": max(0, min(receiver["siglev"], 100)),
                        }
                STATE_PTY_CACHE["seen"] = True
                publish_radio_state(current_radio_state())
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                parse_error = f"Kunde inte tolka status från STATE_PTY: {exc}"

        state = current_radio_state(parse_error)
        publish_radio_state(state)
        return state

def read_monitor_tgs():
    try:
        sections = read_config_sections()
    except (OSError, UnicodeError) as exc:
        return [], f"Kunde inte läsa {CONFIG_FILE}: {exc}"

    monitor_value = sections.get("ReflectorLogic", {}).get("MONITOR_TGS")
    if monitor_value is None:
        return [], "MONITOR_TGS saknas i [ReflectorLogic]."
    if not monitor_value:
        return [], None

    talkgroups = []
    for item in monitor_value.split(","):
        item = item.strip()
        if not item.isdigit() or int(item) <= 0:
            return [], "MONITOR_TGS innehåller ett ogiltigt talgruppsvärde."
        normalized_item = str(int(item))
        if normalized_item not in talkgroups:
            talkgroups.append(normalized_item)
    return talkgroups, None

def summarize_reflector_nodes(nodes, include_callsigns=True):
    if not isinstance(nodes, dict):
        raise ValueError("Reflektorströmmen saknar ett giltigt nodes-objekt.")
    by_tg = {}
    visible_count = 0
    for callsign, node in nodes.items():
        if not isinstance(node, dict) or node.get("hidden", False):
            continue
        visible_count += 1
        try:
            tg = int(node.get("tg", 0) or 0)
        except (TypeError, ValueError):
            continue
        if tg <= 0:
            continue
        entry = by_tg.setdefault(tg, {"tg": tg, "nodes": 0, "talkers": 0})
        if include_callsigns:
            entry.setdefault("callsigns", [])
        entry["nodes"] += 1
        if node.get("isTalker") is True:
            entry["talkers"] += 1
        if include_callsigns:
            entry["callsigns"].append(str(callsign))
    talkgroups = sorted(by_tg.values(), key=lambda item: (-item["talkers"], -item["nodes"], item["tg"]))
    return {"node_count": visible_count, "talkgroups": talkgroups}

def json_pointer_parent(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Ogiltig JSON Pointer i reflektorströmmen.")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    parent = document
    for part in parts[:-1]:
        if isinstance(parent, list):
            parent = parent[int(part)]
        elif isinstance(parent, dict):
            parent = parent[part]
        else:
            raise ValueError("Ogiltig sökväg i reflektorströmmen.")
    return parent, parts[-1]

def apply_reflector_patch(document, operations):
    if not isinstance(operations, list):
        raise ValueError("Reflektorströmmen innehåller en ogiltig patch.")
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in ("add", "remove", "replace"):
            raise ValueError("Reflektorströmmen innehåller en okänd patch-åtgärd.")
        parent, key = json_pointer_parent(document, operation.get("path"))
        action = operation["op"]
        if isinstance(parent, list):
            if action == "add":
                index = len(parent) if key == "-" else int(key)
                parent.insert(index, operation.get("value"))
            elif action == "remove":
                del parent[int(key)]
            else:
                parent[int(key)] = operation["value"]
        elif isinstance(parent, dict):
            if action == "remove":
                del parent[key]
            else:
                parent[key] = operation["value"]
        else:
            raise ValueError("Ogiltig måltyp i reflektorströmmen.")
    return document

def reflector_patch_affects_summary(operations):
    for operation in operations:
        path = operation.get("path")
        if not isinstance(path, str) or not path.startswith("/nodes"):
            continue
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
        if not parts or parts[0] != "nodes":
            continue
        if parts == ["nodes"] or len(parts) == 2:
            return True
        if len(parts) >= 3 and parts[2] in ("hidden", "tg", "isTalker"):
            return True
    return False

def publish_reflector_state(state):
    global REFLECTOR_EVENT_SEQUENCE, REFLECTOR_STREAM_STATE
    with REFLECTOR_EVENT_CONDITION:
        if state == REFLECTOR_STREAM_STATE:
            return
        REFLECTOR_STREAM_STATE = state
        REFLECTOR_EVENT_SEQUENCE += 1
        REFLECTOR_EVENTS.append((REFLECTOR_EVENT_SEQUENCE, state))
        REFLECTOR_EVENT_CONDITION.notify_all()

def handle_reflector_stream_event(event_name, payload):
    global REFLECTOR_DOCUMENT, REFLECTOR_GENERATION
    if event_name == "source":
        if not isinstance(payload, dict) or payload.get("topic") != "status":
            return
        source = payload.get("source")
        connected = isinstance(source, dict) and source.get("connected") is True
        current = REFLECTOR_STREAM_STATE
        publish_reflector_state({
            **current,
            "connected": connected,
            "error": None if connected and current["ok"] else (
                source.get("error") if isinstance(source, dict) and source.get("error") else
                "Väntar på statusdata från reflektorn." if connected else "Reflektorströmmen är frånkopplad."
            ),
        })
        return
    if event_name != "patch" or not isinstance(payload, dict) or payload.get("topic") != "status":
        return

    generation = payload.get("gen")
    if REFLECTOR_GENERATION is not None and generation != REFLECTOR_GENERATION:
        REFLECTOR_DOCUMENT = {}
    REFLECTOR_GENERATION = generation
    try:
        operations = payload.get("patch")
        apply_reflector_patch(REFLECTOR_DOCUMENT, operations)
        if not reflector_patch_affects_summary(operations):
            return
        summary = summarize_reflector_nodes(REFLECTOR_DOCUMENT.get("nodes"), include_callsigns=False)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logging.warning("Ogiltig statuspatch från reflektorn: %s", exc)
        current = REFLECTOR_STREAM_STATE
        publish_reflector_state({
            **current,
            "connected": True,
            "error": f"Kunde inte tolka reflektorstatus: {exc}",
        })
        return

    current = REFLECTOR_STREAM_STATE
    same_summary = (
        current["node_count"] == summary["node_count"]
        and current["talkgroups"] == summary["talkgroups"]
    )
    if same_summary and current["ok"] and current["connected"]:
        return
    publish_reflector_state({
        "ok": True,
        "connected": True,
        "error": None,
        **summary,
        "updated_at": payload.get("ts"),
    })

def reflector_stream_loop():
    global REFLECTOR_DOCUMENT, REFLECTOR_GENERATION
    retry_delay = 1
    while True:
        REFLECTOR_DOCUMENT = {}
        REFLECTOR_GENERATION = None
        request_body = json.dumps(
            {"id": str(uuid.uuid4()), "seq": 1, "topics": {"status": 1}},
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            REFLECTOR_STREAM_URL,
            data=request_body,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "Origin": "https://reflector-sm.svxlink.org",
                "Referer": "https://reflector-sm.svxlink.org/nodes",
                "User-Agent": "SvxLinkWebPanel/1.6",
            },
            method="POST",
        )
        connection_opened_at = None
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError(f"Reflektorströmmen svarade med HTTP {response.status}.")
                if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                    raise RuntimeError("Reflektorströmmen returnerade inte text/event-stream.")
                connection_opened_at = time.monotonic()
                event_name = "message"
                data_lines = []
                while True:
                    raw_line = response.readline()
                    if not raw_line:
                        raise ConnectionError("Reflektorströmmen stängdes.")
                    if len(raw_line) > 5 * 1024 * 1024:
                        raise ValueError("En SSE-rad från reflektorn överskred 5 MB.")
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        if data_lines and event_name in ("source", "patch"):
                            event_data = json.loads("\n".join(data_lines))
                            handle_reflector_stream_event(event_name, event_data)
                        event_name = "message"
                        data_lines = []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        if sum(map(len, data_lines)) > 5 * 1024 * 1024:
                            raise ValueError("En SSE-händelse från reflektorn överskred 5 MB.")
        except Exception as exc:
            logging.warning("Reflektorströmmen tappades: %s", exc)
            if connection_opened_at is not None and time.monotonic() - connection_opened_at >= 30:
                retry_delay = 1
            current = REFLECTOR_STREAM_STATE
            publish_reflector_state({
                **current,
                "connected": False,
                "error": f"Reflektorströmmen är tillfälligt otillgänglig: {exc}",
            })
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)

def ensure_reflector_stream():
    global REFLECTOR_STREAM_STARTED
    with REFLECTOR_EVENT_CONDITION:
        if REFLECTOR_STREAM_STARTED:
            return
        REFLECTOR_STREAM_STARTED = True
    threading.Thread(target=reflector_stream_loop, name="svxlink-reflector-reader", daemon=True).start()

def reflector_status_data():
    """Fetch and summarize public reflector_proxy JSON without blocking long."""
    req = urllib.request.Request(
        REFLECTOR_PROXY_URL,
        headers={"User-Agent": "SvxLinkWebPanel/3.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Reflektorsvaret är större än 5 MB")
        data = json.loads(raw.decode("utf-8"))
        nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
        if not isinstance(nodes, dict):
            raise ValueError("JSON saknar ett giltigt nodes-objekt")
        by_tg = {}
        visible_count = 0
        for callsign, node in nodes.items():
            if not isinstance(node, dict) or node.get("hidden", False):
                continue
            visible_count += 1
            try:
                tg = int(node.get("tg", 0) or 0)
            except (TypeError, ValueError):
                continue
            if tg <= 0:
                continue
            entry = by_tg.setdefault(tg, {"tg": tg, "nodes": 0, "talkers": 0, "callsigns": []})
            entry["nodes"] += 1
            if node.get("isTalker") is True:
                entry["talkers"] += 1
            entry["callsigns"].append(str(callsign))
        talkgroups = sorted(by_tg.values(), key=lambda item: (-item["talkers"], -item["nodes"], item["tg"]))
        return {"ok": True, "node_count": visible_count, "talkgroups": talkgroups,
                "source": REFLECTOR_PROXY_URL}
    except Exception as exc:
        return {"ok": False, "error": f"Kunde inte hämta reflektorstatus: {exc}", "talkgroups": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SvxLinkWebPanel"
    sys_version = ""
    def log_message(self, fmt, *args):
        # Avoid writing request details and credentials to stdout.
        pass
    def authorized(self):
        if not PASSWORD:
            self.send_error(503, "Set SVXLINK_WEB_PASSWORD before starting the panel")
            return False
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SvxLink Control"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True
    def do_GET(self):
        if not self.authorized():
            return
        path = urlparse(self.path).path
        if path == "/api/radio-events":
            self.serve_radio_events()
            return
        if path == "/api/log-events":
            self.serve_log_events()
            return
        if path == "/api/reflector-events":
            self.serve_reflector_events()
            return
        if path == "/":
            filename, content_type = "index.html", "text/html; charset=utf-8"
        elif path in ("/schedules", "/schedules/"):
            filename, content_type = "schedules.html", "text/html; charset=utf-8"
        elif path in ("/config", "/config/"):
            filename, content_type = "config.html", "text/html; charset=utf-8"
        elif path in ("/node-info", "/node-info/"):
            filename, content_type = "node-info.html", "text/html; charset=utf-8"
        elif path in WEB_ASSETS:
            filename, content_type = WEB_ASSETS[path]
        else:
            filename, content_type = None, None
        if filename is not None:
            try:
                body = read_web_file(filename)
                if content_type.startswith("text/html"):
                    callsign = read_config_callsign()
                    callsign_suffix = html.escape(" - " + callsign) if callsign else ""
                    body = (
                        body.decode("utf-8")
                        .replace("__VERSION__", VERSION)
                        .replace("__CALLSIGN_SUFFIX__", callsign_suffix)
                        .encode("utf-8")
                    )
                status = 200
            except (OSError, UnicodeDecodeError):
                body = "Webpanelens fil kunde inte läsas.".encode("utf-8")
                content_type = "text/plain; charset=utf-8"
                status = 500
            self.send_response(status)
            self.send_header("Content-Type", content_type)
        elif path == "/api/status":
            body = json.dumps(status_data(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/reflector":
            body = json.dumps(reflector_status_data(), ensure_ascii=False).encode()
            self.send_response(200 if json.loads(body.decode()).get("ok") else 502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/buttons":
            try:
                data = {"buttons": load_buttons()}
                status = 200
            except (OSError, UnicodeError, ValueError):
                logging.exception("Kunde inte läsa snabbknappsfilen %s", BUTTONS_FILE)
                data = {
                    "ok": False,
                    "error": "Snabbknapparnas fil är ogiltig eller kan inte läsas. Kontrollera JSON-formatet och filrättigheterna.",
                }
                status = 500
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/schedules":
            try:
                data = get_crontab_schedules()
                status = 200
            except (RuntimeError, ValueError) as exc:
                data, status = {"ok": False, "error": str(exc)}, 502
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/config":
            try:
                data = config_helper_request({"action": "read"})
                status = 200
            except RuntimeError as exc:
                data, status = {"ok": False, "error": str(exc)}, 502
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/node-info":
            try:
                data = config_helper_request({"action": "read_node_info"})
                status = 200
            except RuntimeError as exc:
                data, status = {"ok": False, "error": str(exc)}, 502
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_radio_events(self):
        ensure_radio_reader()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

        def send_chunk(payload):
            self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
            self.wfile.write(payload)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        try:
            with RADIO_EVENT_CONDITION:
                sequence = RADIO_EVENT_SEQUENCE
                initial_state = RADIO_STATE
            initial = json.dumps(initial_state, ensure_ascii=False).encode("utf-8")
            send_chunk(b"event: radio\ndata: " + initial + b"\n\n")
            while True:
                with RADIO_EVENT_CONDITION:
                    RADIO_EVENT_CONDITION.wait_for(
                        lambda: RADIO_EVENT_SEQUENCE > sequence,
                        timeout=5,
                    )
                    pending = [event for event in RADIO_EVENTS if event[0] > sequence]
                    if pending:
                        sequence = pending[-1][0]
                if pending:
                    for event_sequence, state in pending:
                        payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
                        send_chunk(b"event: radio\ndata: " + payload + b"\n\n")
                else:
                    send_chunk(b": keepalive\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def serve_log_events(self):
        ensure_radio_reader()
        initialize_log_tail()
        self.close_connection = True
        with LOG_TAIL_LOCK:
            snapshot = list(LOG_TAIL_STATE["lines"])
            error = LOG_TAIL_STATE["error"]
            with LOG_EVENT_CONDITION:
                sequence = LOG_EVENT_SEQUENCE

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

        def send_chunk(payload):
            self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
            self.wfile.write(payload)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        try:
            initial = json.dumps({"lines": snapshot, "error": error}, ensure_ascii=False).encode("utf-8")
            send_chunk(b"event: log-snapshot\ndata: " + initial + b"\n\n")
            while True:
                with LOG_EVENT_CONDITION:
                    LOG_EVENT_CONDITION.wait_for(
                        lambda: LOG_EVENT_SEQUENCE > sequence,
                        timeout=5,
                    )
                    pending = [event for event in LOG_EVENTS if event[0] > sequence]
                    if pending:
                        sequence = pending[-1][0]
                if pending:
                    for _, event_type, data in pending:
                        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
                        send_chunk(f"event: {event_type}\ndata: ".encode("ascii") + payload + b"\n\n")
                else:
                    send_chunk(b": keepalive\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def serve_reflector_events(self):
        ensure_reflector_stream()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

        def send_chunk(payload):
            self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
            self.wfile.write(payload)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        try:
            with REFLECTOR_EVENT_CONDITION:
                sequence = REFLECTOR_EVENT_SEQUENCE
                initial_state = REFLECTOR_STREAM_STATE
            initial = json.dumps(initial_state, ensure_ascii=False).encode("utf-8")
            send_chunk(b"event: reflector\ndata: " + initial + b"\n\n")
            while True:
                with REFLECTOR_EVENT_CONDITION:
                    REFLECTOR_EVENT_CONDITION.wait_for(
                        lambda: REFLECTOR_EVENT_SEQUENCE > sequence,
                        timeout=5,
                    )
                    pending = [event for event in REFLECTOR_EVENTS if event[0] > sequence]
                    if pending:
                        sequence = pending[-1][0]
                if pending:
                    for _, state in pending:
                        payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
                        send_chunk(b"event: reflector\ndata: " + payload + b"\n\n")
                else:
                    send_chunk(b": keepalive\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self):
        if not self.authorized():
            return
        post_path = urlparse(self.path).path
        if post_path not in ("/api/command", "/api/buttons", "/api/service", "/api/schedules", "/api/config", "/api/node-info"):
            self.send_error(404, "Not found")
            return
        # POST requests are browser actions that can transmit DTMF or control services.
        # Require a same-origin fetch request; browsers include Origin for these POSTs.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host:
            self.send_error(403, "Origin and Host headers are required")
            return
        try:
            parsed_origin = urlparse(origin)
            if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != host.lower():
                self.send_error(403, "Cross-origin request rejected")
                return
        except Exception:
            self.send_error(403, "Invalid Origin header")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_error(415, "Content-Type must be application/json")
            return
        try:
            length_header = self.headers.get("Content-Length", "")
            if not length_header.isdigit():
                raise ValueError("Ogiltig Content-Length")
            length = int(length_header)
            max_length = MAX_CONFIG_BYTES * 6 + 65536 if post_path in ("/api/config", "/api/node-info") else (65536 if post_path == "/api/schedules" else 1024)
            if length < 1 or length > max_length:
                raise ValueError(f"Begäran måste vara mellan 1 och {max_length} byte")
            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                raise ValueError("Ofullständig begäran")
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON-roten måste vara ett objekt")
            if post_path == "/api/buttons":
                saved = save_buttons(payload.get("buttons"))
                result, status = {"ok": True, "buttons": saved}, 200
            elif post_path == "/api/service":
                action = payload.get("action", "")
                result, status = control_service(action if isinstance(action, str) else "")
            elif post_path == "/api/schedules":
                result = save_crontab_schedules(payload.get("schedules"))
                result["message"] = "Schemaläggningarna sparades i svxlink-användarens crontab."
                status = 200
            elif post_path == "/api/config":
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("Konfigurationens innehåll måste vara text.")
                expected_hash = payload.get("sha256")
                if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    raise ValueError("Ogiltig kontrollsumma för den inlästa konfigurationen.")
                content_size = len(content.encode("utf-8"))
                if not content_size or content_size > MAX_CONFIG_BYTES or "\x00" in content:
                    raise ValueError("Konfigurationsfilen måste vara mellan 1 byte och 2 MB och får inte innehålla NUL-tecken.")
                result = config_helper_request({"action": "write", "content": content, "sha256": expected_hash})
                result["message"] = "Konfigurationen sparades. En säkerhetskopia skapades."
                status = 200
            elif post_path == "/api/node-info":
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("node_info.json måste vara text.")
                expected_hash = payload.get("sha256")
                if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    raise ValueError("Ogiltig kontrollsumma för den inlästa node_info.json.")
                content_size = len(content.encode("utf-8"))
                if not content_size or content_size > MAX_CONFIG_BYTES or "\x00" in content:
                    raise ValueError("node_info.json måste vara mellan 1 byte och 2 MB och får inte innehålla NUL-tecken.")
                try:
                    json.loads(content, parse_constant=reject_json_constant)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Ogiltig JSON på rad {exc.lineno}, kolumn {exc.colno}: {exc.msg}") from exc
                result = config_helper_request({
                    "action": "write_node_info",
                    "content": content,
                    "sha256": expected_hash,
                })
                result["message"] = "node_info.json sparades. En säkerhetskopia skapades."
                status = 200
            else:
                action = payload.get("action", "")
                result, status = send_dtmf(action if isinstance(action, str) else "")
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            result, status = {"ok": False, "error": str(exc)}, 400
        except RuntimeError as exc:
            result, status = {"ok": False, "error": str(exc)}, 500
        except Exception:
            # Avoid disclosing local paths, internals, or exception details to clients.
            result, status = {"ok": False, "error": "Internt fel vid behandling av begäran."}, 500
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class HardenedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 20

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(10)
        return request, client_address

if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("Set SVXLINK_WEB_PASSWORD in the environment before starting.")
    cert_path = os.environ.get("SVXLINK_WEB_CERT", "").strip()
    key_path = os.environ.get("SVXLINK_WEB_KEY", "").strip()
    if bool(cert_path) != bool(key_path):
        raise SystemExit("Set both SVXLINK_WEB_CERT and SVXLINK_WEB_KEY, or neither.")
    httpd = HardenedThreadingHTTPServer((HOST, PORT), Handler)
    if cert_path and key_path:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = tls_context.wrap_socket(httpd.socket, server_side=True)
        print(f"SvxLink web panel listening on https://{HOST}:{PORT}")
    else:
        print(f"SvxLink web panel listening on http://{HOST}:{PORT}")
        if HOST not in ("127.0.0.1", "::1", "localhost"):
            print("WARNING: panel is bound to a non-local interface without TLS. Use a reverse proxy with HTTPS or set SVXLINK_WEB_CERT and SVXLINK_WEB_KEY; otherwise restrict access to a trusted VPN/LAN.")
    httpd.serve_forever()
