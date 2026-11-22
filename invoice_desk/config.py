"""Paths, environment switches and the clock. The only module that reads os.environ.

Secrets and switches come from environment variables (a local `.env` file is loaded if
present; on Streamlit Community Cloud the same names go into the app's Secrets box).
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SAMPLES_DIR = ROOT / "samples"
FIXTURES_DIR = ROOT / "fixtures"
RULES_PATH = ROOT / "rules.yaml"
IST = timezone(timedelta(hours=5, minutes=30), "IST")

_ENV_LOADED = False


def load_env() -> None:
    """Load `.env` once (never overriding variables that are already set)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:  # python-dotenv missing or unreadable file: environment only
        pass


def _host_secret(name: str) -> str:
    """A value from Streamlit's Secrets box, when the app is running under Streamlit and
    one was set. Streamlit normally exports these as environment variables too; this
    covers a host that does not. The engine itself never imports Streamlit."""
    module = sys.modules.get("streamlit")
    if module is None:
        return ""
    try:
        value = module.secrets.get(name, "")
        return value if isinstance(value, str) else ""
    except Exception:       # no secrets file, or not running inside an app
        return ""


def env(name: str, default: str = "") -> str:
    load_env()
    return (os.environ.get(name) or _host_secret(name) or default).strip()


def runtime_dir() -> Path:
    """Folder for the SQLite file and stored uploads. Falls back to the temp folder when
    the project folder is read-only (some hosts mount the code that way)."""
    override = env("INVOICE_DESK_HOME")
    candidates = [Path(override)] if override else []
    candidates += [DATA_DIR / "runtime", Path(tempfile.gettempdir()) / "invoice_desk"]
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return folder
        except OSError:
            continue
    raise RuntimeError("no writable folder for runtime data")


def db_path() -> Path:
    override = env("INVOICE_DESK_DB")
    return Path(override) if override else runtime_dir() / "invoice_desk.db"


def today() -> date:
    """Today's date, or the frozen demo date in INVOICE_DESK_TODAY (YYYY-MM-DD).
    A frozen clock keeps date rules from changing the result of a rehearsed demo
    (idea from the controlplane project, which froze its demo date for the same reason)."""
    frozen = env("INVOICE_DESK_TODAY")
    if frozen:
        try:
            return date.fromisoformat(frozen)
        except ValueError:
            pass
    return datetime.now(IST).date()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_ist_text(iso_utc: str) -> str:
    """'2026-11-21T16:05:00+00:00' -> '21-Nov-2026 21:35 IST'."""
    try:
        moment = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return str(iso_utc or "-")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).strftime("%d-%b-%Y %H:%M IST")


@dataclass(frozen=True)
class LlmConfig:
    mode: str        # off | live | replay   (resolved; 'auto' never leaves this module)
    base_url: str
    api_key: str
    model: str
    timeout_s: float

    @property
    def label(self) -> str:
        if self.mode == "live":
            return f"LIVE ({self.model})"
        if self.mode == "replay":
            return "REPLAY (recorded responses)"
        return "OFF (rules only)"


def llm_config() -> LlmConfig:
    """LLM_MODE = auto (default) | off | live | replay.
    auto: live when a key, base URL and model are all set, otherwise replay (which uses a
    recorded response when one exists for the document and does nothing when none does)."""
    mode = env("LLM_MODE", "auto").lower()
    base_url, api_key, model = env("LLM_BASE_URL"), env("LLM_API_KEY"), env("LLM_MODEL")
    try:
        timeout = float(env("LLM_TIMEOUT_S", "25"))
    except ValueError:
        timeout = 25.0
    configured = bool(base_url and api_key and model)
    if mode == "off":
        resolved = "off"
    elif mode == "live":
        resolved = "live" if configured else "off"
    elif mode == "replay":
        resolved = "replay"
    else:
        resolved = "live" if configured else "replay"
    return LlmConfig(resolved, base_url, api_key, model, timeout)


def ocr_mode() -> str:
    """OCR_MODE = auto (default) | live | fixture.
    auto: the installed engine when there is one, else recorded output for known files.
    fixture: recorded output first (identical results on every machine), engine otherwise."""
    mode = env("OCR_MODE", "auto").lower()
    return mode if mode in {"auto", "live", "fixture"} else "auto"
