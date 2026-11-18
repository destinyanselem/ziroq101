# ZIROQ: Phase 1 (manual website + worker)

## What this delivers
- **Website** (`docs/`): sign in (owner only), paste a YouTube link, watch live progress, see clips, open the posting pack with Copy buttons, shortlist/reject, settings with the Gemini consent screen.
- **Worker** (`worker/`): the v6 notebook engine, ported without rewriting its logic (`worker/engine_src/` are the notebook cells), run by GitHub Actions. Groq Whisper first with local faster-whisper as fallback; verified Drive upload.
- **Rules** (`firestore.rules`) and the **workflow** (`worker.yml`).
- Everything the addendum requires of the engine (up-to-180 s clips with the long-clip gate, the final-clip Gemini reviewer with one bounded fix and local fallback, per-clip length check) comes from the v6 engine unchanged.

## Not built yet (Phase 2, by design)
Daily discovery, view-velocity ranking, the daily 3-post selector, the Today page, the quota meter, and the optional ChatGPT/MCP module (off by default). Phase 1 needs no Render service at all.

## Verified here (run with fakes, no network)
63 automated tests pass, including: the notebook's own self-tests against the ported engine; YouTube URL validation (rejects file paths, playlists, look-alike domains); progress throttling and secret scrubbing; Groq (429, 401, 5xx, network errors, large file to FLAC, fallback); Drive (resumable upload, resume after a dropped connection, token refresh, retries, size check, invalid_grant message); job claim race and stale-job failure; the full job flow with a fake engine and fake Drive. The website was exercised in Chromium at 360 px wide against a fake Firebase: no sideways scroll, tap targets ≥ 44 px, titles with HTML shown as plain text, Copy works, written documents match the security rules' allowed shape.

## NOT verified (could not be run here: no access to these services)
- A real end-to-end run: real YouTube download, real Groq/Gemini calls, real rendering of a full video, real Drive upload, real Firestore, real GitHub Actions.
- `firestore.rules` was not run in the Firebase emulator (no Java/Firebase tools here). The website's writes were checked against the rules by hand only.
- The Firebase web SDK address (version 10.14.1), `denoland/setup-deno@v2`, and the exact button names in the setup guide come from my knowledge and should be checked on first use.
- Free-tier limits (Gemini requests/day, Groq limits, Actions minutes, Firestore 20k writes/50k reads) were not re-checked live; they are needed for Phase 2's quota meter.
- Whether Google lets a `drive.file`-only app go "In production" without review.

## Known risks (said plainly)
- **YouTube blocking** cloud downloads is the biggest risk. Cookies from a throwaway account help but expire (~2 weeks) and can get that account flagged.
- **Copyright / Content ID**: re-uploading other people's content can cause claims; Shorts over 60 s with a Content ID claim can be blocked worldwide. You carry that responsibility. Nothing is ever posted automatically.
- The first real run will probably reveal small problems; send me the Jobs-tab log and I will fix them.
