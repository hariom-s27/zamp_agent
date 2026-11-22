# Setup on Windows

Copy-paste steps for a Windows 10 or 11 laptop. Steps 1 to 4 get the app running. Steps
5 to 7 are optional. Step 8 is the checklist for demo day.

Every command is typed in **PowerShell**, in the project folder. The commands call the
project's own Python directly (`.venv\Scripts\python`), so nothing has to be
"activated" and PowerShell's script policy never gets in the way.

> **What was and was not checked before this was handed over.** The whole project was
> built and tested on Linux with Python 3.11, 3.12 and 3.13: a clean unzip, a fresh install,
> the full test suite, every sample, and every page in a real Chrome browser. It was
> **not** run on Windows, **not** deployed to a public host, and **never** called a live
> language model, because none of those were available in the build environment. The
> steps below are standard, but do steps 1 to 4 once, today, and not on the morning of
> the interview.

---

## 1. Install Python 3.11

1. Download "Windows installer (64-bit)" for Python 3.11 from <https://www.python.org/downloads/windows/>.
   Python 3.12 and 3.13 also work.
2. Run it. On the first screen tick **"Add python.exe to PATH"**, then "Install Now".
3. Open a new PowerShell window and check:

```powershell
py -3.11 --version
```

It should print `Python 3.11.x`. If `py` is not found, use `python --version` and use
`python` wherever these steps say `py -3.11`.

## 2. Unzip and install

Unzip `zamp-invoice-ps1.zip` somewhere simple, for example `C:\work\zamp-invoice-ps1`.
In File Explorer, open that folder, click the address bar, type `powershell` and press
Enter. Then:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

The install takes a few minutes and about 600 MB. If a fresh install ever behaves
differently from what is described here, install the exact tested set instead:
`.venv\Scripts\python -m pip install -r requirements.lock.txt`.

## 3. Run the tests and the preflight

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m scripts.preflight
```

- `pytest` should end with `358 passed, 6 skipped`. The six skipped tests need the OCR
  engine from step 5; once it is installed the line reads `364 passed`. It takes about
  a minute.
- `preflight` checks the environment, then runs all 14 samples through the real
  pipeline and compares each decision with the documented one. It must end with
  `READY`. A `WARN` about Tesseract is fine before step 5.

## 4. Start the app

```powershell
.venv\Scripts\python -m streamlit run app.py
```

Open <http://localhost:8501> in your browser (the terminal prints the address; the
browser does not open by itself). Stop the app with Ctrl+C in the terminal.

Try it: on **Process invoice**, leave "01 Happy path" selected and press **Process this
invoice**. The nine stages run one after another and the decision appears at the top.
Then open "Run every sample in one go" and press the button, and look at the
**Dashboard** and the **Review queue**.

If port 8501 is taken, add `--server.port 8502` to the command.

---

## 5. Install the OCR engine (recommended)

Without it the two bundled scans still work, from OCR output that was recorded when the
project was built (the screen says "recorded"). With it, scans are read live, and a
scan you upload yourself can be read too.

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

The Tesseract line should now say `ok`, and the sidebar of the app shows the engine's
version. Preflight also runs the two bundled scans through your engine. If your build
reads one of them differently from the build they were made with, the line for that
sample tells you so and what to do: add `OCR_MODE=fixture` to `.env`, which makes the
bundled scans use their recorded OCR output on your machine too. If it is installed somewhere else, create a file named `.env` in the project
folder with one line:

```
TESSERACT_CMD=C:\path\to\tesseract.exe
```

## 6. Add your own API key (optional: turns on LLM assist)

The app runs fully without a key. A key adds one thing: when the rules cannot read a
document (sample 10 is an invoice written as a letter), a language model is asked for
the missing fields, and each value is accepted only if its quote is found on the page.

1. Copy `.env.example` to `.env`.
2. Fill in three lines. Any OpenAI-compatible endpoint works:

```
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-key-here
LLM_MODEL=a-model-name-from-your-provider
```

   For Gemini use `https://generativelanguage.googleapis.com/v1beta/openai/`; for
   OpenRouter use `https://openrouter.ai/api/v1`. Take the model name from your
   provider's own list.
3. Check the key and record a real response for the demo:

```powershell
.venv\Scripts\python -m scripts.preflight --llm
.venv\Scripts\python -m scripts.record_llm_fixtures
```

   The second command sends sample 10 to the model once, shows which values passed the
   quote check, saves the raw response in `fixtures\llm\`, and then replays it with no
   network to confirm sample 10 now ends in APPROVE. Cost: one short call.
4. Restart the app. The sidebar shows `LLM assist  LIVE (your model)`.

**For the live demo, replay the recording instead of calling the model.** Remove the
three lines from `.env` (or set `LLM_MODE=replay`). Sample 10 then gives the same
result every time with no network and no key. That is what the recording is for.

`.env` is in `.gitignore`. Never commit it, and never paste the key into a file that is.

If the model's answer is rejected (the script prints why), the usual reason is a quote
that is not word for word from the page. Use a stronger model and run the script again.

## 7. Host it at a public link (free)

The plan is Streamlit Community Cloud. It needs the code in a GitHub repository.

### 7a. Put the project on GitHub

1. Install Git from <https://git-scm.com/download/win> if `git --version` does not work.
2. On github.com create a new, empty repository, for example `invoice-desk`. Do not add
   a README or a licence there.
3. In PowerShell, in the project folder:

```powershell
git init
git add .
git commit -m "Invoice Desk: PS-1 case study"
git branch -M main
git remote add origin https://github.com/YOUR-USER/invoice-desk.git
git push -u origin main
```

`.gitignore` keeps `.env`, the virtual environment and the local database out of the
repository. Before pushing, run `git status` and make sure `.env` is not listed.

If you recorded a model response in step 6, the new file in `fixtures/llm/` is part of
the commit, which is what lets the hosted app replay it.

### 7b. Deploy

1. Go to <https://share.streamlit.io> and sign in with GitHub. Let it connect to your
   repositories when it asks.
2. In the upper-right corner click **Create app**. When asked "Do you already have an
   app?", choose **Yup, I have an app**.
3. Repository: yours. Branch: `main`. Main file path: `app.py`. You can also pick the
   subdomain, which becomes the public address (`something.streamlit.app`).
4. Click **Advanced settings** and choose Python **3.11** from the "Python version"
   list. The default, 3.12, also passed the full test suite, so nothing breaks if you
   leave it; the Python version cannot be changed later without deleting the app and
   deploying it again.
5. Leave the Secrets box empty and click **Save**. (The hosted app then replays recorded
   model responses and never spends your key. Only if you want live model calls on the
   public link, paste the three `LLM_...` lines there in the form
   `LLM_API_KEY = "..."`; anyone with the link can then trigger calls.)
6. Click **Deploy**. The first build takes several minutes. `packages.txt` installs the
   OCR engine on the host; `requirements.txt` installs the Python packages.

The official walkthrough, with screenshots, is at
<https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy>.

When it is up, open "Run every sample in one go" on the Process page and press the
button. The summary must read **14 of 14 samples gave the documented decision**.

If one of the two scanned samples (02 or 06) differs there, the host's OCR engine is a
different build from the one the samples were made with and reads that scan
differently. Open the app's **Settings**, then **Secrets**, add the line

```
OCR_MODE = "fixture"
```

and save. The app restarts, the two bundled scans use their recorded OCR output (the
screen says "recorded"), and scans that visitors upload are still read live.

### 7c. If the build fails or the host misbehaves

- **Build fails at the "apt" step** (installing `tesseract-ocr`): delete `packages.txt`
  from the repository, push, and the app redeploys without the engine. The bundled
  scans then run from recorded OCR output and say so on screen. Uploaded scans are held
  with the message "scanned page could not be read".
- **"This app has gone to sleep"**: free apps sleep when nobody visits. Open the link a
  few minutes before you need it and press the wake-up button.
- **The hosted app shows old data or odd state**: use "Reset demo data" in the sidebar.
  The database on the host is temporary and is rebuilt from the seeded history.
- **Plan B**: the `Dockerfile` in the project runs the same app on any host that takes
  a container (Render, Railway, Hugging Face Spaces with the Docker SDK). It has not
  been built by me; build it locally once (`docker build -t invoice-desk .`) before
  relying on it.
- **Plan C, always available**: run it on your laptop and share your screen. The live
  interview needs the process to run, not the link.

---

## 8. Demo-day checklist

The day before:

1. `.venv\Scripts\python -m scripts.preflight` ends with `READY`.
2. Rehearse the run order in `DEMO_SCRIPT.md` twice, with a timer.
3. Decide whether sample 10 is part of the demo. If yes, the recording from step 6 must
   exist and `preflight` must show sample 10 as "as documented".

Thirty minutes before:

1. Close everything else. Start the app. Open <http://localhost:8501>.
2. Press **Reset demo data** in the sidebar. Every sample now meets its documented
   starting state.
3. Set "Viewing pause per stage" to about 0.5 seconds, so the stages can be followed.
4. Open the hosted link in a second tab and wake it, as a fallback.
5. Keep `DEMO_SCRIPT.md` open on a second screen or printed.

If something goes wrong live: press **Reset demo data** and run the sample again. Every
sample is deterministic from the reset state.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `py` is not recognised | Use `python` instead, or reinstall Python with "Add python.exe to PATH" ticked |
| `pip install` fails on one package | Run `.venv\Scripts\python -m pip install --upgrade pip` and try again; then try `requirements.lock.txt` |
| Tests fail with "No module named ..." | The install in step 2 did not finish; run it again |
| `preflight` says a sample is "documented: ..." (a mismatch) | Read the line: it shows the decision it got. If `LLM_MODE` or a key is set, sample 10 changes on purpose. Otherwise run `pytest` and read the first failure |
| Samples say "rejected as a duplicate" in the app | They were already processed. Press "Reset demo data" |
| A scan you uploaded is held as "could not be read" | Install Tesseract (step 5) |
| `preflight` fails on "clock" | The laptop's date is before the sample invoices. Fix the date, or add `INVOICE_DESK_TODAY=2026-11-21` to `.env` |
| The page looks stale after editing `rules.yaml` | Reload the page; rules are re-read when the file changes |
| Antivirus blocks `.venv\Scripts\python` | Allow the project folder, or install into a folder outside "Documents" |
