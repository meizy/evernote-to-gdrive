# Setting Up Google Drive Credentials

By the end of this guide you will have a file called `client_secrets.json` in the folder where you'll run the tool. That file is what lets the tool upload your notes to your Google Drive.

**Time needed:** about 10 minutes  
**What you need:** a Google account and a web browser

---

## Step 1 — Create a Google Cloud project

Google requires you to register an "app" in their developer console before you can access Drive programmatically. Don't worry — this is just a configuration step, not actual software development.

1. Open [https://console.cloud.google.com/](https://console.cloud.google.com/) and sign in with your Google account.
2. At the very top of the page, click the **project selector** (it may say "Select a project" or show an existing project name).
3. In the popup, click **New Project** (top-right corner).
4. Give it a name — `evernote-to-gdrive` works well. Leave **Parent resource** set to "No organization", then click **Create**.
5. Wait a few seconds for the project to be created. A notification will appear; click **Select Project**, or use the project selector at the top to make sure your new project is active (its name should appear in the top bar).

---

## Step 2 — Enable the Google Drive API

By default, newly created projects cannot access Drive. You need to switch it on.

1. Click the **☰ icon** (three horizontal lines) in the top-left corner to open the navigation menu, then click **APIs & Services** → **Library**.
2. In the search box, type `Google Drive API` and press Enter.
3. Click the **Google Drive API** result.
4. Click **Enable**.

You'll be taken to the API's overview page once it's enabled.

---

## Step 3 — Configure the consent screen

When someone (you!) authorizes the app, Google shows a consent screen. You need to set it up before creating credentials.

1. In the navigation menu (☰), click **Google Auth Platform** → **Overview**.  
   *(If you don't see "Google Auth Platform", look for "APIs & Services → OAuth consent screen" — it's the same section, just renamed.)*
2. Click **GET STARTED**.
3. Fill in the form:
   - **App name:** `evernote-to-gdrive`
   - **User support email:** your own Google account email
4. Click **Next**.
5. Under **Audience**, select **External**, then click **Next**.  
   *(External just means "not restricted to a corporate Google Workspace domain" — it's the right choice for personal use.)*
6. Under **Contact Information**, enter your email again, then click **Next**.
7. Check the box to agree to the Google API User Data Policy, then click **Continue** and then **Create**.

---

## Step 4 — Add yourself as a test user

While the app is in "Testing" status (which is fine for personal use — you never need to publish it), only explicitly listed accounts can authorize it.

1. You should still be in the Google Auth Platform from the previous step. Click **Audience** in the left panel.
2. Scroll down to the **Test users** section.
3. Click **+ Add users**.
4. Enter your Google account email address, then click **Add** and **Save**.

---

## Step 5 — Create OAuth 2.0 credentials

This is what produces the `client_secrets.json` file.

1. Still in Google Auth Platform, click **Clients** in the left panel.  
   *(Alternatively: **APIs & Services** → **Credentials** → **+ Create Credentials** → **OAuth client ID**.)*
2. Click **+ Create Client**.
3. For **Application type**, select **Desktop app**.
4. For **Name**, enter `evernote-to-gdrive desktop`.
5. Click **Create**.
6. A popup appears showing your Client ID and Client Secret. Click **Download JSON** — your browser will save a file with a name like `client_secret_686050612909-....json`. 
7. Click **OK** to close the popup. You're done with the Google Cloud Console and can close the browser tab.

---

## Step 6 — Put the file in the right place

By default the tool looks for `client_secrets.json` in the **current working directory** — i.e. the folder you're in when you run `evernote-to-gdrive`.

1. Move the downloaded `client_secret_....json` file into the folder from which you plan to run the tool (for example, next to the downloaded `evernote-to-gdrive` exe, or inside a dedicated folder you'll `cd` into).
2. Rename it to exactly **`client_secrets.json`** — note the **s** at the end of "secrets".

> **Tip:** You can keep the secrets file anywhere and use the `--secrets-folder /path/to/folder` flag to point the tool at it.

---

## Step 7 — First run and authorization

To authenticate with Google and save your credentials, run:

```
evernote-to-gdrive auth
```

1. A browser window will open automatically, asking you to sign in with your Google account.
2. **Important:** You may see a warning screen that says *"Google hasn't verified this app"*. This is expected.  
   Click **Advanced** (small link at the bottom), then click **Go to evernote-to-gdrive (unsafe)**.
3. You'll see a permission screen with checkboxes. Make sure the **Google Drive** permission is checked, then click **Continue**.
4. The browser will show: *"The authentication flow has completed. You may close this window."*
5. Back in the terminal, the tool will proceed with the upload.

A `token.json` file is now saved next to `client_secrets.json`. It stores your authorization so you don't have to sign in every time. **Keep both files private** — treat them like passwords.

> **Note:** This token expires after 7 days (a Google limitation for apps in Testing status). If the tool opens the browser and asks you to sign in again after a week, that's normal — just repeat steps 1–4 above.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| *"Access blocked: evernote-to-gdrive has not completed the Google verification process"* | Your account isn't listed as a test user | Go back to Step 4 and add your exact Google account email |
| *"OAuth client secrets file not found"* | Wrong location or wrong filename | Make sure `client_secrets.json` (with an **s**) is in the folder where you're running the tool, or pass `--secrets-folder` to point to its location |
| Browser asks you to sign in again after a few days | Testing-mode tokens expire after 7 days | Normal — just complete the browser flow again |
| *"The OAuth client was not found"* | You're signed into a different Google account in the browser | Sign out of other accounts and use the same account you added in Step 4 |
| JSON file has a top-level key `"web"` instead of `"installed"` | Wrong application type selected | Go back to Step 5, delete the credential, and re-create it as **Desktop app** |
