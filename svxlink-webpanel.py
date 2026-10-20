#!/usr/bin/env python3
"""SvxLink status and log web panel. Python standard library only."""
import base64
import html
import hmac
import json
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("SVXLINK_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("SVXLINK_WEB_PORT", "8090"))
VERSION = "1.4.1"
LOGFILE = os.environ.get("SVXLINK_LOGFILE", "/var/log/svxlink")
SERVICE = os.environ.get("SVXLINK_SERVICE", "svxlink")
USERNAME = os.environ.get("SVXLINK_WEB_USER", "admin")
PASSWORD = os.environ.get("SVXLINK_WEB_PASSWORD", "")
MAX_LOG_BYTES = 256 * 1024
MAX_LINES = 250
BUTTONS_FILE = os.environ.get("SVXLINK_BUTTONS_FILE", "/var/lib/svxlink/webpanel-buttons.json")
REFLECTOR_PROXY_URL = os.environ.get("SVXLINK_REFLECTOR_PROXY_URL", "https://reflector-sm.svxlink.org/reflector_proxy")
CRONTAB_COMMAND = "crontab"
CRONTAB_START = "# BEGIN SVXLINK-WEBPANEL MANAGED SCHEDULES"
CRONTAB_END = "# END SVXLINK-WEBPANEL MANAGED SCHEDULES"
CRONTAB_LOCK = threading.Lock()

WEB_ROOT = os.environ.get("SVXLINK_WEB_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
WEB_ASSETS = {
    "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
    "/assets/common.js": ("common.js", "application/javascript; charset=utf-8"),
    "/assets/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/assets/schedules.js": ("schedules.js", "application/javascript; charset=utf-8"),
}


def read_web_file(filename):
    with open(os.path.join(WEB_ROOT, filename), "rb") as web_file:
        return web_file.read()


def service_status():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE],
            capture_output=True, text=True, timeout=2, check=False
        )
        value = (result.stdout or result.stderr).strip() or "okänt"
        return value, value == "active"
    except Exception:
        return "okänt", False

def control_service(action):
    if action not in ("start", "stop"):
        return {"ok": False, "error": "Otillåten tjänståtgärd."}, 400
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", action, SERVICE],
            capture_output=True, text=True, timeout=15, check=False
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Tidsgränsen överskreds när SvxLink skulle styras."}, 504
    except OSError as exc:
        return {"ok": False, "error": f"Kunde inte köra tjänstkommandot: {exc}"}, 500
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if "a password is required" in details.lower() or "not allowed" in details.lower():
            return {"ok": False, "error": "Panelen saknar behörighet att starta eller stoppa SvxLink. Kontrollera sudoers-konfigurationen."}, 500
        return {"ok": False, "error": f"Kunde inte {('starta' if action == 'start' else 'stoppa')} SvxLink: {details or 'systemctl misslyckades.'}"}, 500
    return {"ok": True, "message": f"SvxLink {('startas' if action == 'start' else 'stoppas')}."}, 200

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
    lines = read_log()
    tg, tgtime = latest_match(
        lines, r"(Talker start|Talker stop) on TG #(24075|2407|2406|2405|2404|2403|2402|2401|2400|240|9)(?:[0-9]{3})?:\s*([^\s\x00]+)?",
        lambda m: f"TG {m.group(2)} · {m.group(1)}" + (f" · {m.group(3).strip().split()[0]}" if m.group(3).strip() else "")
    )
    node, nodetime = latest_match(
        lines, r"Node (joined|left):\s*(\S+)",
        lambda m: f"{m.group(2)} · {m.group(1)}"
    )
    current_tg, _ = latest_match(
        lines, r"Selecting TG #([0-9]+)",
        lambda m: m.group(1)
    )
    service, active = service_status()
    errors = sum(1 for line in lines if re.search(r"ERROR|FATAL|CRITICAL", line, re.I))
    warnings = sum(1 for line in lines if re.search(r"WARNING|UDP frame\\(s\\) lost", line, re.I))
    events = sum(1 for line in lines if re.search(r"ReflectorLogic|Talker start|Talker stop|Node joined|Node left|Selecting TG", line, re.I))
    return {"service_status": service, "active": active, "lines": lines,
            "tg_event": tg, "tg_time": tgtime, "node_event": node, "node_time": nodetime,
            "current_tg": current_tg,
            "errors": errors, "warnings": warnings, "events": events}

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
        if path == "/":
            filename, content_type = "index.html", "text/html; charset=utf-8"
        elif path in ("/schedules", "/schedules/"):
            filename, content_type = "schedules.html", "text/html; charset=utf-8"
        elif path in WEB_ASSETS:
            filename, content_type = WEB_ASSETS[path]
        else:
            filename, content_type = None, None
        if filename is not None:
            try:
                body = read_web_file(filename)
                if content_type.startswith("text/html"):
                    body = body.decode("utf-8").replace("__VERSION__", VERSION).encode("utf-8")
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
            body = json.dumps({"buttons": load_buttons()}, ensure_ascii=False).encode()
            self.send_response(200)
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

    def do_POST(self):
        if not self.authorized():
            return
        post_path = urlparse(self.path).path
        if post_path not in ("/api/command", "/api/buttons", "/api/service", "/api/schedules"):
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
            max_length = 65536 if post_path == "/api/schedules" else 1024
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
            else:
                action = payload.get("action", "")
                result, status = send_dtmf(action if isinstance(action, str) else "")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
