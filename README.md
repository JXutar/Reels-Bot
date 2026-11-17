# Reels Bot

Turns short lesson scripts into vertical videos with an AI voiceover, sends each one to your phone on Telegram for approval, and posts the approved ones to Instagram as **trial reels** on a schedule. Everything runs free on GitHub Actions.

```
 You + Claude            GitHub Actions                     Your phone           Instagram
 ─────────────           ─────────────────────────          ──────────           ─────────
 5 scripts / month ──▶   render.yml                          Telegram:
 (JSON files in          · voiceover (edge-tts)        ──▶   ✅ Approve
  content/queue/)        · slides + video (ffmpeg)           ❌ Reject
                         · host on Cloudinary                     │
                                                                  ▼
                         tick.yml (every hour)          ◀──  your taps
                         · every Wed after 8pm,
                           posts the oldest approved  ──────────────────▶  Trial reel
                           reel, then tells you                           (non-followers first,
                                                                           you graduate it manually)
```

**Cost: RM0.** GitHub Actions, edge-tts voices, Telegram, Cloudinary's free plan and the Instagram API are all free at this volume. The scripts (5 a month) come from Claude in your Pro plan, so there's no API bill.

---

## One-time setup (about 45 minutes)

### 1. Put the code on GitHub
1. Create a new repository on GitHub (e.g. `reels-bot`). **Public** gives you unlimited free Actions minutes, and your secrets stay hidden either way. **Private** also works, because the free monthly minutes are enough for this.
2. Upload every file in this folder, keeping the folder structure. Don't forget `.github/workflows/`.

### 2. Telegram bot (where you review reels)
1. In Telegram, message **@BotFather** → `/newbot` → pick a name. Copy the **bot token**.
2. Open your new bot and send it `/start`.
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and find `"chat":{"id": 123456789`. That number is your **chat id**.

### 3. Cloudinary (temporary video hosting)
With Instagram Login, Instagram downloads the video from a public link. It can't take a direct upload. The bot uploads each reel to Cloudinary and deletes it right after posting (or as soon as you reject it).
1. Sign up at cloudinary.com (free plan, no card needed).
2. From the dashboard, copy **Cloud name**, **API key** and **API secret**.

### 4. Instagram API (Meta developer app)
Your account must be **public** and **professional** (Creator works).
1. Go to developers.facebook.com → **My Apps** → **Create app**. When asked for a use case, choose the one for **managing content on Instagram** (Instagram API).
2. Open **Instagram → API setup with Instagram business login**.
3. If asked, add your account as an **Instagram tester** under *App roles*, then accept the invite in the Instagram app (*Settings → Website permissions / Apps and websites → Tester invites*).
4. Under **Generate access tokens**, click **Add account**, log in with your Instagram account, and approve the permissions (`instagram_business_basic`, `instagram_business_content_publish`).
5. Copy the **access token** and the **Instagram user ID** shown next to your account.

You can keep the app in development mode. You don't need App Review to post to your own account. Meta's screens change often, so if a button is named differently, ask Claude and we'll walk through it together.

### 5. Token auto-renewal (so it never expires)
Instagram tokens last 60 days. The `refresh-token` workflow renews them twice a month, but it needs permission to update your secrets:
1. GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens* → **Generate new token**.
2. Repository access: only `reels-bot`. Permissions: **Secrets → Read and write**.
3. Copy the token.

### 6. Add the secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add these:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from step 2 |
| `TELEGRAM_CHAT_ID` | from step 2 |
| `CLOUDINARY_CLOUD_NAME` | from step 3 |
| `CLOUDINARY_API_KEY` | from step 3 |
| `CLOUDINARY_API_SECRET` | from step 3 |
| `IG_USER_ID` | from step 4 |
| `IG_ACCESS_TOKEN` | from step 4 |
| `GH_PAT` | from step 5 |

### 7. Personalise and test
1. In `config/profiles/insurance.yaml`, set `handle:` to your Instagram handle. Change colours or posting days if you like.
2. **Actions → Check setup → Run workflow.** You should get a 👋 message on Telegram.
3. **Actions → Render new reels → Run workflow.** About 5 minutes later, your 5 reels arrive on Telegram with Approve / Reject buttons.

> **Trial reels need a minimum follower count** (Instagram's threshold is reported as somewhere between 200 and 1,000 for professional accounts). If your account doesn't qualify yet, posting will fail with an error on Telegram. Set `trial_reel: false` in the profile to post normal reels until it does.

---

## Monthly routine (about 5 minutes)

1. Near the end of each month, in your **Social media Posting** Claude project, ask: *"Write next month's 5 insurance reel scripts."* Claude writes 5 JSON files in the format below.
2. In GitHub, open `content/queue/insurance/`, then **Add file → Upload files** and put them in a new month folder (e.g. `2026-12/`). Commit.
3. Rendering starts automatically, and the reels arrive on Telegram. Approve the ones you're happy with.
4. One approved reel posts every Wednesday after 8pm. Months with only 4 Wednesdays carry the extra one over to the next month. Telegram tells you when each one is posted. Want a different day? Change `days:` in the profile.
5. In Instagram, check each trial reel after a day or two. If it did well, open it and tap **Share to everyone**.

**Telegram commands:** `/status` shows what's pending, approved and posted. `/post <id>` posts an approved reel right now.

---

## Script format

```json
{
  "id": "2026-12-01-co-payment",
  "profile": "insurance",
  "title": "What is co-payment?",
  "scenes": [
    {"style": "hook", "text": "On-screen text with *highlighted* words", "voice": "What the voiceover says."},
    {"text": "Main point", "sub": "Optional smaller line underneath", "voice": "..."},
    {"style": "cta", "text": "Follow for *more*", "voice": "Follow for more."}
  ],
  "caption": "Instagram caption text",
  "hashtags": ["#tag1", "#tag2"]
}
```
- `text` is what appears on screen (keep it short). `voice` is what's spoken (can be longer and more natural).
- `*asterisks*` colour words in the accent colour.
- `style`: `hook` (big first slide), `cta` (last slide), or leave it out for normal slides.
- The profile's disclaimer footer and default hashtags are added to every caption automatically.

---

## Switching topics or adding a second account

Copy `config/profiles/insurance.yaml` to e.g. `config/profiles/study-tips.yaml`, then change the name, colours, voice, hashtags and rules. Add the new name to `config/settings.yaml` under `active_profiles`, and put its scripts in `content/queue/study-tips/`.

To post that profile to a **different Instagram account**, set `user_id_secret: IG2_USER_ID` and `token_secret: IG2_ACCESS_TOKEN` in its profile, add those two secrets, and add matching `env:` lines to `tick.yml`, `check.yml` and `refresh-token.yml`.

---

## Preview on your own computer (optional)

Needs Python 3.10+ and ffmpeg.
```bash
pip install -r requirements.txt
python -m bot render --local          # all queued scripts → out/*.mp4
python -m bot render --local --file content/queue/insurance/2026-11/01-deductible.json
```

## Good to know
- GitHub pauses scheduled workflows in repos with no activity for 60 days. Your monthly script uploads keep it active.
- GitHub's hourly schedule can run a little late at busy times. That's normal.
- If anything fails, the bot messages you on Telegram with the error, and the details are under the repo's **Actions** tab.
- `state/state.json` is the bot's memory (what's approved and posted). The workflows update it, so don't edit it by hand.

## Project layout
```
bot/            the Python code (render, slides, tts, telegram, instagram, storage, cli)
config/         settings.yaml + one YAML per content profile
content/queue/  your scripts, one folder per profile, then per month
content/assets/ fonts (Inter, OFL licence) and optional music
state/          what has been sent, approved, posted
.github/        the four workflows
```
