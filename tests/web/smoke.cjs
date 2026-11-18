// Browser smoke test: the real site in Chromium at 360x740 with Firebase replaced by an in-memory fake (no network).
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const ROOT = path.join(__dirname, "..", "..", "docs");
const OUT = process.argv[2] || "/tmp";
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const server = http.createServer((q, r) => { const f = path.join(ROOT, q.url === "/" ? "index.html" : q.url.split("?")[0]); fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); } else { r.writeHead(200, { "content-type": types[path.extname(f)] || "text/plain" }); r.end(d); } }); });

const FAKE_APP = `export function initializeApp(c){return {c}}`;
const FAKE_AUTH = `let cb; export function getAuth(){return {}} export class GoogleAuthProvider{}
export async function signInWithPopup(){ window.__user={email:'owner@example.com'}; cb&&cb(window.__user); return {user:window.__user}; }
export function onAuthStateChanged(a,f){cb=f; setTimeout(()=>f(window.__user||null),0); return ()=>{}}
export function signOut(){window.__user=null; cb&&cb(null)}`;
const FAKE_FS = `
const now=Date.now()/1000;
window.__db = window.__db || { jobs:{
  j1:{title:'<img src=x onerror="window.__xss=1">Big talk',url:'https://www.youtube.com/watch?v=dQw4w9WgXcQ',status:'running',progress:42,stage:'transcribing',createdAt:now-300,tail:['[10:00] transcribe chunk 2/4','AIza-should-never-appear? no: worker scrubs it']},
  j2:{title:'Finished one',url:'u',status:'done',progress:100,createdAt:now-9000,folderUrl:'https://drive.google.com/drive/folders/RUN',note:'3 clips ready'},
  j3:{url:'https://youtu.be/dQw4w9WgXcQ',status:'failed',progress:20,createdAt:now-10,error:'YouTube blocked the download from the cloud server. Refresh the cookies secret (SETUP GUIDE).'}},
  clips:{c1:{title:'Why <b>discipline</b> beats motivation',duration:58.4,sourceTitle:'Big talk',status:'candidate',createdAt:now,driveUrl:'https://drive.google.com/file/d/1/view',coverUrl:'https://drive.google.com/file/d/2/view',
   geminiReview:{verdict:'approve',score:8.1,summary:'Strong hook'},qaSummary:'technical pass, editorial pass',reviewFlags:['Long clip (92s): story needs the extra time'],lengthNotes:['YouTube Shorts: OK (58 s)'],
   pack:{youtube:{title:'Discipline beats motivation',title_alternatives:['A','B'],description:'Hook\\n\\nBody #Shorts',hashtags:['#Shorts','#Mindset'],tags_line:'a, b',category:'Education',pinned_comments:['Q1?','Q2?'],settings:['Visibility: choose']},x:{post:'Short post',reply_comments:['r1']}}}},
  settings:{owner:{geminiConsent:false,requireApproval:true,maxSourceMinutes:90}}};
window.__writes=[];
export function getFirestore(){return {}}
export function collection(d,n){return {n}} export function doc(d,n,id){return {n,id}}
export function query(c,...m){return c} export function orderBy(){} export function limit(){}
export function onSnapshot(c,cb){ const docs=Object.entries(window.__db[c.n]||{}).sort((a,b)=>(b[1].createdAt||0)-(a[1].createdAt||0)).map(([id,d])=>({id,data:()=>d})); setTimeout(()=>cb({docs}),0); return ()=>{} }
export async function getDoc(r){ const d=(window.__db[r.n]||{})[r.id]; return {exists:()=>!!d,data:()=>d} }
export async function addDoc(c,data){ window.__writes.push({op:'add',col:c.n,data}); return {id:'new'} }
export async function setDoc(r,data){ window.__writes.push({op:'set',col:r.n,id:r.id,data}) }
export async function updateDoc(r,data){ window.__writes.push({op:'update',col:r.n,id:r.id,data}) }
export async function deleteDoc(r){ window.__writes.push({op:'delete',col:r.n,id:r.id}) }`;
const CONFIG = `export const firebaseConfig={apiKey:'x',authDomain:'x',projectId:'x',appId:'x'}; export const GITHUB_REPO='me/repo'; export const GITHUB_BRANCH='main';`;

(async () => {
  await new Promise((r) => server.listen(0, r)); const base = "http://localhost:" + server.address().port + "/";
  const browser = await chromium.launch(); const ctx = await browser.newContext({ viewport: { width: 360, height: 740 }, deviceScaleFactor: 2, permissions: ["clipboard-read", "clipboard-write"] });
  const page = await ctx.newPage(); const problems = []; const log = (ok, msg) => { console.log((ok ? "PASS " : "FAIL ") + msg); if (!ok) problems.push(msg); };
  page.on("pageerror", (e) => log(false, "page error: " + e.message));
  await page.route(/gstatic\.com/, (r) => { const u = r.request().url(); const body = u.includes("firebase-app") ? FAKE_APP : u.includes("firebase-auth") ? FAKE_AUTH : FAKE_FS; r.fulfill({ contentType: "text/javascript", body }); });
  await page.route("**/firebase-config.js", (r) => r.fulfill({ contentType: "text/javascript", body: CONFIG }));
  await page.route("https://api.github.com/**", (r) => r.fulfill({ status: 204, body: "" }));

  await page.goto(base); await page.waitForSelector("#signin");
  await page.screenshot({ path: OUT + "/01_signin.png" });
  await page.click("#signin"); await page.waitForSelector("#view-main:not([hidden])");

  // layout checks on every tab
  for (const tab of ["make", "jobs", "clips", "settings"]) {
    await page.click(`[data-tab=${tab}]`); await page.waitForTimeout(150);
    const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
      small: [...document.querySelectorAll("button:not([hidden]),a.btn,input:not([type=checkbox]),summary")].filter((e) => e.offsetParent && e.getBoundingClientRect().height < 43.5).map((e) => (e.textContent || e.id).trim().slice(0, 20)) }));
    log(m.sw <= m.cw, `${tab}: no horizontal scroll (${m.sw} <= ${m.cw})`);
    log(m.small.length === 0, `${tab}: tap targets >= 44px` + (m.small.length ? " small: " + m.small.join("|") : ""));
    await page.screenshot({ path: `${OUT}/02_${tab}.png`, fullPage: true });
  }
  log(await page.evaluate(() => !window.__xss), "HTML in a YouTube title is NOT executed (shown as text)");
  await page.click("[data-tab=jobs]");
  log((await page.textContent("#jobs")).includes("<img src=x"), "job title shown as literal text");
  log(await page.evaluate(() => document.querySelectorAll("video,iframe").length === 0), "no video/iframe elements (nothing autoplays)");
  await page.click("[data-tab=make]");
  log(!(await page.isHidden("#banner")) && (await page.textContent("#banner")).toLowerCase().includes("cookies"), "cookie-failure banner appears");

  // make a job: invalid then valid
  await page.fill("#url", "https://vimeo.com/123"); await page.click("#make");
  log((await page.textContent("#url-msg")).includes("YouTube"), "non-YouTube link refused with a plain message");
  await page.fill("#url", "youtu.be/dQw4w9WgXcQ"); await page.click("#make"); await page.waitForTimeout(100);
  const w = await page.evaluate(() => window.__writes.find((x) => x.op === "add"));
  const allowed = ["url", "origin", "status", "progress", "stage", "createdAt", "updatedAt", "tail"];
  log(w && Object.keys(w.data).every((k) => allowed.includes(k)) && w.data.status === "queued" && w.data.origin === "manual" && w.data.progress === 0 && w.data.url.length <= 300, "new job document matches the security-rule shape: " + JSON.stringify(w && w.data));
  // start worker without token -> guidance; with token -> dispatch
  await page.click("#start"); log((await page.textContent("#start-msg")).includes("token"), "Start worker without token explains what to do");
  await page.click("[data-tab=settings]"); await page.fill("#ghtoken", "github_pat_dummy"); await page.click("#save-token");
  await page.click("[data-tab=make]"); await page.click("#start"); await page.waitForTimeout(150);
  log((await page.textContent("#start-msg")).includes("Worker started"), "Start worker dispatches (fake 204)");

  // clips: pack viewer + copy + shortlist rule shape
  await page.click("[data-tab=clips]");
  log((await page.textContent("#clips")).includes("<b>discipline</b>"), "clip title with markup shown literally");
  await page.click("text=Posting pack"); await page.waitForSelector("#pack:not([hidden])");
  await page.screenshot({ path: OUT + "/03_pack.png", fullPage: false });
  await page.click("#pack-body .field:first-child button"); await page.waitForTimeout(100);
  log((await page.evaluate(() => navigator.clipboard.readText())) === "Discipline beats motivation", "Copy puts the YouTube title on the clipboard");
  await page.click("#pack-close"); await page.click("text=Shortlist"); await page.waitForTimeout(100);
  const u = await page.evaluate(() => window.__writes.find((x) => x.op === "update" && x.col === "clips"));
  log(u && Object.keys(u.data).join() === "status" && u.data.status === "shortlisted", "shortlist writes only {status}");

  // settings save shape
  await page.click("[data-tab=settings]"); await page.check("#consent"); await page.click("#save-settings"); await page.waitForTimeout(100);
  const s = await page.evaluate(() => window.__writes.find((x) => x.op === "set" && x.col === "settings"));
  const sAllowed = ["geminiConsent", "geminiConsentAt", "geminiConsentTextVersion", "requireApproval", "discoveryEnabled", "maxSourceMinutes", "dailyPostCount"];
  log(s && Object.keys(s.data).every((k) => sAllowed.includes(k)) && s.data.geminiConsent === true && s.data.discoveryEnabled === false, "settings document matches rule shape; discovery stays OFF");
  log(!(await page.evaluate(() => JSON.stringify(window.__writes))).includes("github_pat"), "GitHub token never written to the database");
  await browser.close(); server.close(); console.log(problems.length ? "\n" + problems.length + " PROBLEM(S)" : "\nALL WEB CHECKS PASSED"); process.exit(problems.length ? 1 : 0);
})();
