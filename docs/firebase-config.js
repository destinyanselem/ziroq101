// ZIROQ website settings. Replace ONLY the text between the quotes. Everything else stays as it is.
// Where to find the firebaseConfig values: Firebase console > Project settings (gear icon) > General > "Your apps" > Web app > Config.
// These values are NOT secrets (they identify your project); the security rules are what protect your data.

export const firebaseConfig = {
  apiKey: "PASTE_apiKey_HERE",
  authDomain: "PASTE_authDomain_HERE",
  projectId: "PASTE_projectId_HERE",
  appId: "PASTE_appId_HERE",
};

// Your GitHub repository for the worker, written as  username/repository  (used by the "Start worker now" button).
export const GITHUB_REPO = "PASTE_username/repository_HERE";
// The branch the worker workflow lives on.
export const GITHUB_BRANCH = "main";
