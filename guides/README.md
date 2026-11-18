# ZIROQ: Phase 1 (manual website + worker)

## What this delivers
- **Website** (`docs/`): sign in (owner only), paste a YouTube link, watch live progress, see clips, open the posting pack with Copy buttons, shortlist/reject, settings with the Gemini consent screen.
- **Worker** (`worker/`): the v6 notebook engine, ported without rewriting its logic (`worker/engine_src/` are the notebook cells), run by GitHub Actions. Groq Whisper first with local faster-whisper as fallback; verified Drive upload.
- **Rules** (`firestore.rules`) and the workflow (`.github/workflows/worker.yml`).
- Everything the addendum requires of the engine (up-to-180 s clips with the long-clip gate, the final-clip Gemini reviewer with one bounded fix and local fallback, per-clip length check) comes from the v6 engine unchanged.

## Not built yet (Phase 2, by design)
Daily discovery, view-velocity ranking, the daily 3-post selector, the Today page, the quota meter, and the optional ChatGPT/MCP module (off by default). Phase 1 needs no Render service at all.

## Verified locally (fakes; no real account credentials)
65 automated Python tests pass, including: the notebook's own self-tests against the ported engine; YouTube URL validation (rejects file paths, playlists, look-alike domains); progress throttling and secret scrubbing; Groq retry/fallback behavior; Drive resumable-upload recovery; job-claim race and stale-job failure; full job flow with a fake engine and fake Drive. The browser smoke test covers mobile-width layout, safe text rendering, Copy, settings, and Firestore document shapes against fake Firebase/GitHub endpoints.

The worker pins `google-genai` to `>=2.3.0,<3.0.0` until its SDK v3 behavior is migrated and verified.

## Not yet verified with live services
- A real end-to-end run: real YouTube download, real Groq/Gemini calls, real rendering, real Drive upload, real Firestore, and the actual GitHub Actions worker.
- Firestore rules in the Firebase emulator.
- The Firebase web app configuration, owner email, service-account secret, OAuth secrets, and API keys in the actual accounts.
- GitHub Pages publication and a real sign-in through Firebase Authentication.

## Known risks
- YouTube can block downloads from cloud workers. Valid cookies may help but can expire or be revoked.
- Re-uploading other people's content can cause copyright claims or platform restrictions. Only process videos you have permission to use. Nothing is posted automatically.
