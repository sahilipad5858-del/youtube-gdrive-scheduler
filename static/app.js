const state = { videos: [], schedule: {}, verified: false, google: {}, youtube: {} };

const $ = (id) => document.getElementById(id);

function toast(msg, isError) {
  const el = $("toast");
  el.textContent = msg;
  el.style.background = isError ? "#dc2626" : "#22c55e";
  el.style.color = isError ? "#fff" : "#062";
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.style.display = "none"), 3500);
}

async function api(path, opts) {
  const res = await fetch(path, {
    method: opts && opts.method ? opts.method : "GET",
    headers: opts && opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts && opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.details || "Request failed");
  return data;
}

async function refreshAll() {
  const s = await api("/api/state");
  state = s;
  renderStatus(s);
  const v = await api("/api/videos");
  state.videos = v.videos;
  renderQueue(v.videos);
}

function renderStatus(s) {
  $("drive-status").textContent = s.google && s.google.connected ? "Connected" : "Not connected";
  $("youtube-status").textContent = s.youtube && s.youtube.connected ? "Connected" : "Not connected";
  $("verify-status").textContent = s.verified ? "Verified ✅" : "Not verified";
  $("sched-status").textContent = s.schedule && s.schedule.enabled ? `On (${s.schedule.intervalMinutes}m / batch ${s.schedule.batchSize})` : "Off";
}

// ---- connect ----

$("btn-connect").addEventListener("click", async () => {
  try {
    const d = await api("/api/auth/google/start");
    if (d.auth_url) window.location.href = d.auth_url;
    else toast("Google OAuth not configured: " + (d.error || ""), true);
  } catch (e) {
    toast("Connect failed: " + e.message, true);
  }
});

// ---- drive videos ----

let driveVideos = [];

async function loadDriveVideos() {
  const folderId = $("folder-id").value.trim() || undefined;
  try {
    const d = await api("/api/drive/videos?folderId=" + encodeURIComponent(folderId || ""));
    driveVideos = d.videos;
    const box = $("drive-video-list");
    box.innerHTML = "";
    if (!driveVideos.length) {
      box.innerHTML = '<div class="video-item">No videos found in selected folder.</div>';
      return;
    }
    for (const v of driveVideos) {
      const div = document.createElement("div");
      div.className = "video-item";
      div.innerHTML = `
        <div class="head">
          <div class="name">${escapeHtml(v.name)}</div>
          <label><input type="checkbox" class="drive-check" value="${v.driveFileId}" /> Select</label>
        </div>
        <div class="name" style="font-weight:400;color:#9aa7bd;font-size:12px;">${v.mimeType} · ${v.size ? (Number(v.size) / 1024 / 1024).toFixed(2) + " MB" : ""}</div>`;
      box.appendChild(div);
    }
    $("btn-add-selected").disabled = true;
  } catch (e) {
    toast("Failed to load Drive videos: " + e.message, true);
  }
}

$("btn-load-videos").addEventListener("click", loadDriveVideos);

$("btn-add-selected").addEventListener("click", async () => {
  const checks = [...document.querySelectorAll(".drive-check:checked")];
  const videos = checks.map((c) => {
    const v = driveVideos.find((x) => x.driveFileId === c.value) || { driveFileId: c.value, name: "Untitled" };
    return { driveFileId: v.driveFileId, name: v.name, title: v.name, description: "", tags: [] };
  });
  try {
    const d = await api("/api/videos", { method: "POST", body: JSON.stringify({ videos }) });
    toast(`Added ${d.added.length} video(s) to queue`);
    await refreshAll();
  } catch (e) {
    toast("Add failed: " + e.message, true);
  }
});

// ---- queue ----

function statusBadge(status) {
  const map = {
    pending: "pending",
    scheduled: "scheduled",
    uploading: "uploading",
    uploaded: "uploaded",
    failed: "failed",
  };
  const cls = map[status] || "pending-text";
  const label = { pending: "Pending", scheduled: "Scheduled", uploading: "Uploading", uploaded: "Uploaded", failed: "Failed" }[status] || status;
  return `<span class="badge ${cls}">${label}</span>`;
}

function renderQueue(videos) {
  const box = $("queue-list");
  box.innerHTML = "";
  if (!videos.length) {
    box.innerHTML = '<div class="video-item">Queue is empty. Load videos from Google Drive and add them.</div>';
    return;
  }
  for (const v of videos) {
    const div = document.createElement("div");
    div.className = "video-item";
    const seo = v.seo || {};
    div.innerHTML = `
      <div class="head">
        <div>
          <div class="name">${escapeHtml(v.name)}</div>
          <div class="name" style="font-weight:400;color:#9aa7bd;font-size:12px;">Drive ID: ${v.driveFileId}</div>
        </div>
        ${statusBadge(v.status)}
      </div>
      <div class="row">
        <input class="v-title" data-id="${v.id}" value="${escapeHtml(v.title || "")}" placeholder="Custom title" ${v.status === "pending" ? "" : "disabled"} />
        <textarea class="v-desc" data-id="${v.id}" placeholder="Custom description" ${v.status === "pending" ? "" : "disabled"}>${escapeHtml(v.description || "")}</textarea>
        <input class="v-tags" data-id="${v.id}" value="${escapeHtml((v.tags || []).join(", "))}" placeholder="Tags (comma separated)" ${v.status === "pending" ? "" : "disabled"} />
        <div class="seo">SEO score: <span class="score">${seo.score ?? 0}</span>${seo.notes && seo.notes.length ? " — " + escapeHtml(seo.notes.join("; ")) : ""}</div>
      </div>
      <div class="actions">
        <button class="save-v" data-id="${v.id}" ${v.status === "pending" ? "" : "disabled"}>Save metadata</button>
        <button class="upload-v" data-id="${v.id}" ${v.status === "pending" && state.verified ? "" : "disabled"}>Upload now</button>
        <button class="del-v" data-id="${v.id}" style="background:#dc2626;">Remove</button>
      </div>
    `;
    box.appendChild(div);
  }

  for (const btn of box.querySelectorAll(".save-v")) {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const item = state.videos.find((x) => x.id === id);
      const title = box.querySelector(`.v-title[data-id="${id}"]`).value;
      const desc = box.querySelector(`.v-desc[data-id="${id}"]`).value;
      const tags = box.querySelector(`.v-tags[data-id="${id}"]`).value.split(",").map((t) => t.trim()).filter(Boolean);
      try {
        const d = await api(`/api/videos/${id}`, { method: "POST", body: JSON.stringify({ title, description: desc, tags }) });
        toast("Metadata saved");
        await refreshAll();
      } catch (e) {
        toast("Save failed: " + e.message, true);
      }
    });
  }

  for (const btn of box.querySelectorAll(".upload-v")) {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      try {
        const d = await api(`/api/videos/${id}/upload`, { method: "POST" });
        toast(d.ok ? "Uploaded! YouTube ID: " + d.youtubeId : "Upload failed: " + (d.error || ""));
        await refreshAll();
      } catch (e) {
        toast("Upload failed: " + e.message, true);
      }
    });
  }

  for (const btn of box.querySelectorAll(".del-v")) {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      try {
        await api(`/api/videos/${id}`, { method: "DELETE" });
        await refreshAll();
      } catch (e) {
        toast("Remove failed: " + e.message, true);
      }
    });
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- schedule ----

$("btn-apply-schedule").addEventListener("click", async () => {
  const enabled = $("sched-enabled").checked;
  const intervalMinutes = parseInt($("sched-interval").value, 10) || 60;
  const batchSize = parseInt($("sched-batch").value, 10) || 1;
  const start = $("sched-start").value ? new Date($("sched-start").value).toISOString() : new Date().toISOString();
  try {
    const d = await api("/api/schedule", { method: "POST", body: JSON.stringify({ enabled, intervalMinutes, batchSize, startAt: start }) });
    toast("Schedule applied");
    await refreshAll();
  } catch (e) {
    toast("Schedule failed: " + e.message, true);
  }
});

// ---- verify ----

$("btn-verify").addEventListener("click", async () => {
  try {
    const d = await api("/api/verify", { method: "POST" });
    state.verified = d.verified;
    renderStatus(state);
    toast("Verification marked complete ✅");
  } catch (e) {
    toast("Verify failed: " + e.message, true);
  }
});

// ---- init ----

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await refreshAll();
  } catch (e) {
    toast("Init failed: " + e.message, true);
  }
  const schedStart = new Date(Date.now() + 3600000);
  $("sched-start").value = toLocalInputValue(schedStart);
});

function toLocalInputValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
