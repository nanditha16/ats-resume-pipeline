# Setting Up the ATS Pipeline — Step by Step

This guide gets you from zero to a running web app with no prior technical knowledge required.
Estimated time: **15–30 minutes**, mostly waiting for downloads.

---

## What you need before starting

| What | Why | Mac | Windows |
|------|-----|-----|---------|
| **Git** | To download the source code | Usually pre-installed | Download from git-scm.com |
| **Docker Desktop** | Runs the pipeline in a safe container | docker.com/products/docker-desktop | docker.com/products/docker-desktop |
| **A terminal** | To run a few commands | Built-in (Terminal app) | Windows Terminal or Git Bash |

---

## Step 1 — Install Git

**Mac:** Open Terminal and run:
```
git --version
```
If you see a version number, Git is already installed. If not:
```
xcode-select --install
```
Click Install when the popup appears and wait.

**Windows:** Download and install from https://git-scm.com/download/win — use all defaults.

---

## Step 2 — Install Docker Desktop

1. Go to: https://www.docker.com/products/docker-desktop
2. Click **Download Docker Desktop** for your OS
3. Open the installer and follow the prompts
4. Open Docker Desktop from Applications (Mac) or Start menu (Windows)
5. Wait for the whale icon to show "Docker Desktop is running"

> ⚠️ Docker Desktop must be **open and running** every time you use this app.

**Mac note:** macOS uses port 5000 for AirPlay. This app uses port **5001** — no action needed.

---

## Step 3 — Download the source code

```bash
cd <YOUR_CHOSEN_FOLDER>
git clone https://github.com/nanditha16/ats-resume-pipeline.git
cd ats-resume-pipeline
./scripts/init_dirs.sh
```

The last line creates the `input/`, `output/`, `prompts/` and `chunks/` folders (they are not in the download because they hold
your private data). The setup scripts also do this, so running it yourself is optional.

---

## Step 3b — Configure your local paths

```bash
cp .env.example .env
```

Open `.env` in any text editor:

**Mac/Linux:**
```
GCLOUD_CONFIG_DIR=~/.config/gcloud
MARKITDOWN_CODESPACE_DIR=~/.markitdown-codespace
```

**Windows (Git Bash):**
```
GCLOUD_CONFIG_DIR=C:/Users/YOURNAME/.config/gcloud
MARKITDOWN_CODESPACE_DIR=C:/Users/YOURNAME/.markitdown-codespace
```

> ⚠️ Windows: use forward slashes (`/`), not backslashes (`\`).
> ℹ️ `.env` is in `.gitignore` — never committed to GitHub.

---

## Step 4 — Run the one-time setup

**Mac/Linux:**
```bash
chmod +x scripts/*.sh
./scripts/setup.sh
```

**Windows (Git Bash):**
```bash
./scripts/setup.sh
```

This checks Docker, downloads the pipeline environment (5–10 minutes first time), and sets up all tools. Wait for:
```
✅ Setup complete
```

---

## Step 5 — Start the web app

```bash
./scripts/serve.sh
```

Your browser opens automatically to **http://localhost:5001**. You should see the ATS Pipeline interface with green system check icons.

---

## Step 6 — Add your resume files (first time only)

1. Copy your resume PDF files into `input/pdf/`
2. In the web app, click **"Run pipeline (run.sh)"** on the Setup page
3. Wait 1–2 minutes. Resumes are converted and placed in `output/resume/` automatically.

> ⚠️ Name resume PDFs with no spaces (use `_` or `-` instead) — e.g.
> `Firstname_Lastname_Resume_Company.pdf`, not `Firstname Lastname Resume.pdf`.
> Spaces in the filename break Stage 3/4 prompt generation downstream.

> ℹ️ Claude.ai is the default, but generated prompt files work with any LLM.

---

## Step 6b — Build the variant bank (first time only, after Step 6)

> **What this is:** Stage 0/1 (variant ranking) uses three files uploaded together
> to Claude.ai. Two are static and built once here. Only the JD file changes per application.

**Two static files built in this step:**
- `prompts/variant_bank.txt` — all resume variants packaged together
- `prompts/variant_rank_prompt.txt` — evaluation instructions

**One file that already exists from Step 6:**
- `output/JDx.md` — the converted JD, produced by the pipeline

### Using the web UI:

On the Setup page, the **"Build variant bank"** card shows three states:

- 🔴 **Bank missing** — click **"Build Variant Bank"**
- 🟡 **Bank stale** — a resume is newer than the bank; click **"Rebuild Variant Bank"**
- 🟢 **Bank ready** — both static files exist and are current

### Using the command line:

```bash
./scripts/build_variant_bank.sh
```

Expected output:
```
📦 Found 21 resume variant(s) in output/resume
✅ Variant bank saved to prompts/variant_bank.txt
✅ Instructions file saved to prompts/variant_rank_prompt.txt
```

> ✅ **You only do this once.** Rebuild only when you add or edit resumes.

---

## Step 7 — Add your career evidence files (first time only)

Copy evidence files (Career_Wealth.xlsx, project notes, PDFs) into:
```
ats-resume-pipeline/input/evidence/
```

Click **"Refresh evidence"** on the Setup page. Re-run whenever you add new evidence files.

---

## Every time you use the app

1. Open Docker Desktop and wait for "running"
2. Open Terminal
3. Run:
   ```bash
   cd <YOUR_CHOSEN_FOLDER>/ats-resume-pipeline
   ./scripts/serve.sh
   ```
4. Go to http://localhost:5001
5. Press Ctrl+C when done
6. Optionally: `docker compose down`

---

## How to evaluate a job description (the normal workflow)

### Step A — Add the job description

In the web UI: click **"Add JD"** and paste the JD text, or drag a `.txt` file.

> ℹ️ After adding a JD, click **"Convert JDs → output/JDx.md"** (available on
> both Step 1 and Step 2) instead of going back to Step 0 — it runs the same
> pipeline conversion in place.

### Step B — Check readiness (Step 2 in the UI)

Each JD card in Step 2 shows three numbered download buttons when all files are ready:

1. **① variant_bank.txt** — static, reuse across all JDs
2. **② variant_rank_prompt.txt** — static, reuse across all JDs
3. **③ JDx.md** — unique per JD

If a card shows "Run pipeline first" or "Build variant bank first", the status text tells you exactly what's missing.

> No file generation happens in Step 2. All three files exist after Step 0 runs.

### Step C — Upload all three files to Claude.ai

In Claude.ai, start a new conversation and attach all three files at once (multi-select in the file picker). Send — Claude reads all three and evaluates the fit.

> ⚠️ **Use file upload, not copy/paste.** Clipboard corrupts em-dashes in resume content.

> ℹ️ Files ① and ② are reusable for every JD. Only re-download ③ for each new job.

### Step D — Paste Claude's response

Click **"Paste Claude response"** on the JD card. The verdict (GOOD FIT / PARTIAL FIT / POOR FIT) appears as a coloured banner when you view the saved response.

### Step E — Generate Stages 2–4

Click **"Generate Stages 2–4"** in the web UI. Upload each prompt file to Claude.ai individually and paste responses back.

---

## When to rebuild the variant bank

Rebuild when:
- You added a new resume PDF and ran the pipeline
- You edited an existing `.md` file in `output/resume/`
- The web UI shows 🟡 **"Bank stale"**

```bash
./scripts/build_variant_bank.sh --rebuild
```

You do **not** need to rebuild between job applications.

---

## Troubleshooting

**"Docker is not running"** → Open Docker Desktop and wait for it to fully start.

**"port 5001 already in use"**
- Mac/Linux: `lsof -i :5001`
- Windows: `netstat -ano | findstr :5001`

**"Build Variant Bank shows 0 variants"**
→ Run the pipeline first (Step 6). The bank is built from `output/resume/` which is empty until PDFs are converted.

**"Bank shows stale after every rebuild"**
→ Fix mtime: `touch prompts/variant_bank.txt`

**"Claude says it doesn't see a job description"**
→ You uploaded fewer than three files. All three must be attached before sending.

**Step 2 shows "Run pipeline first (output/JDx.md missing)"**
→ Click **"Convert JDs → output/JDx.md"** on Step 1 or 2 — no need to go back to Step 0.

**Stage 2 API returns "Reauthentication is needed"** (Vertex AI)
→ On your local terminal: `gcloud auth application-default login`, then restart the server.

**Stage 2 API returns "invalid x-api-key"** (Claude API)
```bash
printf 'sk-ant-api03-YOUR_KEY_HERE' > ~/.markitdown-codespace/claude_api_key
chmod 600 ~/.markitdown-codespace/claude_api_key
wc -c ~/.markitdown-codespace/claude_api_key   # should be ~108 chars
```

**First-time setup takes more than 15 minutes**
→ Normal on slow internet — downloads ~2GB of tools. Leave it running.

---

## What each folder does

```
ats-resume-pipeline/
├── input/
│   ├── pdf/        ← Put your resume PDFs here
│   ├── evidence/   ← Put Career_Wealth.xlsx and project notes here
│   └── other/      ← JD text files (the app manages these)
├── output/
│   ├── resume/     ← Converted resume files (auto-generated)
│   └── JDx.md      ← Converted JD files (used directly by Claude.ai upload)
└── prompts/
    ├── variant_bank.txt          ← Resume bank — built once, reuse per job
    ├── variant_rank_prompt.txt   ← Evaluation instructions — built once, reuse per job
    └── JD_Analysis/              ← Claude responses, per-JD folders
```

You only ever need to touch `input/pdf/` and `input/evidence/`.

---

## System requirements

| | Minimum | Recommended |
|--|---------|-------------|
| **RAM** | 8 GB | 16 GB |
| **Storage** | 10 GB free | 20 GB free |
| **OS** | macOS 12+ or Windows 10/11 | macOS 13+ |
| **Internet** | Required for first setup and Claude.ai | — |

---

## Getting help

Share the error message from Terminal with whoever gave you access. A screenshot of the terminal window is the easiest way.
