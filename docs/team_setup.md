# Team setup (run MedLens on your own machine)

MedLens runs entirely on your computer. Everyone uses their **own** MySQL database, their **own**
passwords in their own `.env`, their **own** Ollama models and their **own** Qdrant index. Nothing is
shared between teammates except the Git repository.

These steps are the commands the project already uses on the development machine (Windows 11,
PowerShell). They have not been tested on a clean machine; if a step fails for you, tell the team and
fix this file.

## 1. Prerequisites

Versions used during development (newer patch versions should work):

- Git
- Python 3.12 (developed with 3.12.3)
- Node.js 24 with npm 11 (developed with Node 24.22.1, npm 11.19.1)
- MySQL Server 8.0 (developed with 8.0.46 Community)
- Ollama (developed with 0.40.8)
- About 4 GB of free disk space for the two models

The SQL model (`qwen3.5:4b`) answers one question at a time; a GPU makes it much faster.

## 2. Clone

```powershell
git clone https://github.com/ALI-Bin-DO/MEDLENS.git
cd MEDLENS
```

Run every command below from this project root unless a step says otherwise.

## 3. Python environment and backend dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
```

- `requirements-lock.txt` pins the exact versions used in development. `requirements.txt` holds the
  looser direct requirements.
- If PowerShell refuses to run `Activate.ps1`, run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

## 4. Frontend dependencies

```powershell
cd frontend
npm ci
cd ..
```

`npm ci` installs exactly what `frontend/package-lock.json` lists. npm 11 may warn that
`esbuild`'s install script is "not yet covered by allowScripts"; the development install works without
approving it (Vite, the tests and the build all run).

## 5. Local settings (`.env`)

```powershell
Copy-Item .env.example .env
```

Open `.env` and set:

- `MYSQL_USER` / `MYSQL_PASSWORD`: your local MySQL admin account (for example `root`). Only
  `database/load_data.py` uses it.
- `MYSQL_READONLY_PASSWORD`: a password you choose for the read-only account (step 6).

Everything else has working defaults. `.env` is ignored by Git: never commit it and never paste its
contents into chat, issues or pull requests.

## 6. Database: create, load and add the read-only account

> **Fresh-setup only.** `database/schema.sql` **drops and recreates** the five tables, and
> `database/load_data.py` **deletes all rows** before loading the CSV files. Run them only to create
> your own local copy, or to reset it.

The CSV files are already in `Data/raw/` (source and license: `Data/raw/README.md`).

```powershell
$mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"

# 1. Create the medlens database and its tables (drops existing tables).
& $mysql -u root -p -e "source database/schema.sql"

# 2. Load the CSV files (deletes existing rows first). Uses MYSQL_USER/MYSQL_PASSWORD from .env.
python database\load_data.py
```

Expected output: patients 50, doctors 10, appointments 200, treatments 200 and billing 200 rows.

Then create the read-only account the app uses:

1. Make a **local copy** of the script so the placeholder in Git stays unchanged:
   `Copy-Item database\readonly_user.sql database\readonly_user.local.sql`
2. In the copy, replace `CHANGE_ME_READONLY_PASSWORD` (it appears twice) with the same password you put
   in `MYSQL_READONLY_PASSWORD`.
3. Run it, then delete the copy:

```powershell
& $mysql -u root -p -e "source database/readonly_user.local.sql"
Remove-Item database\readonly_user.local.sql
```

The last line of the script shows the grants; it should list only `USAGE` and
`SELECT ON medlens.*`. Do not commit a script that contains a real password.

## 7. Ollama models

Install Ollama from https://ollama.com and make sure it is running, then:

```powershell
ollama pull qwen3.5:4b
ollama pull nomic-embed-text:v1.5
```

Keep `OLLAMA_BASE_URL=http://127.0.0.1:11434` (not `localhost`: on Windows that adds about two seconds
to every request).

## 8. Build the knowledge index

```powershell
python -m backend.scripts.build_index
```

- It embeds the schema, `knowledge_base/data_catalog.md` and the 250 examples in
  `knowledge_base/examples.sql` into local Qdrant storage under `storage/qdrant/` (ignored by Git).
- Expect 268 points: 5 schema, 13 documentation and 250 example chunks.
- Run it again whenever `database/schema.sql` or `knowledge_base/` changes.
- **Stop the backend first.** Qdrant local storage allows only one process at a time, so the build
  fails while the API is running.

## 9. Start the app

Terminal 1, the backend (activate `.venv` first):

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

- Keep `--workers 1`: the local Qdrant store is locked by one process, and the app answers one
  question at a time. More workers fail to open the index.
- Do not change `--host 127.0.0.1`: the API has no authentication.

Terminal 2, the frontend:

```powershell
cd frontend
npm run dev
```

Open http://127.0.0.1:5173. Vite forwards `/chat`, `/export`, `/health`, `/catalog` and `/admin` to
the backend, so no CORS setting is needed.

**Login is a demo screen.** "Sign in" and "Continue with SSO" open the app without checking anything,
and the administration page has no access control. There is no real authentication. Keep both
servers on `127.0.0.1`.

## 10. Smoke check

With the backend running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health                   # status = ok (process only)
Invoke-RestMethod http://127.0.0.1:8000/admin/system | ConvertTo-Json -Depth 5   # MySQL, Ollama and index status
```

Then, in the browser, ask: `How many appointments are there for each status?`. You should get a
table, a chart and the SQL. The first question is slower while Ollama loads the model.

## 11. Tests

These need no MySQL, Ollama or running servers. The backend tests do need a `.env` (step 5): some read
the model names from it, and a copy of `.env.example` is enough.

```powershell
# Backend (from the project root, .venv active)
python -m unittest discover -s tests

# Frontend
cd frontend
npm test
npm run typecheck
npm run build
```

The model evaluations in `evaluation/` (see `evaluation/README.md`) need MySQL and Ollama, and take
several minutes. Reports go to `evaluation/results/`, where Git keeps only the final report of each
milestone.

## 12. Working together

```powershell
git checkout main
git pull
git checkout -b feature/short-description

# ...make changes, run the tests...

git add <the files you changed>
git commit -m "Describe the change"
git push -u origin feature/short-description
```

Then open a pull request into `main` on GitHub, ask a teammate to review it, and merge it once it is
approved. Before every commit, check `git status` and `git diff --staged` for `.env` files, passwords
or local data.

Never commit:

- `.env`
- a read-only script with a real password
- `storage/` contents (Qdrant index, monitoring SQLite)
- `.venv/`, `node_modules/`, `frontend/dist/`
- model files
