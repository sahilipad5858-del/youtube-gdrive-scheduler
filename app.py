import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests as google_requests
from flask import Flask, jsonify, request, send_from_directory
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

app = Flask(__name__, static_folder="static")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

DEFAULT_STATE = {
    "verified": False,
    "google": {"connected": False, "email": None, "token": None},
    "youtube": {"connected": False},
    "videos": [],
    "schedule": {
        "enabled": False,
        "intervalMinutes": 60,
        "batchSize": 1,
        "startAt": None,
    },
}

_scheduler_stop = False


# ---------------- state persistence ----------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged.update(data)
        merged["google"] = {**DEFAULT_STATE["google"], **(data.get("google") or {})}
        merged["schedule"] = {**DEFAULT_STATE["schedule"], **(data.get("schedule") or {})}
        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)
    return state


# ---------------- google services ----------------

def get_google_credentials():
    st = load_state()
    token = (st.get("google") or {}).get("token") or {}
    if not token.get("access_token"):
        return None
    return Credentials(
        token=token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=SCOPES,
    )


def build_google_services():
    creds = get_google_credentials()
    if not creds:
        return None, None
    try:
        creds.refresh(GoogleRequest())
    except Exception:
        return None, None
    drive = build("drive", "v3", credentials=creds, static_discovery=False)
    youtube = build("youtube", "v3", credentials=creds, static_discovery=False)
    return drive, youtube


# ---------------- seo scoring ----------------

def compute_seo(title, description, tags):
    score = 0
    notes = []
    t = (title or "").strip()
    d = (description or "").strip()
    tag_list = tags or []
    if 40 <= len(t) <= 70:
        score += 15
    else:
        notes.append("Title length should be 40-70 characters")
    if t and t == t.capitalize():
        score += 5
    if len(d) >= 100:
        score += 20
    else:
        notes.append("Description should be at least 100 characters")
    if len(tag_list) >= 5:
        score += 15
    else:
        notes.append("Add at least 5 tags")
    if len(tag_list) > 15:
        score += 5
    return {
        "score": min(100, score),
        "titleLength": len(t),
        "descLength": len(d),
        "tagCount": len(tag_list),
        "notes": notes,
    }


# ---------------- youtube upload ----------------

def run_upload(video):
    drive, youtube = build_google_services()
    if drive is None or youtube is None:
        raise RuntimeError("Google services unavailable - reconnect Google Drive")
    media = drive.files().get_media(fileId=video.get("driveFileId"))
    body = {
        "snippet": {
            "title": video.get("title") or video.get("name") or "Untitled",
            "description": video.get("description") or "",
            "tags": video.get("tags") or [],
        },
        "status": {"privacyStatus": "private"},
    }
    request_ = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = request_.execute()
    yt_id = resp.get("id")
    youtube.videos().update(part="status", id=yt_id, body={"status": {"privacyStatus": "public"}}).execute()
    return yt_id


# ---------------- scheduler ----------------

def scheduler_loop():
    global _scheduler_stop
    while not _scheduler_stop:
        time.sleep(20)
        try:
            state = load_state()
            sched = state.get("schedule") or {}
            if not sched.get("enabled") or not state.get("verified"):
                continue
            now = datetime.now(timezone.utc)
            due = []
            for v in state.get("videos") or []:
                if v.get("status") != "scheduled" or not v.get("scheduledAt"):
                    continue
                try:
                    sched_at = datetime.fromisoformat(str(v.get("scheduledAt")).replace("Z", "+00:00"))
                except Exception:
                    continue
                if sched_at <= now:
                    due.append((v, sched_at))
            changed = False
            for v, sched_at in sorted(due, key=lambda x: x[1]):
                try:
                    yt_id = run_upload(v)
                    v["status"] = "uploaded"
                    v["youtubeId"] = yt_id
                    v["publishedAt"] = datetime.now(timezone.utc).isoformat()
                    v["scheduledAt"] = None
                    changed = True
                except Exception as e:
                    v["status"] = "failed"
                    v["error"] = str(e)
                    changed = True
            if changed:
                save_state(state)
        except Exception:
            pass


_scheduler_started = False


def start_scheduler():
    global _scheduler_started, _scheduler_stop
    if _scheduler_started:
        return
    _scheduler_started = True
    _scheduler_stop = False
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


# ---------------- api routes ----------------

@app.before_request
def _ensure_scheduler_started():
    start_scheduler()
    return None


@app.get("/")
def index():
    return send_from_directory(str(app.static_folder), "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/state")
def api_state():
    return jsonify(load_state())


@app.get("/api/auth/google/start")
def auth_google_start():
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "Google OAuth client ID not configured", "auth_url": None}), 500
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5000/api/auth/google/callback")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return jsonify({"auth_url": auth_url})


@app.get("/api/auth/google/callback")
def auth_google_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "missing code"}), 400
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5000/api/auth/google/callback")
    try:
        resp = google_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        return jsonify({"error": "oauth_failed", "details": str(e)}), 500
    if not data.get("access_token"):
        return jsonify({"error": "oauth_failed", "details": data}), 400
    state = load_state()
    state.setdefault("google", {})
    state["google"]["connected"] = True
    state["google"]["token"] = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expiry": data.get("expires_in"),
    }
    save_state(state)
    return jsonify({"ok": True})


@app.get("/api/drive/videos")
def drive_videos():
    folder_id = request.args.get("folderId")
    drive, _youtube = build_google_services()
    if drive is None:
        return jsonify({"error": "google_not_connected"}), 401
    q = "mimeType contains 'video/' and trashed = false"
    if folder_id:
        q = f"'{folder_id}' in parents and {q}"
    out = []
    page_token = None
    while True:
        resp = (
            drive.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime)",
                pageToken=page_token,
                pageSize=100,
            )
            .execute()
        )
        for f in resp.get("files") or []:
            out.append(
                {
                    "driveFileId": f.get("id"),
                    "name": f.get("name"),
                    "mimeType": f.get("mimeType"),
                    "size": f.get("size"),
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return jsonify({"videos": out})


@app.get("/api/videos")
def list_videos():
    return jsonify({"videos": load_state().get("videos") or []})


@app.post("/api/videos")
def add_videos():
    body = request.get_json(silent=True) or {}
    incoming = body.get("videos") or []
    if not incoming:
        return jsonify({}), 400
    state = load_state()
    added = []
    for v in incoming:
        fid = v.get("driveFileId")
        name = v.get("name") or "Untitled"
        title = v.get("title") or name
        description = v.get("description") or ""
        tags = v.get("tags") or []
        duplicate = any(
            x.get("driveFileId") == fid and x.get("status") in ("pending", "scheduled", "uploading")
            for x in state.get("videos") or []
        )
        if duplicate:
            continue
        rec = {
            "id": str(uuid.uuid4()),
            "driveFileId": fid,
            "name": name,
            "title": title,
            "description": description,
            "tags": tags,
            "status": "pending",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "scheduledAt": None,
            "youtubeId": None,
            "publishedAt": None,
            "batchId": None,
        }
        rec["seo"] = compute_seo(rec["title"], rec["description"], rec["tags"])
        state.setdefault("videos", []).append(rec)
        added.append(rec)
    save_state(state)
    return jsonify({"added": added})


@app.post("/api/videos/<vid>")
def update_video(vid):
    state = load_state()
    v = next((x for x in state.get("videos") or [] if x.get("id") == vid), None)
    if not v:
        return jsonify({}), 404
    body = request.get_json(silent=True) or {}
    if body.get("title") is not None:
        v["title"] = body.get("title")
    if body.get("description") is not None:
        v["description"] = body.get("description")
    if body.get("tags") is not None:
        v["tags"] = body.get("tags")
    v["seo"] = compute_seo(v.get("title"), v.get("description"), v.get("tags"))
    save_state(state)
    return jsonify({"video": v})


@app.delete("/api/videos/<vid>")
def delete_video(vid):
    state = load_state()
    state["videos"] = [x for x in state.get("videos") or [] if x.get("id") != vid]
    save_state(state)
    return jsonify({"ok": True})


@app.post("/api/videos/<vid>/upload")
def upload_now(vid):
    state = load_state()
    v = next((x for x in state.get("videos") or [] if x.get("id") == vid), None)
    if not v:
        return jsonify({}), 404
    if v.get("youtubeId"):
        return jsonify({"error": "already_uploaded", "video": v}), 409
    if not state.get("verified"):
        return jsonify({"error": "verification_required", "video": v}), 403
    v["status"] = "uploading"
    save_state(state)
    try:
        yt_id = run_upload(v)
        v["status"] = "uploaded"
        v["youtubeId"] = yt_id
        v["publishedAt"] = datetime.now(timezone.utc).isoformat()
        v["scheduledAt"] = None
        save_state(state)
        return jsonify({"ok": True, "youtubeId": yt_id, "video": v})
    except Exception as e:
        v["status"] = "failed"
        v["error"] = str(e)
        save_state(state)
        return jsonify({"error": str(e), "video": v}), 500


@app.post("/api/schedule")
def set_schedule():
    body = request.get_json(silent=True) or {}
    state = load_state()
    state.setdefault("schedule", {})
    state["schedule"]["enabled"] = bool(body.get("enabled", False))
    state["schedule"]["intervalMinutes"] = max(1, int(body.get("intervalMinutes") or 60))
    state["schedule"]["batchSize"] = max(1, int(body.get("batchSize") or 1))
    start = body.get("startAt")
    state["schedule"]["startAt"] = start
    if state["schedule"]["enabled"]:
        pending = sorted(
            [v for v in state.get("videos") or [] if v.get("status") == "pending"],
            key=lambda x: x.get("createdAt") or "",
        )
        interval = timedelta(minutes=state["schedule"]["intervalMinutes"])
        batch_size = state["schedule"]["batchSize"]
        slot_time = datetime.fromisoformat(str(start)) if start else datetime.now(timezone.utc)
        i = 0
        while i < len(pending):
            batch = pending[i : i + batch_size]
            for v in batch:
                v["status"] = "scheduled"
                v["scheduledAt"] = slot_time.astimezone(timezone.utc).isoformat()
            i += batch_size
            slot_time = slot_time + interval
    save_state(state)
    return jsonify({"ok": True, "schedule": state["schedule"]})


@app.post("/api/verify")
def verify():
    state = load_state()
    state["verified"] = True
    save_state(state)
    return jsonify(
        {
            "ok": True,
            "verified": True,
            "note": "Complete YouTube channel/brand verification in YouTube Studio before scheduled public uploads.",
        }
    )


# ---------------- serve ----------------

@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "not_found"}), 404


if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=5000, debug=False)
