#!/usr/bin/env python3
"""Restricted privileged reader/writer for the SvxLink configuration."""
import json
import hashlib
import os
import re
import stat
import sys
import tempfile
from datetime import datetime

CONFIG_PATH = "/etc/svxlink/svxlink.conf"
NODE_INFO_PATH = "/etc/svxlink/node_info.json"
MAX_CONFIG_BYTES = 2 * 1024 * 1024


def respond(data, status=0):
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    return status


def read_config():
    with open(CONFIG_PATH, "rb") as config_file:
        content = config_file.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Konfigurationsfilen är större än 2 MB.")
    return {
        "ok": True,
        "content": content.decode("utf-8"),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def read_node_info():
    with open(NODE_INFO_PATH, "rb") as info_file:
        content = info_file.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("node_info.json är större än 2 MB.")
    return {
        "ok": True,
        "content": content.decode("utf-8"),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def reject_json_constant(value):
    raise ValueError(f"Ogiltigt JSON-konstantvärde: {value}.")


def validate_json_document(content):
    try:
        json.loads(content, parse_constant=reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ogiltig JSON på rad {exc.lineno}, kolumn {exc.colno}: {exc.msg}") from exc


def write_document(path, content, expected_sha256, document_name):
    if not isinstance(content, str):
        raise ValueError(f"{document_name} måste vara text.")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError(f"Ogiltig kontrollsumma för {document_name}.")
    encoded = content.encode("utf-8")
    if not encoded or len(encoded) > MAX_CONFIG_BYTES:
        raise ValueError(f"{document_name} måste vara mellan 1 byte och 2 MB.")
    if b"\x00" in encoded:
        raise ValueError(f"{document_name} får inte innehålla NUL-tecken.")

    with open(path, "rb") as config_file:
        original = config_file.read(MAX_CONFIG_BYTES + 1)
    if len(original) > MAX_CONFIG_BYTES:
        raise ValueError(f"{document_name} är större än 2 MB.")
    if hashlib.sha256(original).hexdigest() != expected_sha256:
        raise ValueError(f"{document_name} har ändrats sedan den lästes. Läs om den innan du sparar.")

    file_stat = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"Sökvägen för {document_name} måste vara en vanlig fil, inte en länk.")

    directory = os.path.dirname(path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = f"{path}.bak.{timestamp}"
    backup_fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(backup_fd, "wb") as backup_file:
            backup_file.write(original)
            backup_file.flush()
            os.fsync(backup_file.fileno())
        if hasattr(os, "chown"):
            os.chown(backup_path, file_stat.st_uid, file_stat.st_gid)
        os.chmod(backup_path, stat.S_IMODE(file_stat.st_mode))
    except Exception:
        os.unlink(backup_path)
        raise

    temp_fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        with os.fdopen(temp_fd, "wb") as temp_file:
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if hasattr(os, "chown"):
            os.chown(temp_path, file_stat.st_uid, file_stat.st_gid)
        os.chmod(temp_path, stat.S_IMODE(file_stat.st_mode))
        with open(path, "rb") as config_file:
            latest = config_file.read(MAX_CONFIG_BYTES + 1)
        if hashlib.sha256(latest).hexdigest() != expected_sha256:
            raise ValueError(f"{document_name} ändrades under sparningen. Läs om den innan du försöker igen.")
        if os.name == "posix":
            sync_fd = os.open(temp_path, os.O_RDONLY)
            try:
                os.fsync(sync_fd)
            finally:
                os.close(sync_fd)
        os.replace(temp_path, path)
        if os.name == "posix":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise

    return {"ok": True, "backup": backup_path, "sha256": hashlib.sha256(encoded).hexdigest()}


def write_config(content, expected_sha256):
    return write_document(CONFIG_PATH, content, expected_sha256, "Konfigurationsfilen")


def write_node_info(content, expected_sha256):
    if not isinstance(content, str):
        raise ValueError("node_info.json måste vara text.")
    validate_json_document(content)
    return write_document(NODE_INFO_PATH, content, expected_sha256, "node_info.json")


def main():
    if os.geteuid() != 0:
        return respond({"ok": False, "error": "Hjälpprogrammet måste köras via sudo."}, 1)
    if len(sys.argv) != 1:
        return respond({"ok": False, "error": "Argument är inte tillåtna."}, 2)
    try:
        max_request_bytes = MAX_CONFIG_BYTES * 6 + 65536
        raw = sys.stdin.buffer.read(max_request_bytes + 1)
        if len(raw) > max_request_bytes:
            raise ValueError("Begäran är större än tillåtet.")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Begäran måste vara ett JSON-objekt.")
        action = request.get("action")
        if action == "read" and set(request) == {"action"}:
            return respond(read_config())
        if action == "write" and set(request) == {"action", "content", "sha256"}:
            return respond(write_config(request["content"], request["sha256"]))
        if action == "read_node_info" and set(request) == {"action"}:
            return respond(read_node_info())
        if action == "write_node_info" and set(request) == {"action", "content", "sha256"}:
            return respond(write_node_info(request["content"], request["sha256"]))
        raise ValueError("Ogiltig konfigurationsåtgärd.")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return respond({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
