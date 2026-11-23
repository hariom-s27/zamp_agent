# Setup on Windows

Copy-paste steps for a Windows 10 or 11 laptop. Steps 1 to 4 get the app running. Step 5
adds the OCR engine. **Step 6 is the one thing only you can do: record Claude's answers
with your key.** Step 7 is optional. Step 8 puts it on a public link. Step 9 is the
checklist for demo day.

Every command is typed in **PowerShell**, in the project folder. The commands call the
project's own Python directly (`.venv\Scripts\python`), so nothing has to be
"activated" and PowerShell's script policy never gets in the way.

> **What was and was not checked before this was handed over.** This round was built and
> tested on Linux: a clean unzip, a fresh install, the full test suite on Python 3.11,
> 3.12 and 3.13, every sample, the guided demo, and every page in a real Chrome browser.
> Round 1 was verified on Windows 11 by the audit (docs/AUDIT_REPORT.md), and nothing
> since is specific to an operating system, but **this round's code has not been run on
> Windows**. It has **not** been deployed, and it has **never** called the real Claude
> API, because the build environment cannot reach it. Do steps 1 to 6 once, today, and
> not on the morning of the interview.

---

## 1. A Python 3.11 for the project

Python 3.11, 3.12 or 3.13. The audit found that on this laptop `py -3.11` is broken and
plain `python` is 3.14, so check first:

```powershell
py -3.11 --version
```

- **It prints `Python 3.11.x`:** good, go to step 2.
- **It fails, and `uv` is installed** (it is on the laptop the audit used): let uv tell
  you where its Python 3.11 is, and use that path in step 2.

  ```powershell
  uv python find 3.11
  ```

- **Neither works:** install Python 3.12 from
  <https://www.python.org/downloads/windows/> ("Windows installer (64-bit)"), then use
  `py -3.12` wherever these steps say `py -3.11`.

Do not use a bare `python` unless `python --version` prints 3.11, 3.12 or 3.13. The
package versions are pinned for those.

## 2. Unzip and install

Unzip `zamp-invoice-ps1.zip` somewhere simple, for example `C:\work\zamp-invoice-ps1`.
In File Explorer, open that folder, click the address bar, type `powershell` and press
Enter. Then:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

If you are using uv's Python from step 1, the first line is instead:

```powershell
& (uv python find 3.11) -m venv .venv
```

The install takes a few minutes (about ten on a slow connection) and about 700 MB. If a
fresh install ever behaves differently from what is described here, install the exact
tested set instead:
`.venv\Scripts\python -m pip install -r requirements.lock.txt`.

## 3. Run the tests and the preflight

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m scripts.preflight
```

- `pytest` should end with `553 passed, 8 skipped`. The eight skipped tests need the OCR
  engine from step 5; once it is installed the line reads `561 passed`. It takes two to
  three minutes.
- `preflight` checks the machine, runs all 18 samples through the real pipeline,
  rehearses the eleven steps of the guided demo, and opens every page without a
  browser. It must end with `READY`. Two `WARN` lines are expected at this point:
  Tesseract (until step 5) and "recorded answer for the letter invoice: none yet"
  (until step 6).

## 4. Start the app

```powershell
.venv\Scripts\python -m streamlit run app.py
```

Open <http://localhost:8501> in your browser (the terminal prints the address; the
browser does not open by itself). Stop the app with Ctrl+C in the terminal.

Try it. The app opens on **Guided demo**:

1. Press **Run step 1**. The nine stages run one after another and the decision appears
   with what to notice about it.
2. Press **Run all remaining**. All eleven scenarios run, each compared with its
   expected decision.
3. Open **Review queue**. Try to approve the first invoice (the split PO): it is refused
   with the numbers. Then look at the **Dashboard**.
4. Back on Guided demo, **Start a fresh demo** puts the books back.

If port 8501 is taken, add `--server.port 8502` to the command.

---

## 5. Install the OCR engine (recommended)

Without it the bundled scans still work, from OCR output that was recorded when the
project was built (the screen says "recorded"). With it, scans are read live, and a
scan or photograph you upload yourself can be read too.

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

If `winget` is not available, download the installer from
<https://github.com/UB-Mannheim/tesseract/wiki> and keep the default folder
(`C:\Program Files\Tesseract-OCR`). The app looks there on its own; nothing needs to be
added to PATH. Restart the app, then confirm:

```powershell
.venv\Scripts\python -m scripts.preflight
```

The Tesseract line should now say `ok`. Preflight also runs the two bundled scans
through your engine. The audit's Tesseract 5.4.0 read both exactly like the 5.3.4 they
were made with. If your build reads one differently, the line for that sample says so
and what to do: add `OCR_MODE=fixture` to `.env`, which makes the bundled scans use
their recorded OCR output on your machine too.

If Tesseract is installed somewhere else, create a file named `.env` in the project
folder with one line:

```
TESSERACT_CMD=C:\path\to\tesseract.exe
```

## 6. Your Claude key: record the AI answers (do this once)

The app runs fully without a key. What the key adds is the AI part of the demo: sample
10 is an invoice written as a letter, the rules cannot read it, and Claude is asked for
the missing fields. Code then checks every value against the page before it is used.

The demo does not call Claude live. It **replays answers you record once**, so it gives
the same result every time with no network. Recording is this step. It costs well under
one US cent with the default model.

**6a. Put the key in `.env`.** Copy `.env.example` to `.env` and fill in one line:

```
ANTHROPIC_API_KEY=sk-ant-...your key...
```

The key comes from the Claude Console at <https://platform.claude.com/> (Settings, API
keys). `.env` is in `.gitignore`. Never commit it, and never paste the key into any
other file. A key in `.env` changes nothing by itself: the app stays in FIXTURE mode
until you choose otherwise.

**6b. See what would be sent, with no network:**

```powershell
.venv\Scripts\python -m scripts.record_llm_fixtures --dry-run
```

It lists the samples, the size of each request and the most each call could cost.

**6c. Check that the key works** (one tiny call):

```powershell
.venv\Scripts\python -m scripts.preflight --llm
```

**6d. Record:**

```powershell
.venv\Scripts\python -m scripts.record_llm_fixtures
```

It runs every sample through the real pipeline and records each answer the demo will
ask for: one reading of the letter invoice, and a rewording of each drafted message to a
vendor (seven calls in all). For each it prints what was accepted. The last lines must
show sample 10 as `APPROVE`. The script exits with an error if a recording does not
give the documented result, so a bad recording is noticed here and not in front of an
audience. Running it again costs nothing: what is recorded is not asked for twice.

**6e. Confirm:**

```powershell
.venv\Scripts\python -m scripts.preflight
.venv\Scripts\python -m pytest
```

Preflight now shows "recorded answer for the letter invoice: present", sample 10 as
APPROVE, step 9 of the demo as APPROVE, and no warning about it. The tests still pass.
The new files are in `fixtures\llm\`. They are part of the project: commit them in step
8 so the hosted app can replay them. They contain the model's answers and token counts,
not your key.

**If sample 10 does not come out as APPROVE**, the script prints which values failed
which check. The usual reason is a quote that is not word for word from the page.
Two things to try, in this order. First, more effort from the same model: add
`LLM_EFFORT=high` to `.env` and run
`.venv\Scripts\python -m scripts.record_llm_fixtures --force --only 10_ai_assist_letter_invoice`.
Second, the stronger model:

```powershell
.venv\Scripts\python -m scripts.record_llm_fixtures --force --model claude-sonnet-5-5 --only 10_ai_assist_letter_invoice
```

If that one gives APPROVE, delete the first model's file for the letter
(`fixtures\llm\extract__...__claude-haiku-5-5.json`) and run preflight again. Nothing
else needs changing: in FIXTURE mode a recording is replayed whichever model is
configured, and the record names the model that made it. If it still fails, delete both
files and leave it: without a recording, sample 10 is held by the rules alone, the
screen says why, and that is a fair thing to show. Keep what the script printed; that
output is exactly what is needed to improve the prompt.

**Showing a live call in the interview** (optional; DEMO_SCRIPT.md says when). On the
Guided demo page open **Demo settings and admin**, set **AI assist mode** to
CACHED-LIVE and switch on **Ask Claude afresh on every run**. Press **Start a fresh
demo**, then run step 9. Stage 3 now shows "live call to the Claude API", the tokens and
the cost. If the call fails for any reason, the recorded answer is used and the record
says so, so this cannot break the demo. Switch back to FIXTURE afterwards.

## 7. Optional: the independent test set on your machine

```powershell
.venv\Scripts\python -m scripts.evaluate_robustness                # recorded OCR: the same numbers everywhere
.venv\Scripts\python -m scripts.evaluate_robustness --ocr live     # read the 12 photographs with your Tesseract
```

The last line of the first must read `30 approved-correct, 15 held-correct, 4
misread-held (0 false alarms), 0 misread-approved`: 45 of 49 right. The second may
differ by a photograph or two with another Tesseract build; the number that must stay 0
is misread-approved, and the script exits with an error if it does not. Neither command
changes any file unless you add `--write`.

## 8. Host it at a public link (free)

The plan is Streamlit Community Cloud. It needs the code in a GitHub repository.

### 8a. Put the project on GitHub

1. Install Git from <https://git-scm.com/download/win> if `git --version` does not work.
2. On github.com create a new, empty repository, for example `invoice-desk`. Do not add
   a README or a licence there.
3. In PowerShell, in the project folder:

```powershell
git init
git add .
git status
git commit -m "Invoice Desk: PS-1 case study"
git branch -M main
git remote add origin https://github.com/YOUR-USER/invoice-desk.git
git push -u origin main
```

Read what `git status` prints before you commit. `.env` must **not** be in the list
(`.gitignore` keeps it out, along with the virtual environment and the local database).
`fixtures/llm/*.json` from step 6 **should** be in the list.

### 8b. Deploy

1. Go to <https://share.streamlit.io> and sign in with GitHub. Let it connect to your
   repositories when it asks.
2. In the upper-right corner click **Create app**. When asked "Do you already have an
   app?", choose **Yup, I have an app**.
3. Repository: yours. Branch: `main`. Main file path: `app.py`. In **App URL** pick the
   subdomain, which becomes the public address (`something.streamlit.app`).
4. Click **Advanced settings**. Choose Python **3.11** from the "Python version" list
   (the default, 3.12, also passed the full test suite).
5. In the **Secrets** box paste the contents of `.streamlit\secrets.toml.example`. As
   shipped it holds four lines and no key:

```
LLM_MODE = "fixture"
INVOICE_DESK_SANDBOX = "session"
INVOICE_DESK_TODAY = "2026-11-21"
OCR_MODE = "fixture"
```

   What they do: the hosted app replays your recordings and never spends your key;
   every visitor gets private books, so nobody can reset or fill them during your demo;
   the business date is pinned to the date the samples were written for; the bundled
   scans use their recorded OCR output, so the host's Tesseract build cannot change a
   demo result (scans that visitors upload are still read live).
6. Click **Save**, then **Deploy**. The first build takes several minutes.
   `packages.txt` installs the OCR engine on the host; `requirements.txt` installs the
   Python packages.

The official walkthrough, with screenshots, is at
<https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy>.

When it is up: on Guided demo press **Run all remaining**. All eleven steps must end "as
expected", and step 9 must be APPROVE if you committed the recordings.

### 8c. Optional: live Claude on the public link

Only if you want visitors to be able to trigger real calls. The template has four
commented lines for it: your key, `LLM_ALLOW_LIVE = "1"`, and a small daily and
per-invoice cap. Without `LLM_ALLOW_LIVE` a key in Secrets does nothing on a public
demo. Also set a monthly spend limit for the key's workspace in the Claude Console: the
daily cap is kept in a file on the host, and a free host can lose that file when it
restarts.

My advice is to leave it off. The recorded answer shows the same thing, and the live
call is better shown from your laptop.

### 8d. If the build fails or the host misbehaves

- **Build fails at the "apt" step** (installing `tesseract-ocr`): delete `packages.txt`
  from the repository, push, and the app redeploys without the engine. The bundled
  scans still run from recorded OCR output and say so on screen. Uploaded scans are
  held with the message "scanned page could not be read".
- **"This app has gone to sleep"**: free apps sleep when nobody visits. Open the link a
  few minutes before you need it and press the wake-up button.
- **The hosted app shows odd state**: press **Start a fresh demo**. The database on the
  host is temporary and is rebuilt from the seeded history.
- **Plan B**: the `Dockerfile` runs the same app on any host that takes a container
  (Render, Railway, Hugging Face Spaces with the Docker SDK). **I could not build it**:
  the build machine could not pull the base image. Build it once before relying on it:
  `docker build -t invoice-desk .` then `docker run --rm -p 8501:8501 invoice-desk`.
- **Plan C, always available**: run it on your laptop and share your screen. The live
  interview needs the process to run, not the link.

---

## 9. Demo-day checklist

The day before:

1. `.venv\Scripts\python -m scripts.preflight` ends with `READY` and no warning.
2. Rehearse the run order in `DEMO_SCRIPT.md` twice, with a timer.
3. Decide whether you will show a live Claude call. If yes, rehearse it once that day
   (step 6, last paragraph) and look at the cost line.

Thirty minutes before:

1. Close everything else. Start the app. Open <http://localhost:8501>.
2. Open **Demo settings and admin** at the bottom of Guided demo. AI assist mode must
   say FIXTURE. The demo date switch must be on.
3. Press **Start a fresh demo**. Set **Pace of the live view** to Presentation.
4. Open the hosted link in a second tab and wake it, as a fallback.
5. Keep `DEMO_SCRIPT.md` open on a second screen or printed.

If something goes wrong live: press **Start a fresh demo** and run the step again. Every
scenario is deterministic from that state.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `py -3.11` fails with "Unable to create process" | The launcher entry is broken (the audit hit this). Use `& (uv python find 3.11) -m venv .venv`, or install Python 3.12 and use `py -3.12` |
| `pip install` fails on one package | Run `.venv\Scripts\python -m pip install --upgrade pip` and try again; then try `requirements.lock.txt`. If the venv was made with Python 3.14, remake it with 3.11 to 3.13 |
| Tests fail with "No module named ..." | The install in step 2 did not finish; run it again |
| `pytest -q` shows only dots | Fixed in this round; if you still see it, run `.venv\Scripts\python -m pytest` without `-q` |
| `preflight` says a sample is not as documented | Read the line: it shows the decision it got and the one expected. Then run `pytest` and read the first failure |
| The guided demo says the books hold other records | Something was processed outside the demo. Press **Start a fresh demo** |
| A sample says "already received as AP-2026-..." | It was processed earlier. That is the re-submission control. **Start a fresh demo** gives a clean state |
| Step 9 of the demo is HOLD, not APPROVE | No recorded Claude answer yet (step 6), or AI assist mode is OFF |
| `record_llm_fixtures` says the key is missing | `.env` must be in the project folder and contain `ANTHROPIC_API_KEY=...` with no quotes |
| `preflight --llm` reports an authentication error | The key is wrong or revoked. Make a new one in the Claude Console |
| A call is "skipped: over the cap" | Raise `LLM_MAX_COST_PER_INVOICE_USD` or `LLM_DAILY_CAP_USD` in `.env`, or wait until tomorrow |
| A scan you uploaded is held as "could not be read" | Install Tesseract (step 5) |
| Your own real invoice is held | Correct: its vendor is not on the made-up vendor list and its PO is not in the register. Stage 3 shows what was read from it |
| The page looks stale after editing `rules.yaml` | Reload the page; rules are re-read when the file changes |
| Antivirus blocks `.venv\Scripts\python` | Allow the project folder, or install into a folder outside "Documents" |
