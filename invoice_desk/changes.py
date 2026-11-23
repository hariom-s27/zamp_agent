"""Procedure change requests (round 4), step 1: propose a change to one of the six numbers the
what-if sandbox exposes, replay it against every past document on scratch books, and save it
as a change request.

Nothing in this step changes how invoices are decided: `in_force` returns rules.yaml itself.
Replay uses recorded AI answers only (FIXTURE mode) and never makes a live call. Approval by a
second person, a version and a rollback are step 2 (checkpoint 9), not built in this round.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from . import config
from .llm import LlmAssist
from .masterdata import MasterData
from .rules import Rules
from .store import Store, canonical

LABEL = {"tolerances.unit_price.pct": ("unit price tolerance", "%"),
         "tolerances.unit_price.abs_inr": ("unit price tolerance in rupees per line", ""),
         "tolerances.po_total.pct": ("PO value tolerance", "%"),
         "tolerances.po_total.abs_inr": ("PO value tolerance in rupees", ""),
         "auto_approve.max_invoice_total_inr": ("auto-approval ceiling in rupees", ""),
         "duplicates.window_days": ("duplicate window in days", "")}
OFF = "Change requests are switched off in rules.yaml (changes.enabled). rules.yaml alone is in force."
CAPTION = ("The procedure is rules.yaml. A change is proposed by one person, tested against past invoices, approved by "
           "a second person, and can be undone.")
LIMIT_LINE = ("Replay re-runs the documents in arrival order on a scratch copy of the books. Decisions people made in "
              "between are not replayed.")
STEP_TWO = ("Approval by a second person, putting a change in force and rolling it back come in the next step and are "
            "not built in this round. A saved request changes nothing.")
NAMES = "Names are typed in this build. A deployment would take them from the login."


class ChangeError(Exception):
    pass


def _get(rules: Rules, key: str) -> Any:
    value: Any = rules
    for part in key.split("."):
        value = getattr(value, part)
    return value


def _set(rules: Rules, key: str, value: Any) -> None:
    *path, last = key.split(".")
    target: Any = rules
    for part in path:
        target = getattr(target, part)
    setattr(target, last, int(value) if isinstance(getattr(target, last), int) else Decimal(value))


def label(key: str) -> str:
    return LABEL.get(key, (key, ""))[0]


def _show(key: str, value: Any) -> str:
    return f"{value}{LABEL.get(key, ('', ''))[1]}"


def validate(rules: Rules, proposed: dict[str, Any]) -> list[dict[str, str]]:
    """[{key, old, new}] for a proposal inside the rules, or ChangeError with the reason in plain words."""
    c = rules.changes
    if not proposed:
        raise ChangeError("Choose at least one number to change.")
    if len(proposed) > c.max_keys_per_request:
        raise ChangeError(f"A request may change at most {c.max_keys_per_request} numbers together.")
    out = []
    for key, raw in proposed.items():
        if key not in c.allowed:
            raise ChangeError(f"{key} cannot be changed by a change request. Only these can: {', '.join(c.allowed)}.")
        try:
            value = Decimal(str(raw))
        except InvalidOperation as exc:
            raise ChangeError(f"The new value for the {label(key)} must be a number.") from exc
        bounds = c.allowed[key]
        if not bounds.min <= value <= bounds.max:
            raise ChangeError(f"The {label(key)} can be set between {_show(key, bounds.min)} and {_show(key, bounds.max)}.")
        old = _get(rules, key)
        if Decimal(str(old)) == value:
            raise ChangeError(f"The {label(key)} is already {_show(key, old)}.")
        out.append({"key": key, "old": str(old), "new": str(value.normalize() if key.endswith("pct") else value)})
    return out


def apply(rules: Rules, proposed: dict[str, Any]) -> Rules:
    """rules.yaml with the proposal applied, on a copy with its own version and hash. No change: the base object."""
    if not proposed:
        return rules
    diff = validate(rules, proposed)
    trial = rules.model_copy(deep=True)
    for d in diff:
        _set(trial, d["key"], d["new"])
    trial.version = f"{rules.version}+proposal"
    trial.sha256 = hashlib.sha256((rules.sha256 + canonical({"changes": diff})).encode("utf-8")).hexdigest()
    return trial


def in_force(store: Store, rules: Rules) -> Rules:
    """The procedure in force. In step 1 no change is ever in force, so this is rules.yaml itself."""
    return rules


def _documents(store: Store, limit: int) -> tuple[list[dict], bool]:
    rows = [r for r in reversed(store.list_runs()) if r.get("file_sha256") and r.get("verdict")]
    docs = [r for r in rows if store.load_file(r["file_sha256"]) is not None]
    return docs[:limit], len(docs) > limit


def replay(store: Store, master: MasterData, rules: Rules, proposed: dict[str, Any], today: date,
           ocr_mode: Optional[str] = None, llm: Optional[LlmAssist] = None) -> dict[str, Any]:
    """Every past document, in arrival order, decided twice on scratch copies of the seeded books: with the procedure
    in force now and with the change. The real books are not touched."""
    from .pipeline import Pipeline                      # imported here: the pipeline imports the stages, not this module
    if not rules.changes.enabled:
        raise ChangeError(OFF)
    trial = apply(rules, proposed)
    docs, limit_hit = _documents(store, rules.changes.replay_max_documents)
    base_llm = llm.cfg if llm is not None else config.LlmConfig(mode="fixture")
    fixtures = llm.fixtures_dir if llm is not None else None
    differ, counts = [], {"no_longer": 0, "newly_held": 0, "newly_refused": 0}
    with tempfile.TemporaryDirectory(prefix="invoice_desk_replay_") as tmp:
        lines = {}
        for name, used in (("now", rules), ("change", trial)):
            scratch = Store(Path(tmp) / f"{name}.db", data_dir=store.data_dir)
            reader = LlmAssist(dataclasses.replace(base_llm, mode="fixture"), fixtures_dir=fixtures,
                               cache_dir=Path(tmp) / f"{name}_cache")      # stored answers only, never a live call
            pipe = Pipeline(store=scratch, rules=used, master=master, llm=reader, today=today, ocr_mode=ocr_mode)
            lines[name] = [pipe.run(store.load_file(r["file_sha256"]), r["file_name"] or "document.pdf", "replay")
                           for r in docs]
        for row, now, new in zip(docs, lines["now"], lines["change"]):
            a = {f.code for f in now.decision.reasons} if now.decision else set()
            b = {f.code for f in new.decision.reasons} if new.decision else set()
            if now.verdict == new.verdict and a == b:
                continue
            differ.append({"run_id": row["run_id"], "file_name": row["file_name"], "vendor": row.get("vendor_name") or "",
                           "amount": row.get("total") or "", "now": now.verdict, "with_change": new.verdict,
                           "gone": sorted(a - b), "new": sorted(b - a)})
            counts["no_longer"] += now.verdict == "HOLD" and new.verdict == "APPROVE"
            counts["newly_held"] += now.verdict == "APPROVE" and new.verdict == "HOLD"
            counts["newly_refused"] += now.verdict != "REJECT" and new.verdict == "REJECT"
    n = len(docs)
    return {"count": n, "differ": differ, "limit_hit": limit_hit, "last_document": docs[-1]["run_id"] if docs else "",
            "changes": validate(rules, proposed),
            "summary": (f"{n} document{'s' if n != 1 else ''} replayed. {len(differ)} would have been decided "
                        f"differently."),
            "direction": (f"Would no longer need a person: {counts['no_longer']}. Would newly need a person: "
                          f"{counts['newly_held']}. Would newly be refused: {counts['newly_refused']}.")}


def save(store: Store, rules: Rules, proposed: dict[str, Any], proposer: str, reason: str,
         replayed: dict[str, Any]) -> str:
    """Store the proposal with its replay as a change request, status PROPOSED. Nothing about decisions changes."""
    proposer, reason = (proposer or "").strip(), (reason or "").strip()
    if not proposer:
        raise ChangeError("Enter your name: a change request is recorded against the person who proposed it.")
    if not reason:
        raise ChangeError("Write why: a change request needs a reason.")
    diff = validate(rules, proposed)
    if [d["key"] for d in replayed.get("changes", [])] != [d["key"] for d in diff] or \
            [d["new"] for d in replayed.get("changes", [])] != [d["new"] for d in diff]:
        raise ChangeError("Replay this change before saving it.")
    taken = set()
    for row in store.change_requests():
        if row["status"] in ("PROPOSED", "ACTIVE"):
            taken |= {d["key"] for d in json.loads(row["changes_json"])}
    clash = [d["key"] for d in diff if d["key"] in taken]
    if clash:
        raise ChangeError(f"The {label(clash[0])} is already proposed in another change request.")
    change_id = f"CR-{len(store.change_requests()) + 1:04d}"
    store.add_change_request({"change_id": change_id, "changes_json": json.dumps(diff), "reason": reason,
                              "proposed_by": proposer, "proposed_at": config.now_utc().isoformat(),
                              "replay_json": json.dumps(replayed, default=str), "status": "PROPOSED",
                              "base_rules_sha256": rules.sha256})
    return change_id


def describe(diff: list[dict[str, str]]) -> str:
    """'Unit price tolerance: 1.0% to 1.1%. Unit price tolerance in rupees per line: 500 to 550.'"""
    return " ".join(f"{label(d['key'])[0].upper()}{label(d['key'])[1:]}: {_show(d['key'], d['old'])} to "
                    f"{_show(d['key'], d['new'])}." for d in diff)


def yaml_difference(rules: Rules, proposed: dict[str, Any], proposer: str, reason: str) -> str:
    """The proposal as a small YAML difference. On a real deployment this file is the pull request against rules.yaml."""
    diff = validate(rules, proposed)
    lines = [f"# Change request proposed by {proposer.strip() or 'unnamed'}: {reason.strip() or 'no reason given'}",
             f"# Against rules.yaml version {rules.version} (sha256 {rules.sha256[:12]})"]
    tree: dict = {}
    for d in diff:
        node = tree
        *path, last = d["key"].split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = d
    def walk(node: dict, depth: int) -> None:
        for name, value in node.items():
            if "key" in value and isinstance(value.get("key"), str):
                lines.append(f"{'  ' * depth}{name}: {value['new']}    # was {value['old']}")
            else:
                lines.append(f"{'  ' * depth}{name}:")
                walk(value, depth + 1)
    walk(tree, 0)
    return "\n".join(lines) + "\n"
