"""Paths, environment switches and the clock. The only module that reads os.environ.

Secrets and switches come from environment variables (a local `.env` file is loaded if
present; on Streamlit Community Cloud the same names go into the app's Secrets box).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
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


def sandbox_mode() -> str:
    """INVOICE_DESK_SANDBOX = shared (default) | session.
    shared: one database for everyone who opens the app, which is what a team on one machine wants.
    session: every browser session gets its own private copy of the books, which is what a public
    demo link wants: two visitors cannot see or disturb each other's runs."""
    return "session" if env("INVOICE_DESK_SANDBOX").lower() == "session" else "shared"


def sandbox_db(sandbox_id: str) -> Path:
    """The database of one visitor's sandbox. The id is 12 hex characters, checked by the caller."""
    return runtime_dir() / "sandboxes" / sandbox_id / "invoice_desk.db"


def clean_sandboxes(max_age_hours: float = 12.0, keep: str = "") -> int:
    """Delete sandboxes nobody has touched for a while. Returns how many were removed."""
    import shutil
    import time
    root = runtime_dir() / "sandboxes"
    removed = 0
    if not root.is_dir():
        return 0
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name == keep:
            continue
        try:
            newest = max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=folder.stat().st_mtime)
            if time.time() - newest > max_age_hours * 3600:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def live_allowed() -> bool:
    """May the screen offer live Claude calls? It needs a key. Where every visitor has a
    private sandbox (a public demo) it also needs LLM_ALLOW_LIVE=1, so putting a key into
    a published app's secrets does not by itself let strangers spend it. The daily cap
    applies either way."""
    if not env("ANTHROPIC_API_KEY"):
        return False
    return sandbox_mode() == "shared" or env("LLM_ALLOW_LIVE").lower() in {"1", "true", "yes", "on"}


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


DEMO_TODAY = date(2026, 11, 21)      # the business day the bundled samples were written for
_CLOCK = threading.local()


def use_real_date(real: bool) -> None:
    """The screen's switch: process on today's real date instead of the demo date (this thread only)."""
    _CLOCK.real = bool(real)


def business_today() -> date:
    """The business date: INVOICE_DESK_TODAY when set, the real date when the screen asked for it,
    otherwise the demo date."""
    if env("INVOICE_DESK_TODAY") or getattr(_CLOCK, "real", False):
        return today()
    return DEMO_TODAY


def now_utc() -> datetime:
    """One business clock for every stamp a person sees: the business date with the real time of day (IST)."""
    real = datetime.now(IST).replace(microsecond=0)
    return datetime.combine(business_today(), real.timetz()).astimezone(timezone.utc)


DATE_FORMAT, DATETIME_FORMAT = "%d-%b-%Y", "%d-%b-%Y %H:%M"


def to_ist_text(iso_utc: str) -> str:
    """'2026-11-21T08:35:00+00:00' -> '21-Nov-2026 14:05' (IST, the one format used on screen)."""
    try:
        moment = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return str(iso_utc or "-")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).strftime(DATETIME_FORMAT)


LLM_MODES = ("fixture", "cached-live", "off")
_MODE_ALIASES = {"replay": "fixture", "fixtures": "fixture", "recorded": "fixture", "auto": "fixture", "": "fixture",
                 "live": "cached-live", "cached_live": "cached-live", "cachedlive": "cached-live", "cached": "cached-live",
                 "none": "off", "disabled": "off"}
DEFAULT_MODEL = "claude-haiku-5-5"          # the fast, low-cost model; see RESEARCH.md section 14 for why


@dataclass(frozen=True)
class LlmConfig:
    """How the Claude layer behaves. Three modes:

      fixture      replay recorded answers only; never touches the network (the default)
      cached-live  use a stored answer when there is one for this file, prompt version and
                   model; otherwise call the API once and store the answer
      off          no model, no recordings: rules only
    """
    mode: str = "fixture"
    api_key: str = ""
    model: str = DEFAULT_MODEL
    escalation_model: str = ""              # optional stronger model, tried once when the first answer yields nothing usable
    timeout_s: float = 20.0
    max_retries: int = 2
    max_output_tokens: int = 4096
    effort: str = ""                        # '' sends none, so the model's own default level applies (see RESEARCH.md 14)
    per_invoice_cap_usd: float = 0.05
    daily_cap_usd: float = 1.00
    price_input_per_mtok: float = 0.0       # 0 = use the published price table in llm.py
    price_output_per_mtok: float = 0.0
    requested: str = ""                     # what LLM_MODE asked for, when it had to be changed

    @property
    def can_call(self) -> bool:
        return self.mode == "cached-live" and bool(self.api_key)

    @property
    def label(self) -> str:
        if self.mode == "cached-live":
            return f"CACHED-LIVE ({self.model})" if self.api_key else "CACHED-LIVE without a key (recorded answers only)"
        if self.mode == "fixture":
            return "FIXTURE (recorded answers, no network)"
        return "OFF (rules only)"

    @property
    def explanation(self) -> str:
        return {"fixture": "Replays answers recorded earlier from a real model. No key is needed and nothing is sent anywhere.",
                "cached-live": "Uses a stored answer when one exists for this exact file, prompt version and model. "
                               "Otherwise it calls the AI model once, inside the cost caps, and stores the answer.",
                "off": "The model is not used at all. Documents the rules cannot read are held for a person."}[self.mode]


def _number(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def normalise_llm_mode(value: str) -> str:
    """'fixture', 'cached-live' or 'off' for anything LLM_MODE may hold. An unknown value is
    treated as 'fixture': a typing mistake must never switch live calls on."""
    value = (value or "").strip().lower()
    value = _MODE_ALIASES.get(value, value)
    return value if value in LLM_MODES else "fixture"


def llm_config(mode: str | None = None) -> LlmConfig:
    """Read the Claude settings from the environment (or the host's Secrets box).

    LLM_MODE = fixture (default) | cached-live | off. A key on its own changes nothing:
    live calls happen only when LLM_MODE says cached-live. `mode` overrides LLM_MODE (the
    UI passes the mode chosen on screen)."""
    asked = env("LLM_MODE", "fixture") if mode is None else mode
    return LlmConfig(
        mode=normalise_llm_mode(asked), api_key=env("ANTHROPIC_API_KEY"), model=env("LLM_MODEL", DEFAULT_MODEL),
        escalation_model=env("LLM_ESCALATION_MODEL"), timeout_s=_number("LLM_TIMEOUT_S", 20.0),
        max_retries=int(_number("LLM_MAX_RETRIES", 2)), max_output_tokens=int(_number("LLM_MAX_OUTPUT_TOKENS", 4096)),
        effort=env("LLM_EFFORT", "").lower(), per_invoice_cap_usd=_number("LLM_MAX_COST_PER_INVOICE_USD", 0.05),
        daily_cap_usd=_number("LLM_DAILY_CAP_USD", 1.00), price_input_per_mtok=_number("LLM_PRICE_INPUT_PER_MTOK", 0.0),
        price_output_per_mtok=_number("LLM_PRICE_OUTPUT_PER_MTOK", 0.0),
        requested=asked.strip().lower() if normalise_llm_mode(asked) != asked.strip().lower() else "")


def llm_cache_dir() -> Path:
    """Answers stored by cached-live mode on this machine (not part of the repository)."""
    return runtime_dir() / "llm_cache"


def llm_usage_db() -> Path:
    """One spend ledger for the whole installation, so the daily cap holds across every
    visitor's sandbox on a hosted demo."""
    override = env("LLM_USAGE_DB")
    return Path(override) if override else runtime_dir() / "llm_usage.db"


def ocr_mode() -> str:
    """OCR_MODE = auto (default) | live | fixture.
    auto: the installed engine when there is one, else recorded output for known files.
    fixture: recorded output first (identical results on every machine), engine otherwise."""
    mode = env("OCR_MODE", "auto").lower()
    return mode if mode in {"auto", "live", "fixture"} else "auto"
