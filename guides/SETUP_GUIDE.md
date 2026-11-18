# ZIROQ setup guide (phone only, ₦0)

Everything below is done in Chrome on your phone. Button names are written exactly as I know them; Google, Firebase and GitHub sometimes move or rename things, so if a name is slightly different, pick the closest one. **Take your time: it is about 14 small steps, and each one is independent.**

**The golden rule:** never add a credit card or "billing account" anywhere. If any page asks you to "Upgrade", "Enable billing" or "Link billing account", **stop and do not**.

What you are building: a private website (GitHub Pages) → a job list (Firebase) → a worker (GitHub Actions) that downloads the video, makes the clips, and puts them in your Google Drive.

Tip: for Google Cloud, Firebase and GitHub pages, Chrome's menu (⋮) → **Desktop site** makes the buttons easier to find.

---

## Part A. GitHub (the home of the code and the worker)

1. Open github.com and sign in. (Advice: use a separate GitHub account just for ZIROQ.)
2. Tap **+** (top right) → **New repository**. Name: `ziroq`. Choose **Public** (public repositories get free worker minutes). Tap **Create repository**.
3. In the new repository tap **Add file** → **Create new file**. In the name box type exactly `.github/workflows/unpack.yml` (typing the slashes makes the folders). Open the file **unpack.yml** I sent you, copy everything, paste it into the big box, tap **Commit changes** → **Commit changes**.
4. Do the same for the second file: name `.github/workflows/worker.yml`, paste everything from **worker.yml**, commit.
5. Tap **Add file** → **Upload files**, choose **ziroq-system.zip** (keep its name), wait until it shows, tap **Commit changes**.
6. Tap the **Actions** tab → **Unpack ZIROQ** (left list) → **Run workflow** → **Run workflow**. After about 30 seconds it turns green. Go back to the **Code** tab: you now see folders `worker`, `docs`, `guides`, `tests` and the file `firestore.rules`.

If the Actions tab says workflows are disabled, tap the green button that enables them.

## Part B. Firebase (job list + sign-in)

1. Open console.firebase.google.com, sign in with your Google account, tap **Create a project** (or **Add project**). Name it `ziroq`. If it asks about Google Analytics, switch it **off**. Tap **Create project**.
2. **Stay on the Spark (free) plan.** Never press Upgrade.
3. Left menu (☰) → **Build** → **Authentication** → **Get started** → **Sign-in method** → **Google** → switch **Enable** on → choose your support email → **Save**.
4. In Authentication go to **Settings** → **Authorized domains** → **Add domain** → type `YOURGITHUBNAME.github.io` (your GitHub username, lowercase) → **Add**.
5. Left menu → **Build** → **Firestore Database** → **Create database** → pick the location closest to you (for Nigeria, `europe-west` is a sensible choice) → **Start in production mode** → **Create**.
6. In Firestore open the **Rules** tab. Delete everything in the box. Open `firestore.rules` in your GitHub repository (tap the file, then the copy icon), paste it. **Find the one place that says `OWNER_EMAIL_HERE` and replace those words with your Gmail address in lowercase, keeping the quote marks.** Tap **Publish**.
7. Tap the gear ⚙ (top left) → **Project settings** → scroll to **Your apps** → tap the **</>** (Web) icon → nickname `ziroq-web` → **Register app**. You will see a block called `firebaseConfig` with `apiKey`, `authDomain`, `projectId`, `appId`. Keep this page open (or screenshot it).
8. Still in Project settings: tab **Service accounts** → **Generate new private key** → **Generate key**. A small `.json` file downloads. Open it (Files app → tap it → Chrome or a text viewer), **select all, copy**. You will paste it as a secret in Part F. Treat it like a password and delete the file afterwards.

## Part C. Put your Firebase values into the website

1. In GitHub open `docs/firebase-config.js` → tap the pencil ✎ (Edit).
2. Replace `PASTE_apiKey_HERE`, `PASTE_authDomain_HERE`, `PASTE_projectId_HERE`, `PASTE_appId_HERE` with your values (keep the quotes). Replace `PASTE_username/repository_HERE` with `YOURGITHUBNAME/ziroq`. Tap **Commit changes**.

## Part D. Turn the website on

1. In the repository tap **Settings** → **Pages** (left list). Under **Build and deployment** → **Source: Deploy from a branch**. Branch `main`, folder **/docs**, **Save**.
2. Wait about a minute and refresh. The address appears: `https://YOURGITHUBNAME.github.io/ziroq/`. Open it on your phone and **bookmark it / Add to Home screen**. You can already sign in. (The site is not listed on search engines, and only your Google account can see data.)

## Part E. Google Drive (where your finished clips go)

You need three things: a Client ID, a Client secret and a Refresh token.

1. Open console.cloud.google.com (desktop site). At the top tap the project name → **New project** → name `ziroq-drive` → **Create**. **Do not link billing.**
2. Menu → **APIs & Services** → **Library** → search **Google Drive API** → **Enable**.
3. Menu → **APIs & Services** → **OAuth consent screen** (may be called **Google Auth platform**). Choose **External**, app name `ZIROQ`, your email for support and developer contact, then save through the steps. On **Scopes** add only `.../auth/drive.file`. 
4. **Very important:** on the consent screen page set **Publishing status** to **In production** (tap **Publish app**). If you leave it on *Testing*, Google deletes your Drive permission every **7 days** and ZIROQ will stop with a message about `invalid_grant`. (`drive.file` is not a sensitive scope, so no review is expected. I could not check this live: if Google asks for verification, tell me what it says.)
5. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID** → type **Web application** → name `ziroq`. Under **Authorized redirect URIs** tap **Add URI** and type exactly `https://developers.google.com/oauthplayground` → **Create**. Copy the **Client ID** and **Client secret** somewhere safe.
6. Open developers.google.com/oauthplayground (desktop site). Tap the gear ⚙ → tick **Use your own OAuth credentials** → paste the Client ID and Client secret → close.
7. In the box **Input your own scopes** type `https://www.googleapis.com/auth/drive.file` → **Authorize APIs** → choose your Google account → if you see "Google hasn't verified this app", tap **Advanced** → **Go to ZIROQ (unsafe)** → **Allow**. 
8. Back in the Playground tap **Exchange authorization code for tokens**. Copy the **Refresh token** (starts with `1//`). 

## Part F. Free keys

- **Groq** (speech-to-text): console.groq.com → sign up → **API Keys** → **Create API Key** → copy (starts with `gsk_`). If you can, make a second one with another account: ZIROQ rotates keys.
- **Gemini**: aistudio.google.com → **Get API key** → **Create API key**. **Create each key in a different new project with NO billing account.** Repeat for as many keys as you have (up to 5 are used).

## Part G. Give the worker its secrets

In your GitHub repository: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add each row (name exactly as written):

| Name | Value |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | the whole `.json` text from B8 |
| `GOOGLE_OAUTH_CLIENT_ID` | Client ID from E5 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Client secret from E5 |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Refresh token from E8 |
| `GROQ_API_KEY_1` (and `_2`) | your Groq key(s) |
| `GEMINI_API_KEY_1` … `_5` | your Gemini key(s) (any you have) |
| `YT_COOKIES` | see "Refresh YouTube cookies" below (do this after your first test) |

You can leave out `PIXABAY_API_KEY` and `COVERR_API_KEY` for now (they only add optional B-roll sources).

## Part H. The "Start worker now" button (optional but nice)

GitHub → your profile picture → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**. Name `ziroq`, expiry 90 days, **Only select repositories** → `ziroq`. Under **Repository permissions** set **Actions: Read and write**. Generate, copy. In the ZIROQ website go to **Settings** → paste it → **Save token on this phone**. (It never leaves your phone.) Without it, jobs still start by themselves within about 15 minutes.

## Part I. First test

1. Open the website, sign in, go to **Settings**, read the Gemini notice, choose on/off, **Save settings**.
2. **Make** tab → paste a link to a short talking video (10–20 minutes is a good first test) → **Make clips** → **Start worker now**.
3. Watch the **Jobs** tab. A full run takes a while (downloading, transcribing, rendering five clips). Finished clips appear in the **Clips** tab and in Google Drive under **ZIROQ Output**.

## Refresh YouTube cookies (when a job says YouTube blocked the download)

YouTube often blocks downloads from cloud computers. The usual fix is to give the worker a `cookies.txt` from a **throwaway Google account** (never your main one: YouTube can flag accounts used this way, and the cookies stop working after roughly two weeks).

1. Make a new Google account just for this. Sign in to YouTube with it in **Firefox** (Firefox for Android supports add-ons).
2. Install a cookies-export add-on for Firefox (search the add-ons for "cookies.txt"), open youtube.com, export the cookies for `youtube.com` as **Netscape cookies.txt**. I could not test the phone steps myself, so if this part is awkward, tell me and we will find another way.
3. Copy the whole text and put it in the GitHub secret `YT_COOKIES` (Settings → Secrets and variables → Actions → `YT_COOKIES` → **Update secret**).

## What the messages mean

| You see | What to do |
|---|---|
| "YouTube blocked the download…" | Refresh the cookies (above). |
| "Google rejected the Drive refresh token (invalid_grant)" | Part E step 4 was missed (Testing mode) or access was removed. Publish the app, redo E6–E8, update the secret. |
| "This account is not allowed" on the website | Sign in with the owner Gmail, and check the email in the Firebase rules (B6). |
| A job stays "Waiting" for over 20 minutes | Tap **Start worker now**, or open the GitHub **Actions** tab and make sure workflows are enabled. |
| "The worker stopped responding" | It timed out. Tap **Make clips** again. |
