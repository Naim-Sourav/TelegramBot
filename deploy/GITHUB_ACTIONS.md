# GitHub দিয়ে ডিপ্লয় — ধাপে ধাপে

GitHub Actions দিয়ে বট চালানো **সম্ভব**, কোনো সার্ভার/VPS লাগে না। কিন্তু আগে
সীমাবদ্ধতাটা বুঝে নিন:

| | GitHub Actions | VPS / Docker |
|---|---|---|
| খরচ | ফ্রি (public repo) | মাসে ৩–৫ ডলার |
| ২৪/৭ প্রসেস | ❌ নেই — শুধু শিডিউলে জাগে | ✅ |
| ✅/🔄 রিভিউ বাটন | ❌ কাজ করবে না (বট তখন বন্ধ) | ✅ |
| পাবলিশিং | `AUTO_PUBLISH=true` বাধ্যতামূলক | রিভিউ বা অটো, দুটোই |
| সেটআপ | সহজ, শুধু সিক্রেট বসানো | মাঝারি |
| ন্যূনতম বিরতি | ৫ মিনিট (কিন্তু ফ্রি কোটা শেষ হয়ে যাবে) | যত খুশি |

> **সংক্ষেপে:** শুধু "নির্দিষ্ট সময়ে অটো পোস্ট" চাইলে GitHub Actions ঠিক আছে।
> আর আপনি চাইছিলেন বট DM-এ ড্রাফট পাঠিয়ে ✅ অনুমোদন নেবে — সেটার জন্য ২৪/৭ চলা
> হোস্ট (VPS বা Docker) দরকার, কারণ বাটনে ক্লিক করার সময় বটকে জাগ্রত থাকতে হয়।

---

## ধাপ ১ — রিপো জিনিসটা বোঝা

এই ফোল্ডারটাই GitHub রিপো হিসেবে পুশ করা আছে (`Naim-Sourav/TelegramBot`)।
`.github/workflows/autopost.yml` ফাইলটা দেখুন — এখানেই পুরো কাজটা লেখা।

প্রতি রানে যা হয়: কোড চেকআউট → পাইথন সেটআপ → `autopost once` → ডুপ্লিকেট
লেজার (sqlite) `autopost-state` নামের আলাদা ব্র্যাঞ্চে সেভ।

---

## ধাপ ২ — ⚠️ ফাঁস হওয়া ক্রেডেনশিয়াল বাতিল করুন

আপনি চ্যাটে যেগুলো দিয়েছিলেন সেগুলো **আর ব্যবহার করবেন না**:

**ক) বট টোকেন বাতিল**
Telegram → **@BotFather** → `/mybots` → আপনার বট → **API Token** → **Revoke current token**
→ নতুন টোকেন পাবেন।

**খ) api_hash বাতিল**
https://my.telegram.org → **API development tools** → অ্যাপটি **Delete** করে
নতুন অ্যাপ বানান → নতুন `api_id` ও `api_hash` পাবেন।

**গ) সেশন বাতিল (সবচেয়ে জরুরি)**
যদি ওই ছোট স্ট্রিংটা একটা `StringSession` বা পাসওয়ার্ড হয়, তবে সেটা দিয়ে যে
কেউ আপনার **পুরো টেলিগ্রাম অ্যাকাউন্ট** নিয়ন্ত্রণ করতে পারে। তাই:
Telegram মোবাইল অ্যাপ → **Settings → Devices** → অচেনা/পুরনো সেশন → **Terminate**।
২FA পাসওয়ার্ড হলে → **Settings → Privacy and Security → Two-Step Verification** → বদলান।

এগুলো করার পরই নিচের ধাপে যান।

---

## ধাপ ৩ — নতুন Session স্ট্রিং বানান

GitHub Actions-এ ফাইল রাখা যায় না (প্রতি রানে নতুন মেশিন), তাই **StringSession**
দরকার। আপনার নিজের কম্পিউটারে একবার:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# নতুন api_id / api_hash .env এ বসিয়ে:
.venv/bin/autopost login --string
```

নম্বর → OTP → (২FA থাকলে পাসওয়ার্ড) দিলে টার্মিনালে একটা লম্বা স্ট্রিং
(`1B...` দিয়ে শুরু, ৩০০+ অক্ষর) প্রিন্ট হবে। **এটাই `TG_STRING_SESSION`।**
এটা পাসওয়ার্ডের মতো — কোথাও পাবলিকলি পেস্ট করবেন না।

> কম্পিউটার না থাকলে? যেকোনো ফ্রি ক্লাউড শেল (GitHub Codespaces, Replit) থেকেও
> `autopost login --string` চালাতে পারেন।

---

## ধাপ ৪ — GitHub-এ Secrets বসান (গোপন মান)

রিপো → **Settings → Secrets and variables → Actions → Secrets → New repository secret**

| Name | Value |
|---|---|
| `TG_API_ID` | নতুন api_id (সংখ্যা) |
| `TG_API_HASH` | নতুন api_hash (৩২ অক্ষর) |
| `TG_STRING_SESSION` | ধাপ ৩-এ পাওয়া লম্বা স্ট্রিং |
| `BOT_TOKEN` | BotFather-এর নতুন টোকেন |
| `AI_API_KEY` | OpenAI / Gemini কী |

## ধাপ ৫ — Variables বসান (সাধারণ কনফিগ)

একই পেজে **Variables** ট্যাব → **New repository variable**:

| Name | উদাহরণ | আবশ্যক? |
|---|---|---|
| `TARGET_CHANNEL` | `@my_channel` | ✅ |
| `SOURCE_CHANNELS` | `@technews_bd,@world_digest` | ✅ |
| `AI_PROVIDER` | `openai` বা `gemini` | ঐচ্ছিক |
| `AI_MODEL` | `gpt-4o-mini` | ঐচ্ছিক |
| `CHANNEL_TITLE` | `আমার চ্যানেল` | ঐচ্ছিক |
| `CHANNEL_PERSONA` | `তরুণদের জন্য প্রযুক্তি ও ক্যারিয়ার` | ঐচ্ছিক |
| `SIGNATURE` | `📌 @my_channel` | ঐচ্ছিক |
| `STYLE_RULES` | `প্রতিটি পোস্টে সর্বোচ্চ একটি ইমোজি` | ঐচ্ছিক |
| `MAX_POSTS_PER_RUN` | `3` | ঐচ্ছিক |
| `DAILY_POST_LIMIT` | `12` | ঐচ্ছিক |
| `MEDIA_ENABLED` | `true` / `false` | ঐচ্ছিক |
| `BLOCKED_KEYWORDS` | `জুয়া,casino` | ঐচ্ছিক |

`gh` CLI থাকলে এক কমান্ডে সব:

```bash
cp .env.example .env    # মান বসান
./scripts/push_github_secrets.sh
```

---

## ধাপ ৬ — প্রথম রান (dry-run)

রিপো → **Actions** ট্যাব → বাম দিকে **Autopost** → **Run workflow** →
`dry_run` **true** রেখে রান দিন।

লগে দেখবেন কী কী পোস্ট হতো, কিন্তু কিছুই যাবে না। লেখার মান খারাপ লাগলে
`CHANNEL_PERSONA` / `STYLE_RULES` বদলে আবার dry-run করুন।

## ধাপ ৭ — আসল রান

**Run workflow** → `dry_run` **false**। চ্যানেলে পোস্ট চলে যাবে।
এরপর প্রতি ঘণ্টায় নিজে নিজেই চলবে (`cron: "0 * * * *"`)।

---

## ধাপ ৮ — খরচ ও কোটা

- **Public repo** → Actions মিনিট আনলিমিটেড, সম্পূর্ণ ফ্রি।
- **Private repo** → মাসে ২০০০ মিনিট। প্রতি রান ~১–২ মিনিট, ঘণ্টায় একবার =
  দিনে ২৪–৪৮ মিনিট → মাসে ~৭০০–১৫০০ মিনিট। **৩০ মিনিটের cron করলে কোটা শেষ
  হয়ে যেতে পারে** — তাই ডিফল্ট ঘণ্টায় রাখা হয়েছে।
- ৬০ দিন রিপোতে কোনো কমিট/অ্যাক্টিভিটি না থাকলে GitHub শিডিউল বন্ধ করে দেয়।
  মাঝে মাঝে Actions ট্যাব থেকে একবার ম্যানুয়াল রান দিলেই আবার চালু হয়।
- Actions-এর cron নির্ভুল নয় — কখনো ৫–২০ মিনিট দেরি হতে পারে। ব্রেকিং নিউজের
  জন্য ২৪/৭ হোস্ট ভালো।

---

## সমস্যা হলে

| লগে যা দেখছেন | কারণ |
|---|---|
| `SOURCE_CHANNELS variable সেট করা নেই` | ধাপ ৫ বাদ পড়েছে |
| `userbot session is not authorised` | `TG_STRING_SESSION` ভুল/মেয়াদোত্তীর্ণ — ধাপ ৩ আবার করুন |
| `bot is 'member' … must be an administrator` | বটকে চ্যানেলের অ্যাডমিন + Post messages দিন |
| `HTTP 401` (AI) | `AI_API_KEY` ভুল বা কোটা শেষ |
| `state restored` না দেখে `প্রথম রান` | স্বাভাবিক, প্রথম রানের পর `autopost-state` ব্র্যাঞ্চ তৈরি হবে |
| দুবার একই পোস্ট গেছে | একসাথে দুটো রান চলেছিল; `concurrency` সেট আছে কি না দেখুন |

---

## পরে ২৪/৭ চালাতে চাইলে

GitHub Actions থেকে VPS-এ যাওয়া সহজ — শুধু `AUTO_PUBLISH=false` করে
`deploy/autopost.service` ইনস্টল করুন (README-র ধাপ ১০)। তখন ✅ রিভিউ বাটনও
কাজ করবে, আর `data/userbot.session` ফাইল ব্যবহার করা যাবে (স্ট্রিং সেশনের দরকার নেই)।
