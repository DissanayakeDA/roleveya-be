# Roleveya API (roleveya-be)

FastAPI backend for the Roleveya job tracker: auth (Cognito), jobs, application history, CVs and job
posters (Cloudinary), preferences, the dashboard summary, and the CV studio (LaTeX CVs tailored per job,
with AI suggestions from Groq). Data lives in Neon Postgres.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env      # then fill in the values (see below)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Tables are created on first start. API docs are at http://127.0.0.1:8000/api/docs while `APP_ENV` isn't
`production`.

Run the tests (SQLite in memory, with fake Cognito and Cloudinary, so no credentials needed):

```powershell
pytest
```

## Configuration

All settings come from `.env`; `.env.example` lists every key.

### Neon

Paste the connection string exactly as Neon shows it into `DATABASE_URL`
(`postgresql://user:password@host/db?sslmode=require`). The app switches it to the psycopg 3 driver itself.
Connections are pinged before use because Neon suspends idle compute.

### Cognito

The browser never talks to Cognito; the API does, and keeps the tokens in HttpOnly cookies. Set up the
user pool like this:

1. **Sign-in identifier:** Email.
2. **Required attributes:** `email`, `phone_number`, `name`. The sign-up form sends all three; phone numbers
   are E.164 (`+94771234567`).
3. **MFA:** none. **Self sign-up:** on. **Verification:** Cognito sends a code by email to verify the address.
4. **App client:** enable `ALLOW_USER_PASSWORD_AUTH` and `ALLOW_REFRESH_TOKEN_AUTH`. A client secret is
   optional; if the client has one, put it in `COGNITO_CLIENT_SECRET`. Keep token revocation enabled so
   sign-out revokes the refresh token.
5. Put the pool id and client id in `.env`. The region is read from the pool id.

Cognito's built-in email sender is capped at a small daily quota; switch the pool to Amazon SES before
real users sign up.

### Cloudinary

Fill in the cloud name, API key and API secret. CVs and posters are uploaded as `authenticated` assets, so
they have no public URL. The API fetches them with a signed URL and streams the bytes to the browser.
If viewing a PDF fails, check **Settings > Security > Allow delivery of PDF and ZIP files** in the
Cloudinary console.

### Reading job posts (screenshots and pasted text)

The Add a job page can fill itself from a LinkedIn screenshot or copied post text. There's nothing to
configure, and no AI service or API key is involved:

- **Screenshots** are read on this server by [RapidOCR](https://github.com/RapidAI/RapidOCR), a free
  OCR library that runs on the CPU. Images never leave the server, and they aren't stored unless the user
  keeps the screenshot as the job's poster. Reading one takes a few seconds.
- **Finding the details** (company, title, description, location, work arrangement, employment type, link)
  is done with rules that match LinkedIn's job pages and feed posts, in `app/services/job_extract.py`.
  Fields that can't be found are left empty, and the form marks what it filled in so the user checks it.

The OCR models (about 30 MB) are downloaded the first time a screenshot is read, so that first one takes
longer. On a server, download them ahead of time, for example while building the image:

```powershell
python -m app.services.ocr
```

RapidOCR installs the headless build of OpenCV, which doesn't need any desktop libraries on a server.

### CV studio (LaTeX CVs, tailoring, saved versions)

The CV studio keeps master CVs as LaTeX, makes a working copy for each job, and saves the result as a
version that can't be changed afterwards (its PDF is also a normal CV, so it can be picked as the CV sent).
It needs three things on the server:

| Feature | Needs | Cost |
|---|---|---|
| PDF preview and saved versions | [Tectonic](https://tectonic-typesetting.github.io) (a single-file LaTeX engine) | Free |
| Word (.docx) downloads | pandoc, bundled by the `pypandoc_binary` package in `requirements.txt` | Free |
| AI suggestions and PDF-to-LaTeX | A Groq API key in `GROQ_API_KEY` | Free plan, rate-limited |

Anything missing is switched off and the UI says so; the rest keeps working.

**Install Tectonic on Windows** (it isn't a Python package). In PowerShell, from `roleveya-be`:

```powershell
New-Item -ItemType Directory -Force .tools | Out-Null
Invoke-WebRequest -Uri "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip" -OutFile .tools\tectonic.zip
Expand-Archive .tools\tectonic.zip -DestinationPath .tools -Force
Remove-Item .tools\tectonic.zip
.\.tools\tectonic.exe --version
```

The API finds `.tools\tectonic.exe` by itself (or `tectonic` on PATH, or the path in `TECTONIC_PATH`). On
Linux, download the matching build from the same release page. The first compile of a template downloads
the LaTeX packages it uses (cached afterwards in your user profile), so it can take a minute; later ones
take a second or two.

**Templates with their own class file.** Some popular templates ship a `.cls` file that isn't part of
LaTeX, and users can't upload one. The most common, the `resume` class from Overleaf's "Medium Length
Professional CV" (`\documentclass{resume}` with `\name`, `\address`, `rSection` and `rSubsection`), has a
stand-in written for this app in `app/services/tex/resume.cls`, which is copied next to every compile.
Word downloads spell its commands out for pandoc. To support another class, add its `.cls` there and its
name to `ALLOWED_CLASSES` in `app/services/latex_guard.py`.

**Groq:** create a key at https://console.groq.com/keys and put it in `GROQ_API_KEY`. The default model,
`openai/gpt-oss-120b`, supports strict JSON output, which both AI tasks rely on. Only the job post and the
CV's text are sent. Groq's free plan limits tokens per minute and per day
([your limits](https://console.groq.com/settings/limits)); a comparison uses roughly 4,000 to 7,000 tokens, so
results are stored and the same CV and post are never sent twice unless the user asks.

What the AI does, and doesn't:

- **Compare a CV with a job post:** skills the CV shows, skills it's missing (required or nice to have), up to
  eight edits quoting exact LaTeX to replace, and keywords. Edits only reword what's there; anything that
  adds a skill is marked "Only if it's true". Nothing changes until the user applies an edit.
- **PDF to LaTeX:** the PDF's text is read on the server (pypdfium2, or the local OCR for scanned PDFs),
  the AI sorts it into sections without rewording, and the server lays it out in the chosen template. The
  layout is the template's, not the original PDF's.

**Compiling LaTeX from users safely.** LaTeX can read files (`\input{/etc/passwd}`) and embed them in
the PDF, so every document passes `app/services/latex_guard.py` before compiling: only common CV packages
and classes are allowed, commands that read or write files or build command names from text are refused,
and file paths are rejected. Tectonic then runs with shell escape off (`--untrusted`), in a temporary folder,
without the API's environment variables, with a time limit and at most two compiles at a time. pandoc runs
with `--sandbox`. For a public deployment with many users, also run the API (or just the compiler) in a
container that holds no secrets, since a deny-list check can't promise to catch everything LaTeX can do.

## How it fits together

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/signup`, `/confirm`, `/resend-code`, `/login`, `/logout`, `/forgot-password`, `/reset-password`; `GET /api/auth/session` |
| Jobs | `GET/POST /api/jobs`, `GET/PATCH/DELETE /api/jobs/{id}`, `PATCH /api/jobs/{id}/status` |
| Reading posts | `POST /api/jobs/extract/screenshot` (image upload), `POST /api/jobs/extract/text` |
| History | `GET/POST /api/jobs/{id}/events`, `DELETE /api/jobs/{id}/events/{event_id}` (notes only), `GET /api/history` |
| Posters | `POST/GET/DELETE /api/jobs/{id}/poster` |
| CVs | `GET/POST /api/cvs`, `PATCH/DELETE /api/cvs/{id}`, `GET /api/cvs/{id}/file` |
| CV studio | `GET /api/studio/status`, `GET/POST /api/cv-documents`, `POST /api/cv-documents/convert` (PDF to LaTeX), `GET/PATCH/DELETE /api/cv-documents/{id}`, `POST /api/cv-documents/{id}/versions` |
| Compiling | `POST /api/latex/compile` (PDF as base64; errors are a 200 with `ok: false`), `POST /api/latex/docx` |
| Tailoring | `GET /api/jobs/{id}/tailoring`, `POST /api/jobs/{id}/tailoring/draft`, `/analysis`, `/versions` |
| Saved versions | `GET /api/cv-versions`, `GET /api/cv-versions/{id}`, `GET /api/cv-versions/{id}/docx` (no edit endpoint; deleting the CV deletes the version) |
| Preferences | `GET/PUT /api/preferences` |
| Dashboard | `GET /api/dashboard/summary` |

Every stage change goes through `PATCH /jobs/{id}/status` and writes an `application_events` row, which is
what the job timeline, the History page and the dashboard's recent activity read from.

## Security notes

- **Tokens never reach JavaScript.** Login sets `rv_at` (access) and `rv_rt` (refresh) as `HttpOnly`,
  `SameSite=Strict` cookies scoped to `/api`. When the access token expires, the API refreshes it
  server-side on the next request and rotates the cookie, so the browser never handles a 401-and-retry.
- **CSRF:** besides `SameSite=Strict`, every POST/PUT/PATCH/DELETE must carry `X-Requested-With: roleveya`
  (the frontend adds it). Cross-site pages can't add that header.
- **Ownership:** every query is scoped to the signed-in user; another user's ids return 404.
- **Uploads:** the file type is detected from the bytes (PDF, PNG, JPEG, WebP), not from the name or the
  browser's content type. Size limits come from `MAX_CV_MB` and `MAX_POSTER_MB`.
- **Errors:** validation errors never echo submitted values back, and unexpected errors return a generic
  message while the details go to the server log.
- **Production:** set `APP_ENV=production` (hides `/api/docs` and `/api/openapi.json`) and `COOKIE_SECURE=true`,
  and serve over HTTPS.

## Next steps

- Add Alembic before the schema changes. `create_all` creates missing tables but never alters existing ones.
- Rate-limit the auth endpoints at the proxy (Cognito throttles too, but per pool, not per client).
