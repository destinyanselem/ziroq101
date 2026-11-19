// ZIROQ website. Plain ES module, no build step, light on memory (lists are limited, nothing autoplays).
// Everything that comes from YouTube or the worker is shown with textContent, never as HTML.
import { firebaseConfig, GITHUB_REPO, GITHUB_BRANCH } from "./firebase-config.js";

const FB = "https://www.gstatic.com/firebasejs/10.14.1/";
const CONSENT_VERSION = "v1-2026-11";
const CONSENT_TEXT =
  "ZIROQ can send the TRANSCRIPT of a video (and, in later steps, small video frames or clips) to Google's Gemini API.\n\n" +
  "• On Google's UNPAID tier, Google's terms say submitted content may be used to improve its products and may be read by human reviewers " +
  "(disconnected from your account). Do not use sensitive, confidential or personal material.\n" +
  "• To stay at zero cost, use Gemini keys created in Google AI Studio projects that have NO billing account linked.\n" +
  "• With this switched OFF, ZIROQ runs fully on its own local tools and nothing is sent to Gemini.";
const WORKER_POLL_MINUTES = 15;

const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k === "onclick") el.addEventListener("click", v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function safeUrl(u) { try { const x = new URL(u); return x.protocol === "https:" ? x.href : null; } catch { return null; } }
function link(label, url, cls = "btn") {
  const href = safeUrl(url);
  return href ? h("a", { class: cls, href, target: "_blank", rel: "noopener noreferrer" }, label) : null;
}
function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}
function validYouTube(u) {
  try {
    let t = u.trim(); if (!/^https?:\/\//i.test(t)) t = "https://" + t;
    const x = new URL(t); const host = x.hostname.replace(/^(www\.|m\.)/, "");
    let id = "";
    if (host === "youtu.be") id = x.pathname.split("/")[1] || "";
    else if (host === "youtube.com" || host === "music.youtube.com") {
      if (x.pathname === "/watch") id = x.searchParams.get("v") || "";
      else if (/^\/(shorts|embed|live)\//.test(x.pathname)) id = x.pathname.split("/")[2] || "";
    }
    return /^[A-Za-z0-9_-]{11}$/.test(id) ? "https://www.youtube.com/watch?v=" + id : null;
  } catch { return null; }
}

// ------------------------------------------------------------------ boot
const configured = firebaseConfig.apiKey && !String(firebaseConfig.apiKey).startsWith("PASTE");
if (!configured) { show($("view-config"), true); }
else boot().catch((e) => { show($("view-signin"), true); $("signin-msg").textContent = "Could not start: " + e.message; });

async function boot() {
  const [{ initializeApp }, A, S] = await Promise.all([
    import(FB + "firebase-app.js"), import(FB + "firebase-auth.js"), import(FB + "firebase-firestore.js"),
  ]);
  const app = initializeApp(firebaseConfig);
  const auth = A.getAuth(app);
  const db = S.getFirestore(app);
  let unsubs = [];
  let settings = { geminiConsent: false, requireApproval: true, discoveryEnabled: false, maxSourceMinutes: 90 };
  let lastJobs = [];
  let workerDispatchNotice = "";

  $("signin").onclick = async () => {
    $("signin-msg").textContent = "";
    try { await A.signInWithPopup(auth, new A.GoogleAuthProvider()); }
    catch (e) { $("signin-msg").textContent = "Sign-in did not finish (" + (e.code || e.message) + "). If a popup was blocked, allow popups for this site and try again."; }
  };
  $("signout").onclick = () => A.signOut(auth);

  A.onAuthStateChanged(auth, (user) => {
    unsubs.forEach((u) => u()); unsubs = [];
    show($("view-signin"), !user); show($("view-main"), !!user); show($("signout"), !!user);
    $("who").textContent = user ? user.email : "";
    if (user) start();
  });

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    for (const t of ["make", "jobs", "clips", "settings"]) show($("tab-" + t), t === b.dataset.tab);
  }));

  function listen(q, cb, label) {
    unsubs.push(S.onSnapshot(q, cb, (err) => {
      const msg = err.code === "permission-denied"
        ? "This Google account is not allowed to use this ZIROQ. Sign in with the owner account (and check the owner email in the security rules)."
        : "Could not load " + label + ": " + err.message;
      $("banner").hidden = false; $("banner").textContent = msg;
    }));
  }

  function start() {
    // settings
    S.getDoc(S.doc(db, "settings", "owner")).then((snap) => { if (snap.exists()) settings = { ...settings, ...snap.data() }; renderSettings(); }).catch(() => renderSettings());
    // jobs (latest 10 only)
    listen(S.query(S.collection(db, "jobs"), S.orderBy("createdAt", "desc"), S.limit(10)), (snap) => {
      lastJobs = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
      renderJobs(); renderBanner();
    }, "jobs");
    // clips (latest 20 only)
    listen(S.query(S.collection(db, "clips"), S.orderBy("createdAt", "desc"), S.limit(20)), (snap) => {
      renderClips(snap.docs.map((d) => ({ id: d.id, ...d.data() })));
    }, "clips");
  }

  // ---------------------------------------------------------------- worker dispatch
  async function dispatchWorkerNow() {
    const token = safeGet("ziroq_gh_token");
    if (!token) {
      return {
        ok: false,
        reason: "missing-token",
        message: "No GitHub token is saved in this browser. Save a fine-grained token in Settings with Actions: Read and write permission for this repository. The scheduled worker remains the fallback (about every 15 minutes).",
      };
    }

    try {
      const r = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/worker.yml/dispatches`, {
        method: "POST",
        headers: { Authorization: "Bearer " + token, Accept: "application/vnd.github+json", "Content-Type": "application/json" },
        body: JSON.stringify({ ref: GITHUB_BRANCH }),
      });
      if (r.status === 204) {
        return { ok: true, reason: "accepted", message: "GitHub accepted the worker start request. Open Jobs to watch the job move from Waiting to Working." };
      }
      if (r.status === 401 || r.status === 403) {
        return { ok: false, reason: "token-rejected", message: "GitHub rejected the saved token. Replace it in Settings with a fine-grained token that has Actions: Read and write permission for this repository." };
      }
      if (r.status === 404) {
        return { ok: false, reason: "not-found", message: "GitHub could not find the repository or worker workflow. Check the repository and branch settings." };
      }
      return { ok: false, reason: "http-error", message: "GitHub could not start the worker (HTTP " + r.status + "). The scheduled worker remains the fallback." };
    } catch (e) {
      return { ok: false, reason: "network-error", message: "Could not reach GitHub to start the worker: " + e.message + ". The scheduled worker remains the fallback." };
    }
  }

  // ---------------------------------------------------------------- make
  $("make").onclick = async () => {
    const msg = $("url-msg"); msg.className = "msg"; msg.textContent = "";
    const url = validYouTube($("url").value);
    if (!url) { msg.className = "msg bad"; msg.textContent = "Paste one YouTube video link (not a playlist or a channel)."; return; }
    $("make").disabled = true;
    try {
      const now = Date.now() / 1000;
      await S.addDoc(S.collection(db, "jobs"), { url, origin: "manual", status: "queued", progress: 0, stage: "queued", createdAt: now, updatedAt: now, tail: [] });
      $("url").value = "";
      msg.className = "msg ok"; msg.textContent = "Job added. Requesting an immediate worker start…";
      const startResult = await dispatchWorkerNow();
      workerDispatchNotice = startResult.ok
        ? startResult.message
        : "Job is queued. " + startResult.message;
      msg.textContent = startResult.ok
        ? "Job added. An immediate worker start was accepted by GitHub. Track it in Jobs."
        : "Job added, but an immediate start was not confirmed. " + startResult.message;
      if (!startResult.ok) msg.className = "msg";
      renderBanner();
    } catch (e) {
      msg.className = "msg bad";
      msg.textContent = "Could not add the job: " + (e.code === "permission-denied" ? "this account is not the owner." : e.message);
    }
    $("make").disabled = false;
  };

  $("start").onclick = async () => {
    const out = $("start-msg");
    $("start").disabled = true;
    out.textContent = "Asking GitHub to start the worker…";
    const result = await dispatchWorkerNow();
    out.textContent = result.message;
    workerDispatchNotice = result.ok
      ? result.message
      : "A job is waiting. " + result.message;
    renderBanner();
    $("start").disabled = false;
  };

  function renderBanner() {
    const b = $("banner"); b.hidden = true; b.textContent = "";
    const latest = lastJobs[0];
    const waiting = lastJobs.find((j) => j.status === "queued");
    if (latest && latest.status === "failed" && /cookie/i.test(latest.error || "")) {
      b.hidden = false; b.textContent = "The last job failed because YouTube blocked the download. Refresh the cookies secret (SETUP GUIDE, 'Refresh YouTube cookies'), then try again.";
    } else if (waiting) {
      const mins = Math.round((Date.now() / 1000 - waiting.createdAt) / 60);
      b.hidden = false;
      b.textContent = workerDispatchNotice || (mins > WORKER_POLL_MINUTES + 5
        ? "A job has been waiting " + mins + " minutes. Check the saved GitHub token or the worker workflow."
        : "A job is waiting. Save a GitHub Actions token in Settings to request an immediate worker start automatically; otherwise the scheduled worker is the fallback (about every " + WORKER_POLL_MINUTES + " minutes).");
    }
  }

  // ---------------------------------------------------------------- jobs
  function renderJobs() {
    const box = $("jobs"); box.replaceChildren();
    if (!lastJobs.length) { box.append(h("div", { class: "card muted" }, "No jobs yet. Paste a link on the Make tab.")); return; }
    for (const j of lastJobs) {
      const card = h("div", { class: "card job" },
        h("h2", {}, j.title || j.url || "Video"),
        h("span", { class: "chip " + j.status }, statusLabel(j.status)), " ", h("span", { class: "muted" }, ago(j.createdAt)));
      if (j.status === "running" || j.status === "queued") {
        card.append(h("progress", { max: 100, value: j.progress || 0 }), h("div", { class: "muted" }, (j.stage || "") + " · " + (j.progress || 0) + "%"));
        if (j.status === "queued") card.append(h("div", { class: "muted" }, "Waiting for the worker."));
      }
      if (j.note) card.append(h("div", { class: "muted" }, j.note));
      if (j.error) card.append(h("div", { class: "err" }, j.error));
      const row = h("div", { class: "row" });
      const folder = link("Open Drive folder", j.folderUrl); if (folder) row.append(folder);
      if (j.status === "queued") row.append(h("button", { onclick: () => S.updateDoc(S.doc(db, "jobs", j.id), { status: "cancelled", stage: "cancelled", updatedAt: Date.now() / 1000 }) }, "Cancel"));
      if (j.status !== "running" && j.status !== "queued") row.append(h("button", { class: "ghost", onclick: () => confirm("Remove this job from the list?") && S.deleteDoc(S.doc(db, "jobs", j.id)) }, "Remove"));
      card.append(row);
      if (j.tail && j.tail.length) card.append(h("details", {}, h("summary", {}, "Log (last " + j.tail.length + " lines)"), h("pre", { class: "log" }, j.tail.join("\n"))));
      box.append(card);
    }
  }
  const statusLabel = (s) => ({ queued: "Waiting", running: "Working", done: "Done", failed: "Failed", cancelled: "Cancelled" }[s] || s);

  // ---------------------------------------------------------------- clips
  function renderClips(clips) {
    const box = $("clips"); box.replaceChildren();
    if (!clips.length) { box.append(h("div", { class: "card muted" }, "No clips yet. Finished clips appear here.")); return; }
    for (const c of clips) {
      const rv = c.geminiReview;
      const card = h("div", { class: "card clip" },
        h("h2", {}, c.title || "Clip"),
        h("div", { class: "muted" }, Math.round(c.duration || 0) + " s · " + (c.sourceTitle || "") + (c.status && c.status !== "candidate" ? " · " + c.status : "")));
      if (rv && rv.verdict) card.append(h("div", {}, "AI review: " + rv.verdict + (rv.score != null ? " (score " + rv.score + "/10)" : ""), rv.summary ? h("div", { class: "muted" }, rv.summary) : null));
      if (c.qaSummary) card.append(h("div", { class: "muted" }, "Checks: " + c.qaSummary));
      if ((c.lengthNotes || []).length) card.append(h("details", {}, h("summary", {}, "Length check"), h("ul", { class: "flags" }, c.lengthNotes.map((n) => h("li", {}, n)))));
      if ((c.reviewFlags || []).length) card.append(h("details", { open: true }, h("summary", {}, "Please check (" + c.reviewFlags.length + ")"), h("ul", { class: "flags" }, c.reviewFlags.map((f) => h("li", {}, f)))));
      const row = h("div", { class: "row" });
      for (const [lab, u] of [["Open video", c.driveUrl], ["Cover", c.coverUrl]]) { const a = link(lab, u); if (a) row.append(a); }
      if (c.pack && Object.keys(c.pack).length) row.append(h("button", { onclick: () => openPack(c) }, "Posting pack"));
      const setStatus = (s) => S.updateDoc(S.doc(db, "clips", c.id), { status: s });
      row.append(h("button", { onclick: () => setStatus(c.status === "shortlisted" ? "candidate" : "shortlisted") }, c.status === "shortlisted" ? "Unshortlist" : "Shortlist"));
      row.append(h("button", { class: "ghost", onclick: () => setStatus(c.status === "rejected" ? "candidate" : "rejected") }, c.status === "rejected" ? "Undo reject" : "Reject"));
      card.append(row);
      box.append(card);
    }
  }

  // ---------------------------------------------------------------- posting pack viewer
  const PLATFORMS = [["youtube", "YouTube"], ["tiktok", "TikTok"], ["instagram", "Instagram"], ["facebook", "Facebook"], ["x", "X"], ["threads", "Threads"]];
  const LABELS = { title: "Title", title_alternatives: "Title ideas", description: "Description", hashtags: "Hashtags", tags_line: "Tags line", category: "Category",
    pinned_comments: "Pinned comment options", settings: "Settings to choose", caption: "Caption", search_keywords: "Search keywords", on_screen_text: "On-screen text",
    cover_text: "Cover text", alt_text: "Alt text", post: "Post", reply_comments: "Reply options", topic_tag: "Topic tag" };
  function openPack(clip) {
    $("pack-title").textContent = clip.title || "Posting pack";
    const tabs = $("pack-tabs"); tabs.replaceChildren();
    const have = PLATFORMS.filter(([k]) => clip.pack[k]);
    const showPlat = (k) => {
      tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === k));
      const body = $("pack-body"); body.replaceChildren();
      for (const [key, val] of Object.entries(clip.pack[k])) {
        if (key === "generated_by") { body.append(h("p", { class: "muted" }, "Written by: " + val)); continue; }
        const label = LABELS[key] || key;
        if (Array.isArray(val)) {
          if (key === "hashtags") body.append(field(label, val.join(" ")));
          else val.forEach((v, i) => body.append(field(label + (val.length > 1 ? " " + (i + 1) : ""), String(v), key === "settings")));
        } else if (val) body.append(field(label, String(val)));
      }
      const notes = clip.lengthNotes || [];
      if (notes.length) body.append(h("div", { class: "field" }, h("div", { class: "lab" }, "Length check"), h("ul", { class: "flags" }, notes.map((n) => h("li", {}, n)))));
    };
    for (const [k, name] of have) tabs.append(h("button", { class: "tab", "data-k": k, onclick: () => showPlat(k) }, name));
    if (have.length) showPlat(have[0][0]);
    show($("pack"), true);
  }
  function field(label, text, noCopy) {
    const btn = h("button", { class: "small" }, "Copy");
    btn.onclick = async () => {
      try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
      catch { const ta = h("textarea", {}, text); document.body.append(ta); ta.select(); try { document.execCommand("copy"); btn.textContent = "Copied"; } catch { btn.textContent = "Press and hold the text to copy"; } ta.remove(); }
      setTimeout(() => (btn.textContent = "Copy"), 1800);
    };
    return h("div", { class: "field" }, h("div", { class: "lab" }, label), h("div", { class: "val" }, text),
      h("div", { class: "count" }, text.length + (text.length === 1 ? " character" : " characters")), noCopy ? null : btn);
  }
  $("pack-close").onclick = () => show($("pack"), false);

  // ---------------------------------------------------------------- settings
  $("consent-text").textContent = CONSENT_TEXT; $("consent-text").style.whiteSpace = "pre-wrap";
  function renderSettings() {
    $("consent").checked = !!settings.geminiConsent;
    $("consent-state").textContent = settings.geminiConsent ? "Gemini is ON (agreed " + (settings.geminiConsentAt ? ago(settings.geminiConsentAt) : "earlier") + ")." : "Gemini is OFF. Clips are made with local tools only.";
    $("maxmin").value = settings.maxSourceMinutes || 90;
    $("approval").checked = settings.requireApproval !== false;
  }
  $("save-settings").onclick = async () => {
    const out = $("settings-msg"); out.className = "msg";
    const maxmin = Math.round(Number($("maxmin").value));
    if (!(maxmin >= 5 && maxmin <= 180)) { out.className = "msg bad"; out.textContent = "Longest video must be between 5 and 180 minutes."; return; }
    const consent = $("consent").checked;
    const next = { geminiConsent: consent, geminiConsentAt: consent ? (settings.geminiConsent && settings.geminiConsentAt ? settings.geminiConsentAt : Date.now() / 1000) : 0,
      geminiConsentTextVersion: CONSENT_VERSION, requireApproval: $("approval").checked, discoveryEnabled: false, maxSourceMinutes: maxmin };
    try { await S.setDoc(S.doc(db, "settings", "owner"), next); settings = next; renderSettings(); out.className = "msg ok"; out.textContent = "Saved."; }
    catch (e) { out.className = "msg bad"; out.textContent = "Could not save: " + e.message; }
  };
  $("save-token").onclick = () => {
    const v = $("ghtoken").value.trim(); const out = $("token-msg");
    if (!v) { out.className = "msg bad"; out.textContent = "Paste the token first."; return; }
    out.className = safeSet("ziroq_gh_token", v) ? "msg ok" : "msg bad";
    out.textContent = out.className === "msg ok" ? "Saved on this phone only." : "This browser would not store it (private mode?).";
    $("ghtoken").value = "";
  };
  $("clear-token").onclick = () => { try { localStorage.removeItem("ziroq_gh_token"); } catch {} $("token-msg").className = "msg ok"; $("token-msg").textContent = "Token removed."; };
}

function safeGet(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); return true; } catch { return false; } }
