# TelegramBot — চ্যানেল অটোপোস্ট বট

অন্য (সিমিলার) টেলিগ্রাম চ্যানেল থেকে **ইনস্পিরেশন** নিয়ে নিজের চ্যানেলে অটোমেটিক পোস্ট
করার একটি প্রোডাকশন-রেডি বট।

```
সোর্স চ্যানেল  →  ইউজারবট (Telethon) পড়ে  →  AI নতুন করে লেখে (বাংলায়)
              →  ডুপ্লিকেট/স্প্যাম ফিল্টার  →  আপনার অনুমোদন (✅)  →  বট চ্যানেলে পোস্ট করে
```

**মূল কথা:** এটা কপি-পেস্ট বট নয়। AI সোর্স পোস্টের *ভাব* বুঝে সম্পূর্ণ নতুন ভাষায় লেখে,
আর লেখাটা সোর্সের সাথে কতটা মিলেছে সেটা মেপে (similarity score) বেশি মিলে গেলে আবার
লিখিয়ে নেয়। ফলে কপিরাইট/রিপোস্ট ঝামেলা অনেক কমে।

---

## ১. কী কী ফিচার আছে

| ফিচার | বিবরণ |
|---|---|
| 🔍 সোর্স রিডিং | Telethon ইউজারবট দিয়ে যেকোনো public চ্যানেল পড়তে পারে (বট নয়, তাই সীমাবদ্ধতা নেই) |
| ✍️ AI রিরাইট | OpenAI / Gemini / OpenRouter / Groq / DeepSeek / লোকাল Ollama — যেকোনো OpenAI-compatible API |
| 🔁 অরিজিনালিটি গার্ড | রিরাইট সোর্সের সাথে `MAX_SOURCE_SIMILARITY` এর বেশি মিললে অটো রিট্রাই (প্রম্পট বদলে) |
| 🧹 ডুপ্লিকেট লেজার | একই খবর একাধিক চ্যানেলে এলে বা আগে পোস্ট হয়ে থাকলে বাদ যায় (trigram overlap) |
| 🚫 স্প্যাম ফিল্টার | `join our channel`, `@mention`, লিংক-only পোস্ট, ছোট পোস্ট, ব্লকড কীওয়ার্ড বাদ |
| 🖼 মিডিয়া | ছবি/ভিডিও ডাউনলোড করে ক্যাপশনসহ পোস্ট; বড় ফাইল স্কিপ; পোস্টের পরে ফাইল ডিলিট |
| ✅ রিভিউ মোড | প্রতিটা ড্রাফট আপনার DM-এ আসে — ✅ পাবলিশ / 🔄 রিরাইট / ✏️ এডিট / ❌ বাতিল |
| 🤖 অটো মোড | `AUTO_PUBLISH=true` করলে অনুমোদন ছাড়াই সোজা চ্যানেলে |
| ⏰ শিডিউলার | নির্দিষ্ট ইন্টারভালে স্ক্যান, দিনে সর্বোচ্চ লিমিট, নির্দিষ্ট সময়ে (৯টা–১১টা) পাবলিশ |
| 🎛 বট কমান্ড | `/status /run /queue /pause /resume /draft /publish /reject /regen /edit /channels` |
| 💾 SQLite | কোন পোস্ট দেখা হয়েছে, কোন ড্রাফট, কী পাবলিশ হয়েছে — সব ট্র্যাক করে (রিস্টার্টেও হারায় না) |
| 🧪 অফলাইন ডেমো | ক্রেডেনশিয়াল ছাড়াই পুরো পাইপলাইন চালিয়ে HTML রিপোর্ট দেখার সুযোগ |

---

## ২. দ্রুত শুরু (৫ ধাপ)

### ধাপ ০ — অফলাইন ডেমো দেখুন (কোনো অ্যাকাউন্ট লাগে না)

```bash
git clone <this-repo> && cd TelegramBot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/autopost demo
# → data/demo_preview.html ব্রাউজারে খুলুন
```

এটাই বটের আউটপুট ফরম্যাট — দুটো ডামি চ্যানেল থেকে ৬টা পোস্ট পড়ে, ২টা ফিল্টার করে,
৪টা ড্রাফট বানায়, আর দ্বিতীয় রানে দেখায় ডুপ্লিকেট লেজার কাজ করছে।

### ধাপ ১ — Telegram API আইডি নিন (ইউজারবটের জন্য)

1. https://my.telegram.org এ যান → আপনার ফোন নম্বর দিয়ে লগইন
2. **API development tools** → নতুন অ্যাপ বানান
3. `api_id` আর `api_hash` কপি করুন → `.env` এর `TG_API_ID`, `TG_API_HASH`

> ⚠️ এই সেশন আপনার *নিজের অ্যাকাউন্টের* মতোই ক্ষমতাধর। `.session` ফাইল/`TG_STRING_SESSION`
> কোথাও শেয়ার করবেন না, গিটে কমিট করবেন না। `data/` ও `.env` ইতিমধ্যেই `.gitignore`-এ আছে।

### ধাপ ২ — পাবলিশিং বট বানান

1. Telegram-এ **@BotFather** → `/newbot` → নাম + username → **টোকেন** নিন → `BOT_TOKEN`
2. আপনার চ্যানেল → **Administrators** → আপনার বট অ্যাড করুন → **Post messages** পারমিশন দিন
3. `TARGET_CHANNEL=@your_channel` (private চ্যানেল হলে `-100…` আইডি)
4. বটকে DM-এ `/start` দিন — নিজের numeric আইডি জানিয়ে দেবে → `OWNER_USER_IDS`

> বট চ্যানেলে অ্যাডমিন না হলে পাবলিশ ফেল করবে। `autopost check` এটা আগেই ধরে ফেলবে।

### ধাপ ৩ — AI কী নিন

`AI_PROVIDER` + `AI_API_KEY` + `AI_MODEL` বসান:

| প্রোভাইডার | `AI_PROVIDER` | `AI_MODEL` | `AI_BASE_URL` |
|---|---|---|---|
| OpenAI | `openai` | `gpt-4o-mini` | `https://api.openai.com/v1` |
| Gemini | `gemini` | `gemini-2.0-flash` | ডিফল্ট |
| OpenRouter | `openai` | `google/gemini-2.0-flash-001` | `https://openrouter.ai/api/v1` |
| Groq | `openai` | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` |
| লোকাল Ollama | `openai` | `qwen2.5:14b` | `http://localhost:11434/v1` |

বাংলা লেখার মানের জন্য `gpt-4o-mini`, `gemini-2.0-flash` বা `qwen2.5` ভালো কাজ করে।

### ধাপ ৪ — সোর্স চ্যানেল + ইউজারবট লগইন

`.env` এ:

```env
SOURCE_CHANNELS=@technews_bd,@world_digest,@your_niche_channel
```

তারপর একবার (একটা মেশিনে যেখানে OTP টাইপ করতে পারবেন):

```bash
.venv/bin/autopost login
```

নম্বর → OTP → (২FA থাকলে পাসওয়ার্ড) দিলে `data/userbot.session` তৈরি হবে।
সোর্স চ্যানেলগুলো পড়া যায় কি না সেটাও স্ক্রিপ্ট শেষে দেখিয়ে দেবে।

### ধাপ ৫ — যাচাই করে চালু করুন

```bash
.venv/bin/autopost check   # AI, বট পারমিশন, সোর্স, DB — সব চেক
.venv/bin/autopost run     # বট + শিডিউলার একসাথে
```

চালু হলে আপনার DM-এ ড্রাফট আসতে শুরু করবে। প্রথমে `AUTO_PUBLISH=false` রেখে কয়েকটা
পোস্ট নিজে approve করুন, মান ঠিক লাগলে `AUTO_PUBLISH=true` করুন।

---

## ৩. এটা আসলে কীভাবে কাজ করে

```
     ┌──────────────┐   get_messages    ┌──────────────┐
     │ সোর্স চ্যানেল │ ────────────────► │  SourceReader │  (Telethon, আপনার সেশন)
     └──────────────┘                   └──────┬───────┘
                                               │  ফিল্টার: বয়স, দৈর্ঘ্য, কীওয়ার্ড, মিডিয়া
                                               ▼
     ┌──────────────┐    prompt+retry   ┌──────────────┐
     │  LLM API     │ ◄──────────────── │  Pipeline    │  similarity > 0.45 হলে আবার লিখায়
     └──────────────┘ ────────────────► └──────┬───────┘
                                               │  ডুপ্লিকেট চেক (trigram overlap ≥ 0.75)
                                               ▼
     ┌──────────────┐   ড্রাফট + বাটন   ┌──────────────┐
     │  আপনার DM     │ ◄──────────────── │  SQLite      │
     └──────┬───────┘                   └──────┬───────┘
            │ ✅ approve (/publish)             │
            ▼                                  ▼
     ┌──────────────────────────────────────────────┐
     │  Bot API → আপনার চ্যানেলে পোস্ট (ছবি+ক্যাপশন) │
     └──────────────────────────────────────────────┘
```

প্রতি রানে যা হয়:

1. প্রতিটা সোর্স চ্যানেল থেকে সর্বশেষ ২০টা মেসেজ পড়া হয় (বয়স ফিল্টার সহ)
2. অলরেডি দেখা পোস্ট বাদ (`seen_posts` লেজার)
3. স্কোরিং: `views + 3 × forwards` — বেশি এনগেজমেন্টের পোস্ট আগে
4. ফিল্টার: ছোট টেক্সট, ব্লকড কীওয়ার্ড, শুধু লিংক, মিডিয়া না থাকা (যদি `REQUIRE_MEDIA`)
5. নিয়ার-ডুপ্লিকেট চেক (একই রানে ও গত ৯৬ ঘণ্টার ড্রাফটের সাথে)
6. AI রিরাইট → HTML কম্পোজ (bold হেডলাইন + বডি + হ্যাশট্যাগ + সিগনেচার)
7. `AUTO_PUBLISH=false` হলে DM-এ ড্রাফট + বাটন; `true` হলে সোজা পাবলিশ
8. দিনের কোটা, অ্যাক্টিভ আওয়ার, `pause` — সব মেনে চলে

---

## ৪. কনফিগ রেফারেন্স (.env)

পুরো তালিকা টীকাসহ `.env.example`-এ আছে। সবচেয়ে দরকারিগুলো:

| ভেরিয়েবল | ডিফল্ট | কাজ |
|---|---|---|
| `SOURCE_CHANNELS` | — | ইনস্পিরেশনের চ্যানেল (`@a,@b`) |
| `TARGET_CHANNEL` | — | আপনার চ্যানেল (`@name` বা `-100…`) |
| `OWNER_USER_IDS` | — | কে বট চালাতে পারবে (numeric id, কমা দিয়ে একাধিক) |
| `AUTO_PUBLISH` | `false` | `false` = প্রতিটা পোস্টের অনুমোদন লাগবে |
| `POLL_INTERVAL_MINUTES` | `30` | কত মিনিট পরপর স্ক্যান |
| `MAX_POSTS_PER_RUN` | `3` | প্রতি রানে সর্বোচ্চ পোস্ট |
| `DAILY_POST_LIMIT` | `12` | দিনে সর্বোচ্চ পাবলিশ |
| `MIN_SOURCE_AGE_MINUTES` | `10` | এত মিনিটের কম বয়সী পোস্ট নেবে না (চ্যানেলের জন্য পুরনো খবর এড়াতে) |
| `MAX_SOURCE_AGE_HOURS` | `72` | এর চেয়ে পুরনো পোস্ট নেবে না |
| `MIN_SOURCE_CHARS` | `120` | ছোট পোস্ট বাদ |
| `BLOCKED_KEYWORDS` | — | এই শব্দ থাকলে বাদ (`জুয়া,casino`) |
| `DUPLICATE_SIMILARITY_THRESHOLD` | `0.75` | same-story ডিটেকশনের সীমা (0.6 = কড়া, 0.9 = শিথিল) |
| `AI_PROVIDER` / `AI_MODEL` / `AI_API_KEY` | — | কোন মডেল দিয়ে লিখবে |
| `MAX_SOURCE_SIMILARITY` | `0.45` | কপি ডিটেকশন সীমা; বেশি মিললে আবার রিরাইট |
| `POST_LANGUAGE` | `bn` | `bn`, `en`, `hi` … |
| `CHANNEL_PERSONA` | — | চ্যানেলের টোন/ভ্যালু — প্রম্পটে ঢোকে |
| `STYLE_RULES` | — | একাধিক লাইনে কড়া নিয়ম, যেমন *"সব সংখ্যা বাংলা অঙ্কে"* |
| `MAX_POST_CHARS` | `900` | বডির সর্বোচ্চ দৈর্ঘ্য |
| `HASHTAG_COUNT` | `3` | কতটা হ্যাশট্যাগ |
| `SIGNATURE` | — | প্রতি পোস্টের শেষে, যেমন `📌 @my_channel` |
| `ACTIVE_HOURS_START/END` | — | শুধু এই ঘণ্টার মধ্যে পাবলিশ (যেমন 9–23) |
| `MEDIA_ENABLED` / `MEDIA_MAX_MB` | `true` / `45` | ছবি-ভিডিও আনবে কি না |
| `ALLOW_TEMPLATE_FALLBACK` | `false` | AI ফেল করলে সাধারণ টেমপ্লেটে পোস্ট করবে কি না |

> `STYLE_RULES` একাধিকবার লিখলে সবগুলো জমা হয়:
> ```env
> STYLE_RULES=প্রতিটি পোস্টে সর্বোচ্চ একটি ইমোজি
> STYLE_RULES=সংখ্যা বাংলা অঙ্কে লিখবে (১২৩)
> STYLE_RULES=শিরোনামে প্রশ্নবোধক চিহ্ন দেবে না
> ```

---

## ৫. বট কমান্ড ও বাটন

DM-এ বটকে পাঠান:

| কমান্ড | কাজ |
|---|---|
| `/status` | মোড, আজকের কোটা, পজ নাকি চালু, পেন্ডিং সংখ্যা |
| `/run` | এখনই একবার স্ক্যান করবে |
| `/queue` | অপেক্ষমাণ ড্রাফটের তালিকা |
| `/draft 12` | ওই ড্রাফট আবার দেখাবে |
| `/publish 12` | পাবলিশ করবে |
| `/reject 12` | বাতিল করবে |
| `/regen 12` | নতুন করে রিরাইট করাবে |
| `/edit 12` | এরপর পাঠানো টেক্সট দিয়ে ড্রাফট বদলাবে |
| `/pause` / `/resume` | অটোমেশন থামানো / চালু |
| `/channels` | সোর্স ও টার্গেট চ্যানেলের তালিকা |

প্রতিটা ড্রাফট কার্ডে বাটন: **✅ পাবলিশ · 🔄 রিরাইট · ✏️ এডিট · ❌ বাতিল**

`OWNER_USER_IDS` খালি থাকলে যে কেউ বট চালাতে পারবে — তাই এটা অবশ্যই ভরবেন।

---

## ৬. ডিপ্লয়মেন্ট

### অপশন A — VPS + systemd (সবচেয়ে সস্তা, সুপারিশকৃত)

```bash
sudo adduser --system --group autopost
sudo mkdir -p /opt/tg-autopost && sudo chown autopost: /opt/tg-autopost
# কোড কপি/ক্লোন করুন, তারপর:
cd /opt/tg-autopost
sudo -u autopost python3 -m venv .venv
sudo -u autopost .venv/bin/pip install .
sudo -u autopost cp .env.example .env   # মান বসান

# একবার লগইন (একই মেশিনে, ফাইল-সেশন তৈরি হবে)
sudo -u autopost .venv/bin/autopost login

sudo cp deploy/autopost.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autopost
journalctl -u autopost -f
```

### অপশন B — Docker Compose

```bash
cp .env.example .env      # মান বসান
mkdir -p data

# সেশন ফাইলটা হোস্টে বানিয়ে volume-এ রাখতে হবে:
docker compose run --rm -it autopost python -m autopost login

docker compose up -d
docker compose logs -f --tail=100
```

### অপশন B2 — GitHub Actions (সার্ভার ছাড়াই, ফ্রি)

`deploy/GITHUB_ACTIONS.md` দেখুন — পুরো ধাপে ধাপে গাইড। সংক্ষেপে:

1. `autopost login --string` → `TG_STRING_SESSION` নিন (ফাইল রাখা যায় না)
2. রিপো → **Settings → Secrets and variables → Actions**:
   **Secrets:** `TG_API_ID`, `TG_API_HASH`, `TG_STRING_SESSION`, `BOT_TOKEN`, `AI_API_KEY`
   **Variables:** `TARGET_CHANNEL`, `SOURCE_CHANNELS`, বাকিগুলো ঐচ্ছিক
3. **Actions → Autopost → Run workflow** (`dry_run=true` দিয়ে প্রথমে)

```bash
make session      # স্ট্রিং সেশন
make gh-secrets   # .env থেকে সব সিক্রেট/ভেরিয়েবল পুশ
make gh-run       # ম্যানুয়াল dry-run
```

> ⚠️ GitHub Actions-এ বট ২৪/৭ চলে না — তাই **✅ রিভিউ বাটন কাজ করবে না**,
> `AUTO_PUBLISH=true` বাধ্যতামূলক। রিভিউ মোড চাইলে VPS/Docker ব্যবহার করুন।
> ডুপ্লিকেট লেজার `autopost-state` ব্র্যাঞ্চে সেভ হয়, তাই রানগুলোর মধ্যে মনে থাকে।

### অপশন C — শুধু cron (বট কমান্ড ও বাটন ছাড়া)

`deploy/crontab.example` দেখুন — `autopost once` প্রতি ৩০ মিনিটে চালায়। এই মোডে
`AUTO_PUBLISH=true` লাগবে, নয়তো ড্রাফট শুধু DB-তে জমা হবে।

### হোস্টিং টিপস

- **ইউজারবট সেশন** ফাইল (বা `TG_STRING_SESSION`) ছাড়া সোর্স পড়া যাবে না — সেটা সিক্রেট।
- একই সেশন দুই জায়গায় চালাবেন না (Telegram লগআউট করে দিতে পারে)।
- `data/` ব্যাকআপ রাখুন — ডুপ্লিকেট লেজার সেখানেই।
- VPS-এর IP যদি Telegram ব্লক করে, তো ডেটাসেন্টার-ফ্রেন্ডলি রিজিয়ন বেছে নিন।

---

## ৭. কনটেন্ট নীতি ও আইনি দিক (গুরুত্বপূর্ণ)

- এই টুল **ইনস্পিরেশন** নেওয়ার জন্য, হুবহু কপি করার জন্য নয়। ডিফল্ট সেটিংস
  (`MAX_SOURCE_SIMILARITY=0.45`) কপি-পেস্ট আটকায়।
- সোর্স মিডিয়া (ছবি/ভিডিও) ব্যবহার করার আগে নিশ্চিত হন সেটার ব্যবহার আপনার চ্যানেলে
  বৈধ — প্রয়োজনে `MEDIA_ENABLED=false` করে শুধু টেক্সট পোস্ট করুন।
- সংবাদ/তথ্যের ক্ষেত্রে সবসময় মূল সূত্র যাচাই করুন। AI ভুল করতে পারে, তাই
  `AUTO_PUBLISH=false` দিয়ে শুরু করা এবং রিভিউ করা নিরাপদ।
- নিজের চ্যানেলের স্টাইল/রাইট সেট করুন `CHANNEL_PERSONA` + `STYLE_RULES`-এ — এতে
  আউটপুট আপনার ব্র্যান্ডের মতো শোনাবে, কারও কপি নয়।

---

## ৮. টেস্ট ও ডেভেলপমেন্ট

```bash
make install        # venv + deps + .env কপি
make demo           # অফলাইন ডেমো রিপোর্ট
make test           # ৩৪টি টেস্ট, কোনো নেটওয়ার্ক/ক্রেডেনশিয়াল লাগে না
make lint           # ruff
make once           # dry-run: কিছু পাবলিশ না করে দেখায় কী পোস্ট হতো
```

AI কল আসলে হয় কি না তা টেস্টেই মক করা আছে (`httpx.MockTransport`), তাই CI-তে
কোনো API key লাগে না।

```
src/autopost/
├── config.py      # .env → Settings (pydantic-settings)
├── models.py      # SourcePost / Rewrite / Draft
├── textutil.py    # ক্লিনিং, similarity, hashtag, HTML কম্পোজ
├── prompts.py     # সিস্টেম/ইউজার প্রম্পট ও রিট্রাই হিন্ট
├── ai.py          # OpenAI-compatible + Gemini ক্লায়েন্ট, JSON পার্স, অরিজিনালিটি লুপ
├── sources.py     # Telethon ইউজারবট, মিডিয়া ডাউনলোড
├── publisher.py   # Bot API পাঠানো (flood-wait, HTML fallback)
├── db.py          # SQLite: seen/drafts/publishes/kv
├── pipeline.py    # স্ক্যান → ফিল্টার → রিরাইট → কিউ/পাবলিশ
├── bot.py         # aiogram: রিভিউ কার্ড, বাটন, কমান্ড
├── service.py     # বট polling + শিডিউলার একসাথে
├── demo.py        # অফলাইন ডেমো
└── cli.py         # login / check / once / run / drafts / post / demo
```

---

## ৯. সমস্যা হলে

| সমস্যা | কারণ ও সমাধান |
|---|---|
| `userbot session is not authorised` | `autopost login` চালাননি, বা `data/userbot.session` মুছে গেছে |
| `Could not find the input entity for …` | চ্যানেলের username ভুল, বা চ্যানেল private — আগে ইউজারবট অ্যাকাউন্ট দিয়ে চ্যানেলে জয়েন করুন (একবার) |
| `the bot is not allowed to post there` | বটকে চ্যানেলের অ্যাডমিন বানান, **Post messages** পারমিশন দিন |
| `Telegram rejected the message: can't parse entities` | বট নিজেই plain-text fallback করে; তবু হলে `STYLE_RULES`-এ `<`/`>` ব্যবহার কমান |
| AI ব্যর্থ (`401`, `429`) | কী ভুল / কোটা শেষ; `AI_MAX_RETRIES` ও প্রোভাইডার মডেল নাম চেক করুন |
| পোস্ট আসছে না | `/status` দেখুন — পজ নয়তো? দিনের লিমিট শেষ? `MIN_SOURCE_AGE_MINUTES` বড়? |
| সব পোস্ট ফিল্টারে বাদ যাচ্ছে | `MIN_SOURCE_CHARS` কমান, `DUPLICATE_SIMILARITY_THRESHOLD` বাড়ান (0.85) |
| রিরাইট সোর্সের মতোই লাগছে | `MAX_SOURCE_SIMILARITY` কমান (0.35) আর `CHANNEL_PERSONA` আরও নির্দিষ্ট করুন |

লগ: `data/autopost.log` (ঘুরিয়ে রাখা হয়), বা `journalctl -u autopost -f`।

---

## ১০. রোডম্যাপ আইডিয়া

- [ ] ছবি না পেলে AI দিয়ে ব্যানার জেনারেট
- [ ] `/analytics` — কোন সোর্স থেকে আসা পোস্টে বেশি ভিউ
- [ ] একাধিক টার্গেট চ্যানেল (প্রতি চ্যানেলের আলাদা persona)
- [ ] Web dashboard (FastAPI + HTMX)
- [ ] সাপ্তাহিক ডাইজেস্ট পোস্ট

---

লাইসেন্স: MIT · তৈরি হয়েছে [Arena.ai](https://arena.ai) এ
