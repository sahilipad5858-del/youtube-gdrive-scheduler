# Drive → YouTube Auto Scheduler (Webpage)

A custom web app (Flask + Google Drive API + YouTube Data API v3) that:

1. Connects your Google Drive.
2. Lets you pick videos from a Drive folder.
3. Edit custom **title, tags, description** per video and see an **SEO score**.
4. Requires **YouTube verification** before anything is uploaded/scheduled.
5. **Schedules uploads every hour** (configurable interval + batch size).
6. **No duplication** — dedup by Drive file ID against pending/scheduled/uploaded videos.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open: http://localhost:5000

## Google OAuth setup

1. Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID (Desktop app).
2. Enable **Google Drive API** and **YouTube Data API v3**.
3. Add redirect URI: `http://localhost:5000/api/auth/google/callback`
4. Put env vars in `.env` or shell:

```
GOOGLE_OAUTH_CLIENT_ID=your_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:5000/api/auth/google/callback
```

## How the flow maps to your requirements

- **Custom title / tags / description / SEO**: Step 3 of the UI, per-video editor + SEO score.
- **Schedule every hour**: Step 4, "Every X minutes" (default 60) + batch size (videos per slot).
- **No duplication**: Queue is keyed by Drive file ID; re-adding a file already pending/scheduled/uploaded is blocked.
- **Upload all videos at a time for schedule**: Batch size = how many videos get scheduled into one hourly slot.
- **Verification first**: Uploads/scheduling are gated behind the Verification step (Step 5). Scheduler will not publish until verified.

## Notes

- Uploads use YouTube Data API: insert as `private`, then flip to `public` at the scheduled time.
- State is stored in `data/state.json`.
- Dev server only — use gunicorn/uwsgi for production.
