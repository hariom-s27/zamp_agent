# Recorded model responses

This folder is empty on purpose.

The project was built in an environment that could not call a language model, so no
real response could be recorded, and a hand-written one would be a fake. The LLM path is
covered by tests that replace the HTTP call with a stub (`tests/test_llm.py`).

To fill it with real recordings, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in
`.env` and run:

    python -m scripts.record_llm_fixtures

Each file is named after the first 16 characters of the document's SHA-256 and holds the
model name, the time of the call and the raw reply. With a recording present, the same
document replays with no network (`LLM_MODE=replay`, which is also what `auto` does when
no key is set).
