# Recorded Claude answers

Answers from the real Claude API, recorded once and replayed in FIXTURE mode, which is
the default. Replaying needs no key and makes no network call.

**This folder ships without recordings.** The project was built in an environment that
cannot reach the API, and an answer written by hand would be a fake. Until you record,
sample 10 (the invoice written as a letter) is held by the rules alone, and the screen
says why.

To record, put your key in `.env` (`ANTHROPIC_API_KEY=...`) and run:

    python -m scripts.record_llm_fixtures --dry-run     # what would be sent, and the most it could cost
    python -m scripts.record_llm_fixtures               # record what is missing

Then commit the JSON files here. The hosted demo replays them.

## What a file is

`<purpose>__<first 16 characters of the input's SHA-256>__<prompt version>__<model>.json`

| Field | Meaning |
|---|---|
| `purpose` | `extract` (read a document the rules could not) or `vendor_note` (reword a drafted note) |
| `key` | SHA-256 of the input: the PDF for `extract`, the drafted note for `vendor_note` |
| `prompt_version`, `template_sha256` | the prompt the answer was made with. An answer made with another prompt is ignored |
| `model`, `recorded_at`, `request_id`, `sdk` | which model answered, when, the API's request id, the SDK version |
| `usage`, `cost_usd` | tokens in and out, and what the call cost at the published price |
| `response` | the model's answer, exactly as returned |

A recording is replayed only for the same input, prompt version and model. In FIXTURE
mode, a recording made with a different model than the one configured is still replayed,
and the run record names the model that made it.

`tests/test_llm.py::test_no_recording_ships_that_did_not_come_from_the_real_api` checks
that whatever is in this folder has the shape of a real recording.
