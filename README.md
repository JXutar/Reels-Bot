# Reels Bot

Send an idea (or a full script you wrote) to your Telegram bot. It writes the script if needed, makes a vertical reel with an AI voiceover, an **AI-generated picture for every scene** (slow zoom, crossfades, headlines and word-by-word subtitles), and sends it back to you. Then you tap **🚀 Post now**, **🗓 Schedule**, or **❌ Reject**. Everything runs free on GitHub Actions.

```
 You (Telegram)                 GitHub Actions (every ~30 min)                       Instagram
 ──────────────                 ──────────────────────────────                       ─────────
 /idea 60s: why co-payment...   AI writes the script (Gemini, free)
 /script SCENE 1 / VO: ...  ──▶ or keeps your script exactly as written
                                voiceover (edge-tts) + AI picture per scene
                                (Cloudflare, free) + subtitles  ──▶  video to you
 [🚀 Post now] [🗓 Schedule] [❌]  ◀──────────────────────────────────────
         └────────────────────▶ posts now, or at the time you chose  ──────────▶  trial reel
```

**The short version:** send your bot `/idea 60s: why medical cards have a co-payment`. A few minutes later the finished reel arrives on Telegram. Tap 🚀 to post it, or 🗓 and reply `fri 9pm`.

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
Your Instagram account must be **public** and **professional** (Creator works). You also need a Facebook account to sign in to Meta's developer site.

**a) Become a Meta developer**
1. Go to developers.facebook.com and click **Get Started** (top right). "My Apps" only appears once you're logged in and registered.
2. Log in with Facebook, accept the terms, and verify your phone. Choose any role ("Developer" or "Student").
   - **No SMS arriving?** Choose **Malaysia (+60)** and type the number *without* the leading 0. If it still doesn't come, first add and confirm your number in Facebook (*Settings → Accounts Center → Personal details → Contact info*), then come back. If you've pressed Resend many times, wait a few hours before trying again.

**b) Create the app**
1. Go to **developers.facebook.com/apps** → **Create app**. For the use case, pick the **Instagram API** one (managing content on Instagram).
2. In the app dashboard, go to **Use cases** → **Customize** (Instagram API) → **API setup with Unified login**. Don't use the "Facebook login" option, because it needs a Facebook Page.

**c) Create a business portfolio (Meta requires one)**
1. On that page, click **+ Business portfolio** → **Create a new business portfolio**. Any name works (e.g. "Reel Bot"). It's free, private, and doesn't change your profiles.
2. In the portfolio's settings (business.facebook.com → **Settings → Accounts → Instagram accounts**), make sure your Instagram account is listed. Then click **Assign people** → choose yourself → turn on **Full control** → **Assign**.
3. Back on **API setup with Unified login**, select your portfolio. The orange "Business portfolio selection is mandatory" warning should disappear.

**d) Get your token and ID**
1. Under **Generate access tokens**, click **Add account** / **Generate token** next to your Instagram account. Log in and allow the permissions (`instagram_business_basic`, `instagram_business_content_publish`).
2. If Meta asks, accept the **tester invite** in the Instagram app (*Settings → Website permissions → Apps and websites → Tester invites*), then generate the token again.
3. Copy the **access token** → secret `IG_ACCESS_TOKEN`. Keep it private: anyone with it can post to your account.
4. Copy the **Instagram user ID** shown next to your account (a long number starting with 1784…) → secret `IG_USER_ID`. The **Check setup** workflow in step 7 confirms both are right.

You can keep the app in development mode, since posting to your own account doesn't need App Review. Meta renames buttons often, so if something looks different, send Claude a screenshot.

### 5. Token auto-renewal (so it never expires)
Instagram tokens last 60 days. The `refresh-token` workflow renews them twice a month, but it needs permission to update your secrets:
1. GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens* → **Generate new token**.
2. Repository access: only `reels-bot`. Permissions: **Secrets → Read and write**.
3. Copy the token.

### 6. Add the secrets
Secrets are how the bot gets your passwords without them ever appearing in the code. You create **8 required secrets**, plus the optional ones for AI writing (Gemini), AI pictures (Cloudflare) and stock photos (Pexels/Pixabay), one per row below. Each one has two boxes:
- **Name:** type the exact name from the left column (capitals and underscores matter, because the bot looks secrets up by name).
- **Secret:** paste *only* the value, with no quotes and no labels.

For each row: **Settings → Secrets and variables → Actions → New repository secret** → fill in Name + Secret → **Add secret**. Afterwards, the page should list exactly these 8 names under **Repository secrets**:

| Name (type exactly) | Secret (paste the value) |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from step 2 |
| `TELEGRAM_CHAT_ID` | from step 2 |
| `CLOUDINARY_CLOUD_NAME` | from step 3 |
| `CLOUDINARY_API_KEY` | from step 3 |
| `CLOUDINARY_API_SECRET` | from step 3 |
| `IG_USER_ID` | from step 4 |
| `IG_ACCESS_TOKEN` | from step 4 |
| `GH_PAT` | from step 5 |
| `CLOUDFLARE_ACCOUNT_ID` *(for AI pictures)* | see step 6e |
| `CLOUDFLARE_API_TOKEN` *(for AI pictures)* | see step 6e |
| `GEMINI_API_KEY` *(for /idea)* | free key from aistudio.google.com, see step 6c |
| `PIXABAY_API_KEY` *(optional)* | free key from pixabay.com/api/docs, for stock videos + pop-up images |

### 6b. Pixabay (optional, for stock backgrounds and pop-up images)
1. Sign up free at pixabay.com, then open **pixabay.com/api/docs**. Your key is shown on that page (in the `key` parameter section) once you're logged in.
2. Add it as the secret `PIXABAY_API_KEY`.

(A Pexels key also works as `PEXELS_API_KEY` if you have one. Pexels has paused new keys, though.)

Without a key, reels still work: they use your own clips (see *Video styles*) or a plain gradient, and scenes with a keyword `image` just skip the pop-up.

### 6c. Gemini (for `/idea`, free)
1. Go to **aistudio.google.com** and sign in with a Google account.
2. Click **Get API key → Create API key**, then copy it.
3. Add it as the secret `GEMINI_API_KEY`.

The bot automatically uses the newest free Gemini "flash" model, and falls back to another one if a model is busy or over its free limit. Note: on the free tier, Google may use what you send to improve its models. That's fine for public reel ideas, but don't put private client details in an idea.
*(Optional: add an `ANTHROPIC_API_KEY` secret instead to have Claude write the scripts. It's paid per use, a few sen per script, and when present it's used instead of Gemini.)*

### 6d. Make the bot check Telegram every 30 minutes (cron-job.org, free)
GitHub's own hourly schedule often runs 5–12 hours late, so your `/idea` messages and taps could sit for hours. cron-job.org pokes the bot on time instead.
1. **Make a token that can start workflows:** GitHub → *Settings → Developer settings → Fine-grained tokens → Generate new token*. Repository access: only `Reels-Bot`. Permissions: **Actions → Read and write**. Set the expiry to 1 year. Copy it. (This is a different token from `GH_PAT`, and it does **not** go into your repo secrets.)
2. Sign up at **cron-job.org** → **Create cronjob**:
   - **URL:** `https://api.github.com/repos/JXutar/Reels-Bot/actions/workflows/tick.yml/dispatches`
   - **Schedule:** every 30 minutes
   - **Advanced → Request method:** `POST`
   - **Advanced → Headers:** add these three:
     - `Authorization` = `Bearer <your token from step 1>`
     - `Accept` = `application/vnd.github+json`
     - `X-GitHub-Api-Version` = `2022-11-28`
   - **Advanced → Request body:** `{"ref":"main"}`
3. Save, then use **Test run**. A response of **204** means it worked, and a new *Tick* run appears in your Actions tab.

### 6e. Cloudflare (AI pictures for every scene, free)
Cloudflare's Workers AI gives everyone a free daily allowance. That's enough for well over 100 pictures a day with the FLUX.1 schnell model the bot uses. A 15-scene reel needs 15.
1. Sign up free at **dash.cloudflare.com** (no card needed).
2. **Account ID:** on the dashboard home, open **Workers & Pages** (or any account page). The **Account ID** is shown in the right-hand sidebar, and it's also the long code in the page URL after `dash.cloudflare.com/`. Save it as the secret `CLOUDFLARE_ACCOUNT_ID`.
3. **API token:** go to your profile icon → **My Profile → API Tokens → Create Token** → use the **"Workers AI"** template → **Continue → Create Token**. Copy it and save it as the secret `CLOUDFLARE_API_TOKEN`.
4. Run **Check setup**. It should say `AI pictures (Cloudflare): OK`.

If the free daily allowance runs out, or Cloudflare has a problem, scenes fall back to stock photos (Pexels/Pixabay) automatically.

### 7. Personalise and test
1. In `config/profiles/insurance.yaml`, set `handle:` to your Instagram handle. Change colours or posting days if you like.
2. **Actions → Check setup → Run workflow.** You should get a 👋 message on Telegram.
3. Send your bot `/idea short: what a deductible is`. Within ~30 minutes, the finished reel arrives on Telegram with 🚀 Post now / 🗓 Schedule / ❌ Reject buttons.

> **Trial reels need a minimum follower count** (Instagram's threshold is reported as somewhere between 200 and 1,000 for professional accounts). If your account doesn't qualify yet, posting will fail with an error on Telegram. Set `trial_reel: false` in the profile to post normal reels until it does.

---

## Making reels

### From an idea (the AI writes it)
- `/idea <your idea>`: uses the default profile (insurance). Example: `/idea why medical cards have a co-payment`
- `/idea story: <idea>` or `/idea general: <idea>`: picks the profile
- `/idea 60s: <idea>`, `/idea long: ...`, `/idea short: ...`: sets the length (15–90 seconds; default ~40)
- Mix them: `/idea story 75s: in this town nobody can say "I" or they turn to stone`
- `/idea story` on its own: the AI picks a topic

### From your own script (kept word for word)
Paste a script with `SCENE` / `VO:` / `Text:` / `Visual:` lines, either after `/idea` or `/script`. Every scene and every spoken line stays exactly as you wrote it, with no length cap (up to 25 scenes). The AI only adds a title, caption and hashtags, and turns your `Visual:` notes into picture prompts. It also keeps a recurring character (e.g. "Alex") looking the same across scenes as far as it can.
```
/script 28,000 DAYS
SCENE 1
Visual: A big counter spins up to 28,000
VO: "You'll live about 28,000 days. But you'll only get paid for half of them."
Text: 28,000 days
PAUSE 1.5
SCENE 2
...
SCENE 14
Visual: Simple end card
VO: (none)
Text: For general education only. Not financial advice.
```
- `PAUSE` (or `PAUSE 2`) adds a beat of silence after the scene above it.
- `VO: (none)` makes a silent scene: its `Text:` is shown big in the middle for 3 seconds (good for end cards).
- Header lines above `SCENE 1` (Length, Voice, Music) are ignored.
- Long scripts that Telegram splits into two messages are joined back together automatically.

### Reviewing and posting
Every finished reel arrives with three buttons:

| Button | What happens |
|---|---|
| 🚀 **Post now** | posts at the bot's next check (within ~30 minutes) |
| 🗓 **Schedule** | the bot asks "When?". Reply with `tomorrow 8pm`, `fri 9pm`, `28/11 20:00`, `in 3 hours`, `tonight`… |
| ❌ **Reject** | never posted; the temporary video copy is deleted |

Nothing posts unless you choose. Times are accurate to about 30 minutes (the bot wakes up every ~30 minutes).

### All Telegram commands
| Command | What it does |
|---|---|
| `/idea ...` / `/script ...` | make a new reel (see above) |
| `/status` | what's waiting, scheduled (with times), posted |
| `/post <id>` | post now |
| `/schedule <id> <when>` | schedule or reschedule, e.g. `/schedule deductible fri 9pm` |
| `/unschedule <id>` | cancel a scheduled post (the reel is kept) |
| `/remake <id>` or `/remake all` | re-render reels that aren't posted yet (e.g. after changing the style) |
| `/cancel` | stop the "When?" question |
| `/help` | list of commands |

**Ids can be shortened** to any unique part: `deductible` instead of `2026-11-01-deductible`.

You can also remake from GitHub: **Actions → Render new reels → Run workflow**, then type an id or `all` in the **Remake** box.

### Words the AI must never use
Each profile has a `banned_phrases:` list (e.g. "follow for", "check your policy", "policies differ"). If the AI writes one, it's asked to rewrite, and any sentence still containing one is removed. Add your own phrases to the list anytime. Your own `/script` lines are never changed.

### Automatic weekly posting (optional)
Set `posting: auto` in a profile to also post reels you've put on hold (via `/unschedule`) automatically on the `schedule:` days and hour, catching up within `catch_up_hours` if GitHub runs late. The default is `posting: manual`.

## Writing scripts with Claude (optional)
You can still ask Claude in your **Social media Posting** project for a batch of scripts and upload the JSON files to `content/queue/<profile>/<YYYY-MM>/`. Rendering starts automatically.

## Script format

You can write your own scripts in this format, ask Claude to write them, or mix both. Everything in the queue goes through the same Telegram review. To have Claude format your idea, just send your rough notes.

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
- `voice` is what's spoken; the subtitles come from it, word by word. Leave it empty (`""`) for a silent scene, and add `"hold": 3` for how many seconds to show it.
- `"visual"` *(per scene)*: description for the AI picture (ai_visuals style).
- `"pause_after"` *(per scene)*: seconds of silence after the scene.
- `text` is the scene's on-screen headline (keep it short; `*asterisks*` colour words).
- `"image"` *(optional, per scene)*: a pop-up picture while that scene is spoken. Use keywords for a stock photo (`"image": "hospital bill"`) or your own file (`"image": "file:my-chart.png"` from `content/assets/images/`).
- `"background"` *(optional, per script)*: stock-video search words for the background video (`"background": "hospital corridor"`), or one of your clips (`"background": "file:parkour1.mp4"`).
- `"video_style"` *(optional)*: `"slides"` to use the slideshow for just this reel.
- `style`: `hook` / `cta` (used by the slides style).
- The profile's disclaimer footer and default hashtags are added to every caption automatically.

---

## Video styles

Set `video_style:` in the profile.

**`ai_visuals` (default):** every scene gets its own AI picture (Cloudflare), framed in the middle with a slow zoom and pan, over a blurred, darkened copy of itself. Scenes crossfade, each scene's `text` appears as a headline above the picture, and word-by-word subtitles run underneath. The picture style for the whole profile is set in `visuals: style:`, so all scenes look like they belong together. The fallbacks are your own image (`"image": "file:..."`), then a stock photo, then a plain background.

**`background`:** a background video plays full screen (darkened so text stays readable), subtitles appear in the middle with the spoken word highlighted, and pop-up images bounce in above them.

Where the background comes from, in order:
1. The script's `"background": "file:..."` clip, if set.
2. **Your own clips** in `content/assets/backgrounds/`. Each reel picks one at random and starts from a random point, so a single 3–5 minute recording gives lots of variety. **Minecraft parkour:** record your own gameplay (Mojang allows this). Don't use other creators' videos; Instagram can mute or remove reels that reuse them. Keep files under ~90 MB each (GitHub's limit is 100 MB).
3. **Free stock video** (Pixabay), using the script's `"background"` words or the profile's `stock_query`.
4. A plain gradient, if none of the above.

To stop using your own clips without deleting them, set `use_own_clips: false` under `background:` in the profile. Adjust darkness with `dim:` and subtitle size with `caption_size:` under `style:`.

**`slides`:** the original slideshow, one designed slide per scene with a progress bar.

---

## Profiles included

| Profile | Use with | Style |
|---|---|---|
| `insurance` | `/idea ...` (default) or `/idea ins: ...` | concept lessons, friendly, "not financial advice" caption footer |
| `stories` | `/idea story: ...` | fiction with a twist, storyteller voice, cinematic pictures |
| `general` | `/idea general: ...` | any other topic: tips, explainers, events. No disclaimer |

All three use AI visuals, manual posting, and the same Instagram account by default.

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
- GitHub pauses its own schedule in repos with no activity for 60 days. cron-job.org keeps the bot running regardless.
- GitHub's hourly schedule can run a little late at busy times. That's normal.
- If anything fails, the bot messages you on Telegram with the error, and the details are under the repo's **Actions** tab.
- `state/state.json` is the bot's memory (what's approved and posted). The workflows update it, so don't edit it by hand.

## Project layout
```
bot/            the Python code (render, slides, tts, telegram, instagram, storage, cli)
config/         settings.yaml + one YAML per content profile
content/queue/  your scripts, one folder per profile, then per month
content/assets/ fonts (Inter, OFL licence), backgrounds/ (your clips), images/ (your pop-ups), optional music
state/          what has been sent, approved, posted
.github/        the four workflows
```
