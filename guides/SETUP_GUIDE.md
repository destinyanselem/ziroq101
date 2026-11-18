# ZIROQ setup guide (phone or desktop)

Everything below is done in a browser. Button names can move slightly as Google and GitHub update their consoles.

**The golden rule:** keep Firebase and Google Cloud on free/no-billing settings for this build. Do not add a card or link a billing account.

What you are building: a private website (GitHub Pages) → a job list (Firebase) → a worker (GitHub Actions) that downloads a YouTube video, creates clips, and puts the results in your Google Drive.

---

## Part A. GitHub (the code is already in place)

1. Use the existing public repository: **destinyanselem/ziroq101**. Do not create a second repository or upload the ZIP again.
2. On the **Code** tab, confirm you can see `docs/`, `worker/`, `tests/`, `guides/`, and `firestore.rules`.
3. On the **Actions** tab, **ZIROQ CI** runs the automated Python and browser tests on each push. **ZIROQ worker** is the actual video-processing worker; it can be started manually or checks the queue every 15 minutes.
4. If GitHub says Actions are disabled, enable Actions for this repository.

## Part B. Firebase (job list + sign-in)

1. Open console.firebase.google.com and select the existing ZIROQ project with project ID `ziroq-417df`. Do not create a second Firebase project.
2. Keep the project on the Spark (free) plan. Do not link billing.
3. Open **Build → Authentication → Get started → Sign-in method → Google**. Enable Google sign-in, choose your support email, and save.
4. In Authentication → **Settings → Authorized domains**, add `destinyanselem.github.io`.
5. Open **Build → Firestore Database → Create database**. Choose a nearby region; `europe-west` is a reasonable choice from Nigeria. Start in production mode.
6. In Firestore → **Rules**, paste the plain text contents of the repository's `firestore.rules` file (do not include Markdown fences or line numbers) and click **Publish**. The owner email is already configured in that file.
7. Open **Project settings → General → Your apps**. Register a Web app named `ziroq-web` (or open the existing one) and copy the `apiKey`, `authDomain`, `projectId`, and `appId` values from its `firebaseConfig`.
8. In Project settings → **Service accounts**, generate a new private key only after removing any key that has been exposed. Treat the downloaded JSON as a password; never commit it to the repository or paste it into chat. Add it only as the `FIREBASE_SERVICE_ACCOUNT` GitHub Actions secret.

## Part C. Add the Firebase values to the website

1. In GitHub, open `docs/firebase-config.js` and edit it.
2. The `projectId` is already set to `ziroq-417df`. Replace only `PASTE_apiKey_HERE`, `PASTE_authDomain_HERE`, and `PASTE_appId_HERE` with the exact values from the registered Web app's `firebaseConfig`. Keep the quotation marks.
3. Leave `GITHUB_REPO` set to `destinyanselem/ziroq101` and `GITHUB_BRANCH` set to `main`. Commit the file.

## Part D. Publish the website

1. In the repository, open **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**, branch `main`, folder `/docs`, then save.
3. The site address is `https://destinyanselem.github.io/ziroq101/`. Open it after GitHub Pages finishes deploying.

## Part E. Google Drive (where finished clips go)

You need a Client ID, Client secret, and Refresh token.

1. Open console.cloud.google.com. Create or select a project for Drive integration (for example `ziroq-drive`). Do not link billing.
2. Open **APIs & Services → Library**, search for **Google Drive API**, and enable it.
3. Open **APIs & Services → OAuth consent screen** (sometimes called **Google Auth Platform**). Choose **External**, set app name `ZIROQ`, enter support/developer contact emails, and add only the `drive.file` scope.
4. Set the consent screen to **In production**. If Google requires verification or shows a warning, stop and review that prompt rather than assuming the app is approved.
5. Under **Credentials**, create an **OAuth client ID** of type **Web application**. Add `https://developers.google.com/oauthplayground` as an authorized redirect URI. Copy the Client ID and Client secret somewhere safe.
6. Open developers.google.com/oauthplayground. In settings, enable **Use your own OAuth credentials** and paste the Client ID and Client secret.
7. Enter `https://www.googleapis.com/auth/drive.file` as the scope, authorize it with the Google account that owns the Drive output, then exchange the authorization code for tokens.
8. Copy the Refresh token. Do not paste it into chat or commit it to GitHub.

## Part F. API keys

- **Groq** (speech-to-text): create an API key at console.groq.com.
- **Gemini** (optional review): create an API key at aistudio.google.com. The current worker is pinned to the Google GenAI SDK 2.x until its SDK v3 behavior is migrated and tested.

## Part G. Add GitHub Actions secrets

In **destinyanselem/ziroq101 → Settings → Secrets and variables → Actions**, create these repository secrets:

| Name | Value |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | Full service-account JSON from Part B |
| `GOOGLE_OAUTH_CLIENT_ID` | Client ID from Part E |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Client secret from Part E |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Refresh token from Part E |
| `GROQ_API_KEY_1` (and optionally `_2`) | Groq key(s) |
| `GEMINI_API_KEY_1` … `_5` | Gemini key(s), optional when Gemini consent is off |
| `YT_COOKIES` | Optional; add only if YouTube blocks cloud downloads |

You can leave out `PIXABAY_API_KEY` and `COVERR_API_KEY`; they are optional B-roll sources.

## Part H. Enable the "Start worker now" button

1. In GitHub, open **Settings → Developer settings → Personal access tokens → Fine-grained tokens** and create a token.
2. Set repository access to **Only select repositories → ziroq101**. Grant **Actions: Read and write**. No other permission is needed for the worker-dispatch button.
3. Paste the token into the website's **Settings → GitHub token** field and save it on this phone/browser. The token stays in that browser's local storage; it is not written to Firestore. Without a token, the worker's 15-minute queue check can still start jobs.

## Part I. First real test

1. Open `https://destinyanselem.github.io/ziroq101/`, sign in with the Google account specified in Firestore rules, and save Settings once.
2. Submit a short, public YouTube video you have permission to process.
3. Start the worker, then watch the **Jobs** tab. When complete, inspect the output in **Clips** and Google Drive.

A real run still depends on valid Firebase configuration, Firestore rules, GitHub Secrets, Google OAuth, API keys, and YouTube's download availability. The automated CI tests do not validate those external account settings.

## Refresh YouTube cookies (only if a job reports a YouTube bot check)

Use cookies only from an account you are authorized to use. Prefer an account dedicated to this purpose, and never share cookie contents in chat or commit them to the repository. Export a Netscape-format `cookies.txt` for youtube.com and add its contents as the `YT_COOKIES` GitHub secret. Cookies can expire or be revoked.

## What common errors mean

| Message | Next step |
|---|---|
| YouTube bot check / sign-in required | Check whether the video is public and whether `YT_COOKIES` is present and current. |
| Google Drive `invalid_grant` | Re-authorize the Drive app and update `GOOGLE_DRIVE_REFRESH_TOKEN`. |
| "This account is not allowed" | Sign in with the owner Google account and verify the email in Firestore rules. |
| Worker check fails before claiming a job | Verify `FIREBASE_SERVICE_ACCOUNT` is present and valid. |
| Drive upload fails | Verify the OAuth client ID, client secret, and refresh token. |
