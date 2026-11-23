# Round 2 verification report (Windows)

Run on 22 and 23 Nov 2026 on the Windows 11 laptop, in this folder, following SETUP_WINDOWS.md.
Python 3.11.16 (uv's interpreter; `py -3.11` is broken on this machine), Tesseract 5.4.0.20240606
at `C:\Program Files\Tesseract-OCR`. Every command used `.venv\Scripts\python`.

**Summary: every check gave the documented result. No code was changed.**

## 1. Setup

| Command | Expected | Actual |
|---|---|---|
| `& (uv python find 3.11) -m venv .venv`, then `pip install --upgrade pip` and `pip install -r requirements.txt` | installs | Python 3.11.16, install ended with exit code 0 |
| `python -m pytest` | 561 passed | `======================= 561 passed in 151.61s (0:02:31) =======================` |
| `python -m scripts.preflight` | READY, one WARN (no Claude recording) | `READY (1 warning(s) ...)`. The only WARN: "recorded answer for the letter invoice (sample 10): none yet". Tesseract ok; 18 of 18 samples and 11 of 11 demo steps as documented; all six pages open. |
| `python -m invoice_desk.run --all` | 18 of 18 | `18 of 18 samples gave the expected decision (LLM: FIXTURE ...; OCR: Tesseract 5.4.0.20240606)` |
| `python -m scripts.evaluate_robustness` | 30 / 15 / 4 / 0 | `49 documents: 30 approved-correct, 15 held-correct, 4 misread-held (0 false alarms), 0 misread-approved; values 691/705` |

## 2. Claude key and recordings

The old folder had no `.env`. `.env.example` was copied to `.env` and the owner put the key in it.
The key was never printed or logged. Only its presence and length were checked. No file in
`fixtures/llm/` contains `sk-ant`.

| Command | Expected | Actual |
|---|---|---|
| `scripts.record_llm_fixtures --dry-run` | lists requests, sends nothing | 11 rows (1 extract, 10 vendor notes), worst case about $0.0021 to $0.0023 each; "Nothing was sent" |
| `scripts.preflight --llm` | key works | `live call: claude-haiku-5-5 answered 'ready'; 18 tokens in, 4 out, $0.00000`; READY with the same single WARN (no recording yet) |
| `scripts.record_llm_fixtures` | sample 10 APPROVE | `10_ai_assist_letter_invoice  extract  RECORDED  APPROVE  1883 in, 1804 out, $0.0011; accepted: invoice_number, subtotal, igst, total, line_items`. All 6 vendor-note rewordings accepted. `7 recording(s) in fixtures/llm; spent today $0.0038 over 7 call(s)` |
| Fallback (LLM_EFFORT=high, then claude-sonnet-5-5) | only if sample 10 is not APPROVE | **Not needed**: APPROVE on the first recording with claude-haiku-5-5 at default effort |
| `scripts.preflight` (rerun) | READY, no warnings, sample 10 and demo step 9 APPROVE | `READY.` with no warnings; "recorded answer for the letter invoice (sample 10): present"; `10 ... APPROVE`; `step 9 Letter, AI assist: APPROVE`; 6 vendor-note rewordings |
| `python -m pytest` (rerun) | 561 passed | `======================= 561 passed in 163.22s (0:02:43) =======================` |

The dry run shows 11 rows, but only 7 calls were made. The five unreadable-file samples (corrupt,
empty, password-protected, not a PDF, truncated) draft the same vendor note, so one recording
serves all five ("already recorded", $0). This matches the "seven calls in all" in
SETUP_WINDOWS.md 6d.

Files recorded (all with claude-haiku-5-5):
`extract__390029c63ed53b89__extract-v2__claude-haiku-5-5.json` and six
`vendor_note__*__vendor-note-v1__claude-haiku-5-5.json`.

## 3. Live AI check (sample 10, CACHED-LIVE, fresh call forced)

This was run headless, not by clicking in the browser. A short script outside the project ran
sample 10 through the real `Pipeline` on a scratch database, with `config.llm_config("cached-live")`
and `LlmAssist.fresh = True`. Those are exactly the two settings that the Guided demo's "AI assist
mode = CACHED-LIVE" and "Ask Claude afresh on every run" switches set (`invoice_desk/ui/common.py`,
`get_llm`). The script then ran the same sample in FIXTURE mode and compared the two.
`INVOICE_DESK_HOME` pointed at a scratch folder, so the fresh answer was cached outside the project.
The SHA-256 of every file in `fixtures/llm/` was identical before and after.

| What was checked | Result |
|---|---|
| A live call happens | Stage 3 "Answer: live call to the Claude API (claude-haiku-5-5, prompt extract-v2)"; mode CACHED-LIVE |
| Values are checked against the page | Invoice number, taxable value, IGST, total and both line items accepted, each with a quote found on the page ("its quote is on the page and states the value; and the invoice arithmetic agrees with it"). Rejected: nothing. No disagreement with the rules reader. |
| Same decision as the recording | Live: **APPROVE**, no reasons. FIXTURE replay: **APPROVE**, no reasons. Same fields, same values. |
| Cost and latency shown | "Tokens and cost: 1883 in, 1633 out; $0.0010 for this run". The extract stage took 11.1 s live, against 27 ms in replay. |

Back to FIXTURE: nothing persistent was changed. The modes were set only inside that one process.
`.env` sets only `ANTHROPIC_API_KEY`, so `LLM_MODE` stays at its default, FIXTURE, and preflight
reports "mode: FIXTURE". In the app, check under Demo settings and admin that AI assist mode says
FIXTURE (it does unless it was changed in that browser session).

## API cost

| Call | Cost (USD) |
|---|---|
| `preflight --llm` key check (18 in, 4 out) | about $0.00001 |
| Recording, 7 calls | $0.0038 |
| Live check, 1 call | $0.0010 |
| **Total** | **about $0.0048 (under half a US cent)** |

## 4. Git

GIT_SECTION_PENDING

## What was changed, and what failed

- **No project code was changed.** All checks passed as documented.
- Files added: `.env` (from `.env.example`, holds the key, git-ignored, not committed),
  `fixtures/llm/*.json` (7 recordings, committed) and this report.
- One command failed and was corrected outside the project: the first run of the scratch
  live-check script could not import `invoice_desk`, because the script lives outside the
  project. It was rerun with `PYTHONPATH` set to the project folder.
- Scratch scripts: none of Antigravity's scripts (`test_cdp.py`, `inspect_dom.py`,
  `run_walkthrough.py`, `full_walkthrough.py`, `test_step1.py`, `test_upload.py`, task logs)
  are inside this project folder. The only `walkthrough_results.json` files are in the parent
  folder (`D:\sem_iitk\sem9\zamp\zamp-invoice-ps1\docs\screenshots\walkthrough\`) and in the old
  repo folder. Neither is in this project, so neither can be committed, and both were left alone.
  The verification scripts used here sit in Claude's temporary scratchpad, outside the project.

## Left for you

- `docs/screenshots/round2_walkthrough/` is committed as you asked, but three of its images look
  like test leftovers: `00_test.png`, `test_setup.png` and `01_step1_clean.png` (the same size as
  `01_step1.png`). Delete them in a follow-up commit if they are not wanted.
- In the app, look at the live call once yourself (SETUP_WINDOWS.md, "Showing a live call"). Here
  it was driven headless with the same settings.
- Deploy (SETUP_WINDOWS.md step 8) and the Dockerfile build (plan B) were not part of this run.
- If the key was ever pasted anywhere other than `.env`, rotate it in the Claude Console.
