#!/usr/bin/env python3
"""Write ~/.pi/agent/models.json (+ default model in settings.json) from the
models Ollama actually has, so you never hand-edit JSON after `ollama pull`.

models.json is only (re)generated while the marker file exists. If you want to
hand-tune it, delete ~/.pi/agent/.pibox-generated and pibox will leave it alone.

It also merges your Mac's settings.json (mounted read-only at ~/.pi-host) into the
box's own settings.json. See merge_host_settings.
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# The box reaches Ollama only through pibox's proxy container (see pibox-proxy.py).
base = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
ctx = int(os.environ.get("PIBOX_CONTEXT") or 32768)

agent_dir = Path.home() / ".pi" / "agent"
models_path = agent_dir / "models.json"
settings_path = agent_dir / "settings.json"
marker = agent_dir / ".pibox-generated"
host_settings_path = Path.home() / ".pi-host" / "settings.json"
# The Mac settings applied last time, so a key you delete on the Mac is removed here too.
synced_path = agent_dir / ".pibox-host-settings.json"

# Settings that pibox controls, or that only make sense on the Mac.
NOT_SYNCED = {
    "defaultProvider", "defaultModel",  # pibox picks an Ollama model
    "lastChangelogVersion",             # tracks the pi version in the box
    "sessionDir", "selfUpdateCommand", "httpProxy",
}


def log(msg: str) -> None:
    print(f"pibox: {msg}", file=sys.stderr)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def current_model_ids() -> list[str]:
    """Model ids in the existing models.json (empty if missing/unreadable)."""
    try:
        data = json.loads(models_path.read_text())
        return [m["id"] for m in data["providers"]["ollama"]["models"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def read_json(path: Path) -> dict | None:
    """The JSON object in path, or None if it's missing or isn't a JSON object."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_remote(model: dict) -> bool:
    """True for Ollama cloud models, which send prompts to ollama.com. The proxy
    already hides these. This is a second check."""
    name = str(model.get("name", ""))
    return bool(model.get("remote_host") or model.get("remote_model")
                or re.search(r"[:-]cloud$", name, re.I))


def fetch_tags() -> dict:
    """Ollama's model list. The proxy starts at the same moment as the box, so
    retry briefly while it comes up."""
    deadline = time.monotonic() + 3
    while True:
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as res:
                return json.load(res)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def merge_host_settings() -> None:
    """Copy your Mac's settings into the box. The Mac wins for every key it sets
    (except NOT_SYNCED). Keys set only in the box are left alone. A key you delete on
    the Mac is removed from the box too, unless you have changed it in the box since."""
    if not host_settings_path.exists():
        return
    host = read_json(host_settings_path)
    if host is None:
        log("your Mac's settings.json isn't valid JSON. Not syncing it.")
        return
    host = {k: v for k, v in host.items() if k not in NOT_SYNCED}
    before = read_json(synced_path) or {}
    settings = read_json(settings_path) or {}

    changed = 0
    for key, value in host.items():
        if settings.get(key, object()) != value:
            settings[key] = value
            changed += 1
    for key, value in before.items():
        if key not in host and key in settings and settings[key] == value:
            del settings[key]
            changed += 1

    if changed:
        write_json(settings_path, settings)
        log(f"synced {changed} setting(s) from your Mac")
    if host != before:
        write_json(synced_path, host)


def main() -> int:
    agent_dir.mkdir(parents=True, exist_ok=True)
    merge_host_settings()

    try:
        # Short timeouts: this runs on every `pibox` launch, so a closed Ollama
        # shouldn't stall startup.
        tags = fetch_tags()
    except Exception as e:  # noqa: BLE001 - any failure just means "skip"
        log(f"can't reach Ollama at {base} ({e}). Leaving pi config unchanged.")
        return 0

    names = sorted(
        m["name"] for m in tags.get("models", [])
        if not re.search("embed", m["name"], re.I) and not is_remote(m)
    )
    if not names:
        log("Ollama has no chat models yet. Try: ollama pull qwen3.5:9b")
        return 0

    if not models_path.exists() or marker.exists():
        new = json.dumps({
            "providers": {
                "ollama": {
                    "baseUrl": f"{base}/v1",
                    "api": "openai-completions",
                    "apiKey": "ollama",
                    "models": [{"id": n, "contextWindow": ctx} for n in names],
                }
            }
        }, indent=2) + "\n"
        try:
            old = models_path.read_text()
        except OSError:
            old = None
        if new != old:
            before = set(current_model_ids())
            models_path.write_text(new)
            if old is None or not before:
                log(f"registered {len(names)} Ollama model(s) with pi: {', '.join(names)}")
            else:
                diff = [f"+{n}" for n in names if n not in before]
                diff += [f"-{n}" for n in sorted(before) if n not in names]
                log(f"models updated: {' '.join(diff) or '(settings changed)'}")
        if not marker.exists():
            marker.write_text("delete this file to stop pibox regenerating models.json\n")

    # Merge (never clobber) settings.json: pi stores other preferences there too.
    try:
        settings = json.loads(settings_path.read_text())
    except (OSError, ValueError):
        settings = {}

    def first(pred):
        return next((n for n in names if pred(n)), None)

    # Keep whatever the user picked in pi. Only choose a fallback when there is
    # no default yet, or the old default was removed from Ollama.
    pick = (
        (settings.get("defaultModel") if settings.get("defaultModel") in names else None)
        or first(lambda n: re.search(r"qwen.*coder", n, re.I))
        or first(lambda n: re.search("qwen", n, re.I))
        or names[0]
    )
    if settings.get("defaultProvider") != "ollama" or settings.get("defaultModel") != pick:
        settings["defaultProvider"] = "ollama"
        settings["defaultModel"] = pick
        write_json(settings_path, settings)
        log(f"default model -> {pick}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
