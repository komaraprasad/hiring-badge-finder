# Deploying Hiring Badge Finder for free (Render + Neon)

No card is needed for either service.

- **Neon** keeps the data (leads, searches, settings) in a free Postgres database.
- **Render** runs the dashboard and server on its free plan.
- The LinkedIn reading still happens in each user's own browser, through the extension.
  Render never contacts LinkedIn.

## Before you start: no login

This deployment has **no password**. Anyone who has the address can open the dashboard, start searches
and download every saved lead (names, LinkedIn URLs, companies). Share the address only with the people
who should use it.

## 1. Create the free database (Neon)

1. Go to https://neon.tech, click **Sign up** and sign up with GitHub or Google (no card).
2. Create a project: name `hiring-badge-finder`, Postgres version as offered, and the region closest to
   your Render region (e.g. **AWS Singapore** for Render Singapore, **AWS US East** for Render Ohio/Virginia).
3. On the project dashboard click **Connect**. Copy the **connection string**. It looks like
   `postgresql://neondb_owner:...@ep-....neon.tech/neondb?sslmode=require`.
   Keep it private: it is the password to your data.

The app creates its tables by itself on first start.

## 2. Create the web service (Render)

The code is already on GitHub (`komaraprasad/hiring-badge-finder`).

1. Go to https://dashboard.render.com and sign in with GitHub.
2. Click **New → Web Service** and pick the `hiring-badge-finder` repository.
3. Fill in:

   | Field | Value |
   |---|---|
   | Name | `hiring-badge-finder` |
   | Region | the one near your Neon region |
   | Branch | `main` |
   | Runtime / Language | Python 3 |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "*"` |
   | Instance Type | **Free** |

4. Under **Environment Variables** add:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string from step 1 |
   | `PYTHON_VERSION` | `3.12.8` |

5. Under **Advanced**, set **Health Check Path** to `/api/extension/info`.
6. Click **Create Web Service**. The first build takes 2–4 minutes. When it says **Live**, open the address
   at the top, e.g. `https://hiring-badge-finder.onrender.com`.

(Alternatively, **New → Blueprint** reads `render.yaml` and asks only for `DATABASE_URL`.)

## 3. Install the extension (each user)

The extension must come **from the deployed dashboard**, not from the project folder. The downloaded copy
is set up to trust and talk to that one address only.

1. Open the deployed dashboard and click **Download** in the install box. Extract the zip.
2. In Edge, open `edge://extensions`, turn on **Developer mode**, click **Load unpacked** and choose
   the extracted folder.
3. Refresh the dashboard (Ctrl+F5). The install box disappears.
4. Sign in to LinkedIn **in the same Edge profile** with the account meant for this tool, never the
   company account.

## What the free plans mean

| Free limit | Effect | Handled by |
|---|---|---|
| Render sleeps after 15 min without visitors | The first visit takes ~1 minute to load | Just wait; a search keeps it awake while it runs |
| Render: 750 free hours a month | Enough for one service running all month | — |
| Neon: 0.5 GB storage | Hundreds of thousands of leads | — |
| Neon suspends the database when idle | The first request after a pause is ~1 s slower | The app reconnects by itself |

## If it stops working after a deploy

| What you see | Why | Fix |
|---|---|---|
| Dashboard says the extension is too old / asks to Reload | The deploy changed the extension | Download it again from the dashboard, replace the folder, click **Reload** in `edge://extensions`, Ctrl+F5 |
| "Could not reach the dashboard server" | Render was restarting or waking for more than ~2 minutes (the extension retries that long) | Wait until the page loads, then Launch again: it continues where it stopped |
| "Already 5 searches running" | Old searches lost their browser tab | Wait 15 minutes (quiet searches close by themselves) or press **Stop** on them |
| Page does not load / "Service Unavailable" | Deploy, build or wake-up in progress, or failed | Render → the service → **Events** / **Logs** |
| Logs say `connection failed` / `password authentication failed` | `DATABASE_URL` is wrong or missing | Copy the connection string from Neon again into Render → **Environment**, then **Manual Deploy** |

Avoid pushing changes while a search is running: each push restarts the server.

## Settings

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Neon connection string (`postgresql://...`). Without it the app uses a local SQLite file, which a free Render service loses on every restart. |
| `RENDER_EXTERNAL_URL` | Set by Render automatically: the only website the server and the extension trust |
| `HBF_PUBLIC_URL` | Only with a custom domain, e.g. `https://leads.example.com` |
| `HBF_WEBSITE_LOOKUP` | `1` (default) fills Company Website by DNS lookup; `0` turns it off |
| `PYTHON_VERSION` | 3.12.8 |

## Backups

Neon keeps a restore history of your database (Neon console → **Backup & Restore**). For a file copy, use
**Export all** on the Leads tab (Excel).

## Running on a computer instead

Nothing changes: `run.bat` still starts it at http://localhost:8000 with a local SQLite file, and the
extension loaded from the `extension` folder talks to it.
