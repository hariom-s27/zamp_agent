"""Reading one email that reached the AP address (round 4): headers, plain text, attachments,
the quoted part of a reply, the case token and who the sender is likely to be.

Nothing here decides anything or changes any record. Messages are files: no mail server is
connected. The text is read as plain text only; HTML is reduced to its words so that no
markup or script is ever shown or followed.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from email import message_from_bytes
from email import policy as email_policy
from email.utils import parseaddr, parsedate_to_datetime

from .masterdata import MasterData
from .rules import Rules
from .textutil import edit_distance

CASE_TOKEN = re.compile(r"(?<![0-9A-Za-z])CASE-([0-9A-Fa-f]{8})(?![0-9A-Za-z])", re.IGNORECASE)
# where the quoted part of a reply begins: "On <date>, <someone> wrote:", an Outlook divider or header block
_QUOTE_START = [
    re.compile(r"^\s*On .{0,200}wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*From:\s.+$", re.IGNORECASE),
]
_SENT_LINE = re.compile(r"^\s*(Sent|Date|To|Subject):\s", re.IGNORECASE)


@dataclass
class Attachment:
    name: str
    mime: str
    content: bytes
    sha256: str
    size: int

    @property
    def is_pdf(self) -> bool:
        return self.mime == "application/pdf" or self.name.lower().endswith(".pdf")

    @property
    def is_table(self) -> bool:
        return self.name.lower().endswith((".csv", ".xlsx")) or self.mime in (
            "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@dataclass
class ParsedMail:
    sha256: str
    message_id: str = ""
    sender: str = ""
    sender_name: str = ""
    to: str = ""
    subject: str = ""
    sent_at: str = ""                 # the Date the sender claims, ISO 8601, "" if absent or unreadable
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class SenderCheck:
    result: str                       # on_file | same_domain | lookalike | unknown
    vendor_id: str
    detail: str                       # the sentence shown on the Inbox page


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</tr\s*>|</li\s*>", "\n", markup)
    markup = re.sub(r"(?s)<[^>]*>", "", markup)
    text = html.unescape(markup).replace("<", "(").replace(">", ")")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln) + "\n"


def parse_eml(data: bytes) -> ParsedMail:
    """Everything the desk records about one received file. A file that is not a readable email still gets a
    fingerprint, so it is registered once and shown to a person."""
    parsed = ParsedMail(sha256=hashlib.sha256(data).hexdigest())
    try:
        msg = message_from_bytes(data, policy=email_policy.default)
        name, address = parseaddr(str(msg.get("From", "")))
        parsed.sender, parsed.sender_name = address.strip().lower(), name.strip()
        parsed.to = parseaddr(str(msg.get("To", "")))[1].strip().lower()
        parsed.subject = str(msg.get("Subject", "")).strip()
        parsed.message_id = str(msg.get("Message-ID", "")).strip()
        try:
            parsed.sent_at = parsedate_to_datetime(str(msg["Date"])).isoformat() if msg.get("Date") else ""
        except (TypeError, ValueError):
            parsed.sent_at = ""
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            content = body.get_content()
            parsed.text = _html_to_text(content) if body.get_content_subtype() == "html" else content
        for part in msg.iter_attachments():
            payload = part.get_payload(decode=True) or b""
            parsed.attachments.append(Attachment(
                name=part.get_filename() or "attachment", mime=part.get_content_type(), content=payload,
                sha256=hashlib.sha256(payload).hexdigest(), size=len(payload)))
    except Exception:                 # a damaged file: keep the fingerprint, a person will look at it
        return ParsedMail(sha256=parsed.sha256)
    parsed.text = parsed.text.replace("\r\n", "\n")
    return parsed


def strip_quoted(text: str) -> tuple[str, str]:
    """(what the sender wrote now, the quoted earlier messages). Lines starting with '>' are quoted, and so is
    everything from an "On ... wrote:" line, an Outlook divider or a forwarded header block onward."""
    lines = text.replace("\r\n", "\n").split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        if any(p.match(line) for p in _QUOTE_START[:3]):
            cut = i
            break
        if _QUOTE_START[3].match(line) and any(_SENT_LINE.match(n) for n in lines[i + 1:i + 4]):
            cut = i
            break
    new_lines = [ln for ln in lines[:cut] if not ln.lstrip().startswith(">")]
    quoted = [ln for ln in lines[:cut] if ln.lstrip().startswith(">")] + lines[cut:]
    return "\n".join(new_lines).strip() + "\n", "\n".join(quoted).strip()


def case_tokens(text: str) -> list[str]:
    """Case references (CASE- and 8 hex characters) in the order they appear, each once, in capitals."""
    found: list[str] = []
    for match in CASE_TOKEN.finditer(text or ""):
        token = "CASE-" + match.group(1).upper()
        if token not in found:
            found.append(token)
    return found


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_phrases(text: str, phrases: list[str]) -> list[str]:
    """The phrases (from rules.yaml) that appear in the text, ignoring capitals and line breaks."""
    flat = _squash(text)
    return [p for p in phrases if _squash(p) and _squash(p) in flat]


def _domain(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


def check_sender(address: str, master: MasterData, rules: Rules) -> SenderCheck:
    """Compare the sender with what is on file. On shared mail services only the exact address counts.
    This only compares text: a deployment would also trust the mail gateway's authentication results."""
    address = (address or "").strip().lower()
    domain = _domain(address)
    shared = {d.lower() for d in rules.inbox.shared_mail_domains}
    on_file = {v.email.strip().lower(): v for v in master.vendors.values() if v.email.strip()}
    vendor = on_file.get(address) if address else None
    if vendor is not None:
        return SenderCheck("on_file", vendor.vendor_id,
                           f"Sender matches the address on file for {vendor.legal_name}.")
    if domain and domain not in shared:
        for email_on_file, v in on_file.items():
            if _domain(email_on_file) == domain:
                return SenderCheck("same_domain", v.vendor_id,
                                   f"Sender's domain matches {v.legal_name}, but this address is new to us.")
        best = None
        for email_on_file, v in on_file.items():
            theirs = _domain(email_on_file)
            if not theirs or theirs in shared:
                continue
            distance = edit_distance(domain, theirs)
            if 0 < distance <= rules.inbox.lookalike_max_edits and (best is None or distance < best[0]):
                best = (distance, v, theirs)
        if best is not None:
            return SenderCheck("lookalike", best[1].vendor_id,
                               f"Careful: the sender's domain {domain} looks like {best[2]} and is not the same.")
    return SenderCheck("unknown", "", "Sender is not a known vendor address.")
