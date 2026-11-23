"""No test may see a real API key or reach the real API, whatever order the tests run in.

config.load_env() reads .env lazily, the first time a setting is asked for. Before this guard,
a test that removed ANTHROPIC_API_KEY and then ran first in a session got the key back from
.env a moment later. tests/conftest.py now marks .env as already read, removes the key and
points the SDK at an address that refuses connections. Assertions are on booleans only, so a
failure can never print a key."""
import os

from invoice_desk import config


def test_no_test_can_see_a_real_api_key_or_reach_the_api():
    assert config._ENV_LOADED is True                                   # .env is never read during tests
    assert ("ANTHROPIC_API_KEY" in os.environ) is False
    assert bool(config.env("ANTHROPIC_API_KEY")) is False
    assert os.environ.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:9"    # a stray real client cannot connect
