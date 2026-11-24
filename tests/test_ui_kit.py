"""Tests for checkpoint 1: ui_kit and theme.

Formatter tests and 'every badge has an icon and a word' from section 7.9.
"""
from datetime import date
import pytest

from ui_kit import (
    ACTOR,
    STATUS,
    actor_chip,
    chip_md,
    due_label,
    evidence_panel,
    inr,
    inr_short,
    kpi_card,
    stage_card,
    status_chip,
)


def test_formatting_helpers():
    assert inr(1234567.4) == "₹12,34,567"
    assert inr(999) == "₹999"
    assert inr(100000) == "₹1,00,000"
    assert inr_short(680000) == "₹6.8 lakh"
    assert inr_short(23100000) == "₹2.31 crore"
    assert due_label(date(2026, 11, 19), date(2026, 11, 21)) == ("overdue", "Overdue since 19-Nov-2026")
    assert due_label(date(2026, 11, 21), date(2026, 11, 21)) == ("due_soon", "Due 21-Nov-2026 (today)")
    assert due_label(date(2026, 11, 30), date(2026, 11, 21)) == ("on_track", "Due 30-Nov-2026 (in 9 days)")
    assert chip_md("hold") == ":orange-badge[:material/pause_circle: On hold]"


def test_every_badge_has_an_icon_and_a_word():
    for kind, (word, colour, icon) in STATUS.items():
        assert icon.startswith(":material/"), f"missing icon for {kind}"
        assert word.strip(), f"missing word for {kind}"
        md = chip_md(kind)
        assert f":{colour}-badge[" in md
        assert f"{icon} {word}" in md
    for kind, (word, colour, icon) in ACTOR.items():
        assert icon.startswith(":material/"), f"missing icon for actor {kind}"
        assert word.strip(), f"missing word for actor {kind}"
        md = actor_chip(kind)
        assert f":{colour}-badge[" in md
        assert f"{icon} {word}" in md
