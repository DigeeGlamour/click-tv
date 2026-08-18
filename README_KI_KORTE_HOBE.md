# claude-solution-16 — কী করতে হবে

`CLICK_TV_SPORTS_CHANNEL_SYSTEM_UPDATED.md` A-to-Z single source of truth ধরে
**system / backend / scanner / data-model / playback** implement করা হয়েছে।
Card/UI redesign এই phase-এ **করা হয়নি** — সেটা পরের phase।

Base: `claude-solution-14-FIX` + `claude-solution-14-FINAL`-এর correction গুলো
(latest corrected behaviour wins) + current repository audit।

এই folder self-sufficient: original repo-র সাথে যা আলাদা সব এখানে আছে (৪৬টা project file + এই README)।

> **Correction pass (এই update):** `channels[]` হারানোর root cause fix, আসল
> output-এ multi-channel-এর প্রমাণ, Streamed test-mode end-to-end verify, আর
> তোমার সব channel source-এর audit। Card/UI **এখনও শুরু হয়নি**।

---

## ০. সবচেয়ে আগে — তোমার GitHub Actions fail করছে, কারণটা code নয়

Screenshot-এর `Validate scanner files` step exit 1 দিচ্ছে। কারণ খুঁজে পাওয়া গেছে:
**`config/` folder-এর ৫টা file GitHub-এ upload হয়নি।**

```
GitHub-এ আছে : config/settings.json  (আর config/sources/ folder)
GitHub-এ নেই  : config/sources.json          <-- CI-র প্রথম test -f এখানেই fail করে
                config/event-fixtures.json
                config/channel-aliases.json
                config/channel-categories.json
                config/header-profiles.json
```

CI script-এর লাইন `test -f config/sources.json` fail করে, তাই পুরো step exit 1।

**সমাধান:** এই folder-এর `config/` folder-টা upload করলেই ঠিক হয়ে যাবে — পাঁচটাই
এখানে দেওয়া আছে (`settings.json` এই round-এ বদলেছে, বাকি চারটে original-এর হুবহু
copy, শুধু missing বলে সাথে দেওয়া হল)।

আগের দুই README-তেও এটা লেখা ছিল: **`config/sources.json` কখনও মুছবে না।**

---

## ০ক. তোমার সব channel source audit করা হয়েছে

`config/sources.json`-এর **২৪টা source** একটা একটা করে live fetch করা হয়েছে
(`sourceaudit.json`-এ পুরো report)। ফল:

| Pipeline | মোট | ঠিক আছে | সমস্যা |
|----------|-----|---------|--------|
| tv | 11 | 10 | **1** |
| today_match | 6 | 1 | **5** |
| upcoming | 5 | 4 | **1** |
| movies | 2 | 2 | 0 |

**যেগুলোতে সমস্যা:**

| Source | Pipeline | কী হচ্ছে |
|--------|----------|----------|
| `sm-iptv-akash` | tv | **HTTP 404** — upstream repo থেকে `Akash.m3u` file মুছে গেছে |
| `sm-tapmad-auto` | today_match | HTTP 200, কিন্তু file নিজেই বলছে `Channels Count: 0` |
| `sm-tapmad-auto-blob-alias` | today_match | উপরেরটার হুবহু একই URL — তাই একই ভাবে খালি |
| `srhady-tapmad-bd-live` | today_match | HTTP 200, ০টা `#EXTINF` |
| `srhady-cricket-live-matches` | today_match | HTTP 200, file বলছে `Total Active Links: 0` |
| `sm-sonyliv-event-today` / `-upcoming` | today_match / upcoming | HTTP 200, ০টা entry (একই URL দুই pipeline-এ) |

**গুরুত্বপূর্ণ:** এগুলো আমাদের bug নয় — playlist গুলো আজ **19:00 BD time-এ
update হয়েছে এবং নিজেরাই খালি**। তাই Today Match-এ card কম, আর broadcaster
diversity প্রায় নেই। `srhady-cricket-live-matches` ঠিক সেই source যেটা আগে
"India vs Sri Lanka Willow" দিত — সেটা এখন খালি।

দুটো real duplicate config-ও ধরা পড়েছে: `sm-tapmad-auto` আর
`sm-tapmad-auto-blob-alias` একই URL, তেমনি `sm-sonyliv-event-today` আর
`sm-sonyliv-event-upcoming`। এগুলো কোনো ক্ষতি করছে না (dedupe ধরে ফেলে), কিন্তু
প্রতি scan-এ দুবার fetch হয়।

**যা করা উচিত:** `sm-iptv-akash`-এর নতুন URL জোগাড় করা বা `enabled: false` করা।
বাকিগুলো upstream-এ match এলে নিজেরাই ভরে যাবে — অপেক্ষা করা ছাড়া কিছু করার নেই।

### Deployed site-এর অবস্থা

| যা দেখা হয়েছে | ফল |
|---------------|-----|
| `clicktv.pages.dev` (browser User-Agent দিয়ে) | **200 — site চালু আছে** |
| non-browser User-Agent দিয়ে | 403, Cloudflare `error code: 1010` (browser-integrity check) |
| `data/manifest.json` deployed timestamp | **2026-08-17T09:35 UTC — পুরনো** |
| deployed `today-match.json` | **৫টা card, কিন্তু আসলে একই এক match** |

Deployed data পুরনো কারণ CI fail করছে (section ০)। আর ওই ৫টা card §1/§3/§5-এর
violation যা **এই মুহূর্তে production-এ live** —

```
Sri Lanka vs India 1st Test
India tour of Sri Lanka 2026 1st Test Sri Lanka vs India
Sri Lanka vs India
Day 3 1st Test 17 Aug 2026 | India Tour of Sri Lanka 2026
India vs Sri Lanka Willow
```

এর মধ্যে যে তিনটের participants পড়া যায়, সেই তিনটে এখন **একটা card** হয় (নিচে
section ২ক দেখো)। বাকি দুটোর title-এ participants পড়ার মতো কিছুই নেই
("Day 3 1st Test 17 Aug 2026") — সেটা upstream title-এর সমস্যা।

---

## ০খ. CI correction — missing file এখন নাম ধরে বলে

আগের `Validate scanner files` step ছিল `set -euo pipefail`-এর নিচে সারি সারি
`test -f`। প্রথম missing file-এ থেমে যেত আর log-এ লেখা থাকত শুধু
`Error: Process completed with exit code 1` — কোন file নেই সেটা বলত না। এই কারণেই
পাঁচটা config file খুঁজে বের করতে এত সময় লেগেছে।

এখন ৫৫টা required path একটা list-এ, সবগুলো check হয়, **প্রতিটা missing file নাম
ধরে report হয়** GitHub annotation সহ, তারপর exit 1:

```
::error title=Required files are missing from the repository::3 file(s) not found
  MISSING  config/sources.json
  MISSING  config/channel-aliases.json
  MISSING  config/header-profiles.json

These files exist locally but were never uploaded, or were deleted.
Upload them and re-run. Never delete config/sources.json.
```

Verify করা হয়েছে — workflow-এর bash block বের করে সত্যি চালিয়ে:

| যা করা হল | ফল |
|-----------|-----|
| as-is চালানো | `All 55 required files present.` exit **0** |
| ৩টা config file লুকিয়ে চালানো | তিনটেরই নাম + `::error` annotation, exit **1** |
| আবার restore করে চালানো | exit **0** |

List-এ নতুন করে যোগ হয়েছে যেগুলো আগে check-ই হত না: পাঁচটা missing config file,
চারটে নতুন scanner module, `live_protection.py`, `snapshot_publish.py`,
`targeted_scan.py`, `embed-player.css`, `sw.js`, আর দুটো নতুন test file।

> **মনে রাখো:** এই change-এর পরে CI **স্পষ্ট করে** ওই পাঁচটা config file-এর নাম
> বলে fail করবে, যতক্ষণ সেগুলো upload না করবে। সেটাই উদ্দেশ্য — চুপচাপ exit 1-এর
> বদলে সঠিক নির্দেশ।

---

## ১. নতুন architecture: Fixture/Event → Channels[] → Streams[]

```
event  (event_id)                      "Al Nassr vs Al Fateh"
  ├─ channel  "FANCODE"                primary + backups
  ├─ channel  "FOX DEPORTES"           primary + backups
  └─ channel  "SporTV"                 primary
       └─ stream variant               native playback_id  /  embed embed_url
```

### নতুন module

| File | কী করে |
|------|--------|
| `scanner/channel_resolver.py` | §11/§12/§34 — কোন feed কোন broadcaster, আর না জানলে **স্পষ্ট করে "জানি না"** |
| `scanner/channel_groups.py` | §6–§10, §19, §26, §27 — channel group, stream variant identity, primary/backup, failover order |
| `scanner/event_lifecycle.py` | §21 — `UPCOMING → STARTING → LIVE → END_PENDING → ENDED` decision layer |
| `scanner/streamed_provider.py` | §22–§25, §31–§33 — Streamed provider, additive only |

### Root cause যা ঠিক হয়েছে (§5)

**একই match তিনটে card হয়ে যাচ্ছিল।** Source playlist title-এর শেষে broadcaster
জুড়ে দেয়:

```
"Al Nassr Vs Al Fateh FANCODE"
"Al Nassr Vs Al Fateh FOX DEPORTES"
"Al Nassr Vs Al Fateh SporTV BR"
```

`normalize_event_key()` এগুলোকে **তিনটে আলাদা key** দিত → তিনটে main card। এখন
`fixture_identity_key()` আগে broadcaster-টা বাদ দেয়, তারপর key বানায় — তিনটেই
`al-nassr-vs-al-fateh`, একটাই card, তিনটে channel। আসল data-তে merged card
**41 → 36** নেমেছে, আর "Cpl T20" একটা card-এ **৪টে channel** নিয়ে এসেছে।

Truncation ইচ্ছে করেই conservative: `"Sky Blues vs Arsenal"` → key অপরিবর্তিত,
কারণ broadcaster শব্দ team নামের ভিতরেও থাকতে পারে।

### §7 stream variant identity

Effective playback config-এর SHA-256: final URL, DRM/ClearKey, licence, token/
query/expiry, cookie, referer, origin, user-agent, required headers, renderer।
তাই —

* একই config দুবার → duplicate, একটা বাদ
* token/cookie/referer/DRM আলাদা → **আলাদা variant**, backup হিসেবে থাকে

### §8 Willow-এর ৫টা entry

```
5 entries -> 2 exact duplicates removed -> 1 "Willow" channel
             primary + backup + backup
frontend-এ ৫টা Willow button নেই, একটাই Willow
```

### §9/§14 failover

Willow select করলে: `Willow primary → Willow backup 1 → Willow backup 2 → next
independent channel → তার backups`। Select না করলে scanner-এর channel order।
`_channel_lineage` এখন ঠিকভাবে কাজ করে (নিচে দেখো), তাই backup list এক channel-এর
৫টা variant দিয়ে ভরে না।

### §10 same channel, different match

`channel_id = "<event_id>--<normalized_name>"`। Match A-র Willow আর Match B-র
Willow কখনও merge হয় না — parent key সবসময় `event_id`। `Ten 1` আর `Ten 3`,
`Willow` আর `Willow 2` আলাদা feed হিসেবেই থাকে (feed number ইচ্ছে করেই রাখা হয়)।

### §11/§12 channel name resolver

Priority: explicit `channel_name` → `tvg-name` → source/provider metadata →
`group-title` (যখন সত্যিই channel-এর মতো) → alias map
(`config/channel-aliases.json`, ৪০টা alias) → cleaned stream title।

Cleaner বাদ দেয়: match/team names, `vs/v/versus`, Server 1/2/3, HD/FHD/UHD/4K,
LIVE, Backup, token noise, resolution label, date/year, Roman numeral, stray
initial।

**Confident না হলে fake নাম বানায় না** — `Unknown 1`, `Server X`, `Sports`,
`Cricket`, `Live Events`, `True` — কোনোটাই channel হবে না। তখন card স্বাভাবিক
event card-ই থাকে, channel bar থাকে না (§12)।

আসল ২৬৫টা candidate-এ: **৩৯টা resolve** হয় (Willow, FANCODE, TAPMAD, Fox
Cricket, beIN SPORTS, SKY SPORTS, TNT SPORTS, SporTV, CricGo, Star Sports 2,
Sony Sports Ten, Amazon), বাকি ২২৬টা honestly "unknown"।

### §13/§14 scanner default vs user selection

দুটো আলাদা state। Scanner default = event-এর primary যে channel-এ আছে।
User selection `state.channelSelection[event_id]`-এ pin হয়, localStorage-এ থাকে,
আর background scan অন্য channel-কে better rank করলেও **healthy playback
force-switch হয় না**।

`window.selectEventChannel(eventId, channelId)` — পরের Card/UI phase এটাই call
করবে। এখন কোনো নতুন visible UI যোগ করা হয়নি।

### §26–§30 dual renderer

`native` = existing HLS/DASH/DRM/Worker path, **সম্পূর্ণ অপরিবর্তিত**।
`embed` = provider iframe, existing player shell-এর **ভিতরে**।

* §27 native-first: embed কখনও channel primary হয় না, আর embed-only channel
  native channel-এর নিচে থাকে। Healthy native primary demote হয় না।
* §28 embed শুধু **সব native route fail করার পরে** চালু হয়
  (`handlePlaybackPlanExhausted`)। iframe `#videoContainer`-এর ভিতরে
  `position:absolute; inset:0` — shell-এর width/height/aspect-ratio/position
  একটুও বদলায় না। ফেরার সময় `src="about:blank"` করে remove, তাই stale audio/
  session থাকে না।
* §29 embed mode-এ native-only control (quality, network) disable হয়, **জায়গা
  ছেড়ে দেয় না** (layout shift নেই), native ফিরলে restore হয়।
* §30 iframe `load` = "renderer loaded", playback proof নয়। কোনো
  native↔embed flapping loop নেই।

### §21 LIVE end detection

`UPCOMING → STARTING → LIVE → END_PENDING → ENDED`। ENDED-এ পৌঁছানোর পথ মাত্র দুটো:

1. **strong end signal** — authority নিজে FT / FINISHED / ENDED / FINAL / AET /
   PEN / ABANDONED ইত্যাদি বলছে; অথবা
2. **multi-signal confirmation** — estimated end পার হয়েছে **এবং** primary +
   সব backup সত্যিই dead **এবং** পরপর ৩টে scan-এ কোনো live signal নেই।

Preserve করার চারটে স্বাধীন কারণ: authority এখনও LIVE, primary playable, কোনো
backup playable, অথবা **user এখন সেটা দেখছে** (সবচেয়ে strong)। শুধু scheduled/
estimated end পার হওয়া কখনও remove-এর কারণ নয়। Football extra time, Tennis-এর
লম্বা match, Cricket-এর rain delay / multi-day — সব preserve হয়।

Sport না জানা থাকলে duration estimate উদারভাবে ধরা হয় (cricket 8h, tennis 5h,
football 2.5h, default 4h) — over-estimate-এ কিছু হারায় না, under-estimate-এ card
হারায়।

**একটা গুরুত্বপূর্ণ finding:** carried-forward card তার আগের scan-এর লেখা
`LIVE_NOW` ধরে রাখে। ওটাকে "authority এখনও LIVE বলছে" ধরলে কোনো event কখনও
END_PENDING-এও যেত না। তাই authority verdict এখন **এই scan থেকে** আসে
(`_authority_states`), stale card থেকে নয়। Stale *finished* status অবশ্য মানা হয় —
শেষ হয়ে যাওয়া match আবার চালু হয় না।

### §22–§25, §31–§33 Streamed

Existing GitHub/native source = playback backbone। Existing fixture authority =
authority layer। Streamed = fixture metadata + artwork + last-resort embed।

* §23 Streamed-এর match id **কখনও** Click TV `event_id` হয় না — normalize করে
  existing canonical matcher-এ যায়, তারপর `provider_event_id` হিসেবে record হয়।
* §24 Streamed listing থেকে event উধাও হলেও remove/ENDED হয় না — §21 decide করে।
* §25 artwork priority: Streamed badge → Streamed poster → existing artwork →
  initials। Streamed fail করলে existing chain চলতে থাকে।
* §31 future fixture-এর জন্য stream endpoint continuously resolve হয় না — শুধু
  targeted-scan window-এ, fixture live হলে, বা explicit on-demand। ৯ ঘণ্টা পরের
  fixture-এ কোনো lookup হয় না।
* §32 timeout/error/malformed → provider unavailable mark হয়, candidate 0,
  existing snapshot অক্ষত। কোনো exception বাইরে যায় না।
* §33 embed publish হয় existing snapshot staging/validation-এর ভিতরেই; native
  `playback_id` resolution অপরিবর্তিত।

**`streamed_provider.enabled` default `false`** — guide বলছে Streamed additive,
তাই চালু করা deliberate সিদ্ধান্ত হওয়া উচিত, upgrade-এর side effect নয়।
`config/settings.json`-এ `base_url` দিয়ে `enabled: true` করলেই চালু।

---

## ২. পুরনো round-এর যে bug ধরা পড়েছে (এখন GitHub-এ live)

`scanner/merger.py`-তে **দুটো regex-এর word boundary (`\b`) literal backspace
character (0x08) হয়ে গিয়েছিল** — solution-14-এ ঢুকেছে, 14-FIX আর 14-FINAL হয়ে
এখন GitHub-এও আছে। Backspace খোঁজে বলে regex কিছুই match করত না, অর্থাৎ **দুটো
rule নীরবে বন্ধ ছিল**:

| জায়গা | কী হত | কোন requirement ভাঙত |
|-------|-------|---------------------|
| `_normalized_competition` | competition থেকে year strip হত না, তাই "Premier League 2026" ≠ "Premier League" | §1/§3 — একই fixture দুই group-এ থেকে যেতে পারত |
| `_channel_lineage` | path থেকে quality/server token strip হত না, তাই এক channel-এর দুই variant আলাদা channel মনে হত | §9 — backup list এক channel-এর ৫টা variant দিয়ে ভরে যেত |

দুটোই ঠিক করা হয়েছে, আর একটা test যোগ করা হয়েছে যা **যেকোনো** delivered
`scanner/*.py`-তে backspace byte থাকলে fail করবে, যাতে এটা আর ফিরে না আসে।

---

## ২ক. এই correction pass-এ যা ঠিক হয়েছে (channels[] root cause)

তোমার প্রশ্ন ছিল: **resolved broadcaster candidate গুলো fixture-authority
enrichment-এর আগে/মধ্যে filter হয়ে `channels[]` হারাচ্ছে কেন।** আসল scan output
(`working/bd-results.json`, ২৫৩টা real event candidate) ধরে stage-by-stage trace
করা হয়েছে। **চারটে আলাদা কারণ** পাওয়া গেছে, চারটেই ঠিক করা হয়েছে —
fixture authority, live-preservation, canonical identity বা কোনো solved
protection দুর্বল না করে।

### কারণ ১ (আসল root cause) — gate ঠিক ছিল, কিন্তু candidate-টা মুছে ফেলত

`enrich_event_candidates()` একটা **publish gate**: catalogue বা
fixture-authority ছাড়া কোনো stream-only playlist entry নিজে থেকে card হতে পারে
না। এটা ঠিক, এটাই রাখা হয়েছে। সমস্যা হল ওটা candidate-টাকে **delete-ও** করে
দিত। আর ঠিক যে entry গুলোর title-এ broadcaster থাকে সেগুলোই playlist থেকে আসে —
মানে `attach_streams_to_fixtures()`-এর ঠিক **এক stage আগে** সেগুলো মারা যেত, আর
সেই stage-টার একমাত্র কাজই হল fixture-এর সাথে stream জোড়া দেওয়া।

> Card আর তার channel — দুটোই একই scan-এ ছিল, কখনও পরিচয় হয়নি।

**Fix:** `enrich_event_candidates(..., attachment_pool=[...])`। Refuse হওয়া
stream-only candidate delete না হয়ে pool-এ যায়। Gate অপরিবর্তিত — pool-এর কিছুই
returned output-এ নেই। `attach_streams_to_fixtures()` pool-টা নেয়, আর একটা item
public হয় **শুধু তখনই যখন authority/catalogue-এর কোনো fixture তাকে দাবি করে**, আর
তখন সে ওই fixture-এরই identity, clock আর status পরে। Fixture না পেলে আগের মতোই
suppressed। `attached_from_suppressed_pool: true` দিয়ে audit করা যায়।

মানে: **card-এ channel যোগ হতে পারে, কিন্তু নতুন card কখনও তৈরি হতে পারে না।**
Pool না পাঠালে হুবহু আগের behaviour।

### কারণ ২ — দুই feed একই team-কে আলাদা নামে ডাকে

`team_pair_key()` exact বা prefix match চাইত। কিন্তু আসল data-য়:

```
authority feed  : "Baltimore Orioles vs Tampa Bay Rays"
playlist        : "Rays vs Orioles"                      <- উল্টো order, ছোট নাম
authority feed  : "Australia vs Bangladesh 2nd Test"     <- round পিছনে
playlist        : "1st Test Australia vs Bangladesh"     <- round সামনে
playlist        : "Braves vs Diamondbacks Quality"       <- "Quality" একটা link label
```

`"1st Test Australia"` → `"1st"`-এ কেটে যেত, তাই fixture আর stream কোনোদিন
মিলত না।

**Fix তিনটে, সবগুলো attach-এর key-তেই সীমাবদ্ধ:**

* সামনের round descriptor (`1st Test`, `5th ODI`, `20th Match`) বাদ
* পিছনের ordinal (`Bangladesh 2nd` → `Bangladesh`) বাদ — শুধু ordinal, তাই
  `Felgueiras 1932` অটুট থাকে
* `Quality` / `Link 1` / `Alt` / `Mirror` / `Backup` / `Feed` — link label, team নয়

আর matching-টা **order-insensitive + token-containment**: `Rays` মানে
`Tampa Bay Rays` হতে পারে, কিন্তু দুটো guard আছে — শেষ শব্দটা মিলতে হবে (তাই
`Sox` একসাথে `Boston Red Sox` আর `Chicago White Sox` হতে পারে না), আর **একাধিক
fixture একই stream-কে দাবি করলে কোনোটাতেই attach হয় না**।

### কারণ ৩ — team-এর নামকে broadcaster ভাবা হচ্ছিল (§12 violation, live ছিল)

`Guyana Amazon Warriors` একটা CPL team। `Amazon` strong brand list-এ আছে, তাই
resolver ওটাকে **broadcaster** ধরে নিত — এবং §5 key ওখানেই কেটে দিত:

```
আগে:  "Antigua and Barbuda Falcons vs Guyana Amazon Warriors"
       → card name  "Antigua and Barbuda Falcons vs Guyana"     <- ভুল team name!
       → §5 key     "antigua-and-barbuda-falcons-vs-guyana"     <- over-merge risk
```

**Fix:** brand-word-এর exemption এখন শুধু title-এর **শেষে** কাজ করে
(`_trailing_broadcaster_span`)। Brand-এর পরে যদি সাধারণ কোনো শব্দ থাকে
("Warriors"), তাহলে ওটা participant — broadcaster নয়। Region/language marker
(`Deportes`, `BR`, `ES`, `English`, `Xtra`) trailing হিসেবেই গোনা হয়, তাই
`FOX DEPORTES`, `SporTV BR`, `beiN ENGLISH`, `Willow Xtra` আগের মতোই resolve হয়।
`Sony Sports Ten 4` / `Star Sports 2`-ও পুরো নাম নিয়ে অটুট থাকে।

### কারণ ৪ — একই match কয়েকটা card, তাই channel গুলো ছড়িয়ে থাকত

`normalize_event_key` participant-এর order আর round-এর বানান ধরে রাখে, তাই:

```
"Sri Lanka vs India 1st Test"   -> sri-lanka-vs-india-1-test
"Sri Lanka vs India"            -> sri-lanka-vs-india
"India vs Sri Lanka Willow"     -> india-vs-sri-lanka
```

তিনটে আলাদা card, প্রত্যেকটায় অন্যটার stream নেই — যে broadcaster গুলোর একটাই
event-এর channel হওয়া উচিত ছিল, তারা তিন card-এ ভাগ হয়ে থাকত।

**Fix:** `participant_fold_key()` — broadcaster বাদ, round descriptor বাদ, দুই
side sorted। এটা **identity key নয়**, reconciler-এর কাছে একটা দুর্বল second
opinion; sport, competition আর kickoff-tolerance check আগের মতোই পাশ করতে হয়।
তাই একই দুই team-এর দুই তারিখের match এখনও দুটো card (test আছে)। Gender
key-তে আলাদা করে রাখা হয়েছে, তাই `Trent Rockets Women vs Oval Women` কখনও
`Trent Rockets vs Oval`-এ মেশে না।

**আসল data-য় ফল:** উপরের তিনটে spelling → **১টা card, ২টো backup**।

### কারণ ৫ (bonus) — event id-তে broadcaster ঢুকে যেত

Card id সবচেয়ে ভালো candidate থেকে আসত, আর playlist broadcaster-টা id-তেও
লিখে দেয়। তাই channel id হত:

```
আগে:  id                al-nassr-vs-al-fateh-sportv-br
      channels[].id     al-nassr-vs-al-fateh-sportv-br--fancode   <- FANCODE কি SporTV-র sub-feed?
এখন:  id                al-nassr-vs-al-fateh
      channels[].id     al-nassr-vs-al-fateh--fancode
```

`event_id_without_broadcaster()` — conservative, fixture না থাকলে কাটে না।

### §26/§27-এর দুটো defect যা নতুন test ধরেছে

1. Channel-এ `renderer` field-ই ছিল না। এখন প্রতিটা channel `native` / `embed` /
   `mixed` স্পষ্ট বলে।
2. **আসল bug:** native stream সুস্থ থাকলেও §12 যদি তার broadcaster-এর নাম না
   জানে, তখন `channels[]`-এ কোনো native entry থাকে না — আর তখন embed channel
   `default_channel_id` হয়ে যেত, যা playback plan reorder করে **native primary
   demote করত**। এটাই §27 নিষেধ করে। এখন card-এর নিজের native stream থাকলে
   default খালি রাখা হয়, embed-কে দেওয়া হয় না।

---

## ২খ. প্রমাণ — আসল output-এ একই event-এ কয়েকটা channel

তিন ভাবে verify করা হয়েছে, তিনটেই আসল scan data দিয়ে।

### প্রমাণ ১ — published payload-এ multi-channel (`publishedchanneltest.py`, PASS 23 / FAIL 0)

আজকের scan-এ যে fixture গুলোর **একাধিক live broadcaster** আছে:

```
Al Nassr Vs Al Fateh        FANCODE, FOX, SporTV
Cpl T20 Vs Cpl T20          SKY SPORTS, TAPMAD, TNT SPORTS, WILLOW
Sevilla Vs Rayo Vallecano   FANCODE, beiN ENGLISH
```

এগুলো authority feed-এও নেই, `config/event-fixtures.json`-এও নেই — তাই fixture
authority ঠিকভাবেই card publish করতে দেয় না, **আর সেটাই রাখা হয়েছে**। তাই test
একটা **scratch catalogue** বানায় (shipped file ছোঁয় না, পরে মুছে দেয়) যাতে
fixture-টা "জানা" হয়ে যায় — বাকি সব real: title, URL, header, verification
status, playback id। `process_events()` production-এর মতোই চলে:

```
today_match: Al Nassr Vs Al Fateh
  id                 al-nassr-vs-al-fateh
  lifecycle_state    LIVE
  channel_count      3
  default_channel_id al-nassr-vs-al-fateh--sportv
    - SporTV     renderer=native conf=explicit streams=1 verified=True
        primary  playback_id=ctv_dba2eb5537be6cf056ceb35ef20457a6  type=dash
    - FOX        renderer=native conf=explicit streams=1 verified=True
        primary  playback_id=ctv_962a3c521ef8a3ad8cbe8f2ba4bbad83  type=dash
    - FANCODE    renderer=native conf=explicit streams=1 verified=True
        primary  playback_id=ctv_ce4d09169653a3234ac0ca9e26f5a9e3  type=dash

today_match: Sevilla Vs Rayo Vallecano
  channel_count      2
    - beiN ENGLISH / FANCODE, দুটোই native + verified
```

প্রতিটা assertion pass: channel id unique · event id দিয়ে namespaced ·
default একটা published channel · প্রতিটা channel-এ অন্তত ১টা stream · প্রতিটা
stream-এ `playback_id` · প্রতিটা channel-এ renderer · কোনো invented নাম নেই
(সবগুলোর confidence আছে) · card নিজের native primary ধরে রেখেছে · shipped
`config/` দুটো file অপরিবর্তিত।

### প্রমাণ ২ — native channel layer, আসল record-এ (`nativechanneltest.py`, PASS 17 / FAIL 0)

`working/bd-results.json` থেকে verbatim record নিয়ে production
`merge_candidates()`:

```
cpl-t20-vs-cpl-t20         7 real record -> ১টা card, ৪টা channel
   TAPMAD / TNT SPORTS / WILLOW / SKY SPORTS   সব verified_global
   (FOX CRICKET, FANCODE, WILLOW ALT — link failed, তাই বাদ)
sevilla-vs-rayo-vallecano  3 real record -> ১টা card, ২টা channel
aus-vs-ban                 8 real record -> ০ card (সব link failed) <- ঠিক আচরণ
```

### প্রমাণ ৩ — pool mechanism, live data-য় (`process_events()`)

```
pooled_for_attachment  36     <- delete না হয়ে pool-এ গেল
pool_offered           36
pool_attached           1     <- "Rays vs Orioles" -> "Baltimore Orioles vs Tampa Bay Rays"
pool_unclaimed         35     <- কোনো fixture দাবি করেনি, তাই আগের মতোই suppressed
```

---

## ২গ. Streamed integration — configured test mode-এ end-to-end (PASS 32 / FAIL 0)

`streamedtest.py` একটা **আসল HTTP server** চালায় (আসল port, Streamed API-র আসল
shape), আর fixture হিসেবে **আজকের published card গুলোই** পরিবেশন করে — যাতে
canonical matcher-কে সত্যিই কাজ করতে হয়। তারপর `config/settings.json`-এ
provider on করে `process_events()` চালানো হয়, শেষে file হুবহু restore হয়।

```
provider health  {"available": true, "calls": 10, "errors": 0,
                  "fixtures_ingested": 4, "embed_streams": 8, "artwork_supplied": 4}
enrichment       {"matched": 4, "artwork": 4, "embed_backups": 8, "embed_channels": 8}

4টা real published card, প্রতিটায় channel_count = 2
```

**GitHub/native Primary অপরিবর্তিত — এটাই সবচেয়ে জরুরি assertion:**

| যা check করা হয়েছে | ফল |
|--------------------|-----|
| native primary + backups + playback id + lifecycle-এর SHA-256 fingerprint, provider off vs on | **হুবহু এক** |
| published card সংখ্যা | এক |
| embed channel সবসময় native channel-এর **পিছনে** (§27) | ✓ |
| working native primary থেকে default সরানো হয় না (§27) | ✓ |
| `backups[]`-এ কোনো embed URL ঢোকেনি (§27) | ✓ |
| provider off করলে output baseline-এ ফিরে যায় | ✓ |
| shipped `config/settings.json` byte-identical restore | ✓ |

Shipped config-এ `streamed_provider.enabled` **`false`** — test mode আলাদা,
production behaviour বদলায়নি। চালু করতে হলে `base_url` দিয়ে `enabled: true`।

---

## ২ঘ. event-fixtures.json data alignment — যা পাওয়া গেল

তুমি বলেছিলে alignment ঠিক করতে। করতে গিয়ে প্রথমে ভেবেছিলাম Test match-এর
`duration_minutes: 480` মানে পাঁচ দিনের Test আট ঘণ্টায় শেষ ধরা হচ্ছে, তাই দিন-২
থেকে সব broadcaster refuse হচ্ছে। **এই অনুমান ভুল ছিল।** ফাইলটা যাচাই করে দেখা
গেল প্রত্যেক Test fixture-এর নিজের explicit `end` আগে থেকেই আছে, আর
`load_fixtures()` সেটাই ব্যবহার করে:

```
Australia vs Bangladesh 1st Test    08-13 00:30 -> 08-17 08:30 UTC   4.3 দিন
Australia vs Bangladesh 2nd Test    08-22 00:00 -> 08-26 08:00 UTC   4.3 দিন
Sri Lanka vs India 1st Test         08-15 04:30 -> 08-19 12:30 UTC   4.3 দিন  live=True
Sri Lanka vs India 2nd Test         08-23 04:30 -> 08-27 12:30 UTC   4.3 দিন
```

`Sri Lanka vs India 1st Test` আজ (Day 3) **ইতিমধ্যেই live** হিসেবে ধরা পড়ছে।
CPL-এর ১৩টা fixture-ও Wikipedia-র official schedule-এর সাথে মিলিয়ে দেখা হয়েছে —
তারিখ আর team pairing সব ঠিক।

**তাই catalogue-এ কিছু বদলানো হয়নি — `config/event-fixtures.json` byte-identical
আছে।** যা ইতিমধ্যে ঠিক, সেটা "align" করতে গিয়ে নাড়াচাড়া করা বিশুদ্ধ ক্ষতি।

### আজ multi-channel না আসার আসল কারণ (প্রতিটা যাচাই করা)

আজকের feed-এ যে ৪টা fixture-এ একাধিক broadcaster আছে, প্রত্যেকটার পিছনের match
আসলে **শেষ হয়ে গেছে বা অস্তিত্বই নেই**:

| Fixture | Broadcaster | আসল অবস্থা | যাচাই |
|---------|-------------|-----------|-------|
| Al-Nassr vs Al-Fateh | FANCODE, FOX DEPORTES, SporTV | **15 Aug-এ শেষ**, Al-Nassr 3-0 জিতেছে | Saudi Pro League MD1, 15 Aug 2026 21:00 Riyadh |
| Sevilla vs Rayo Vallecano | FANCODE, beiN ENGLISH, PREMIER SPORTS | **15 Aug-এ শেষ** | LaLiga opener, 15 Aug 2026 19:30 UTC |
| 1st Test Australia vs Bangladesh | Fox Cricket, Star Sports 1, Willow, Willow Cricket | **16 Aug-এ শেষ**, Bangladesh 9 wicket-এ জিতেছে | Darwin Test, scheduled 13–17 Aug, চার দিনে শেষ |
| Cpl T20 Vs Cpl T20 | TAPMAD, SKY SPORTS NZ, TNT SPORTS, WILLOW | **17 Aug-এ কোনো CPL match নেই** | CPL 2026: Match 9 = 16 Aug, Match 10 = 18 Aug |

অর্থাৎ playlist গুলো **দুদিনের পুরনো entry** আর একটা placeholder title নিয়ে বসে
আছে। Fixture authority এগুলো refuse করছে — এবং সেটাই **ঠিক**। এগুলোকে publish
করাতে গেলে হয় শেষ হওয়া match live দেখাতে হবে, নয় নেই-এমন match-এর জন্য fixture
বানাতে হবে। দুটোই তোমার নিষেধ করা জিনিস, আর দুটোই solved protection ভাঙে।

### বদলে যা করা হয়েছে: catalogue-টা আর নীরবে ভাঙতে পারবে না

`tests/test_event_fixture_catalogue.py` (**১৮টা test, নতুন**)। Fixture authority-র
data ভুল হলে কোনো error হয় না — শুধু card আসা বন্ধ হয়ে যায়, চুপচাপ। এই test গুলোই
সেই নীরবতা ভাঙে:

* প্রতিটা fixture-এর `end > start`, ৩০ মিনিটের কম নয়, ৭ দিনের বেশি নয়
* **Test format-এর fixture-এ explicit `end` থাকতেই হবে আর window একদিনের বেশি
  হতেই হবে** — যে defect-এর ভয় পেয়েছিলাম, সেটা আর ফিরতে পারবে না
* one-day format-এ multi-day window থাকতে পারবে না
* competition id unique, fixture id unique, timezone resolve হয়, `source_url` আছে
* alias bare sport/category হতে পারবে না (`cricket`, `sports`, `live tv`), ৪ অক্ষরের
  কম হতে পারবে না, আর এক competition-এর alias অন্য competition-এর fixture claim
  করতে পারবে না
* **placeholder fixture ঢুকতে পারবে না** — `"Cpl T20 Vs Cpl T20"`-এর মতো দুই পাশে
  একই নাম, বা যার participants পড়াই যায় না

Test গুলো সত্যিই কাজ করে কিনা mutation দিয়ে যাচাই করা হয়েছে — catalogue-এ ইচ্ছে
করে defect ঢুকিয়ে:

| ইচ্ছাকৃত defect | Test ধরল? |
|-----------------|-----------|
| Test fixture থেকে explicit `end` সরিয়ে দেওয়া | **হ্যাঁ** — `test_a_multi_day_format_spans_more_than_one_day` |
| `"Cpl T20 Vs Cpl T20"` placeholder যোগ করা | **হ্যাঁ** — `test_no_fixture_names_the_same_side_twice` |
| fixture-এর end start-এর আগে বসানো | **হ্যাঁ** — ২টা test |
| alias হিসেবে `cricket` যোগ করা | **হ্যাঁ** — `test_no_alias_is_a_bare_sport_or_category` |

প্রথমবার placeholder test-টা **ধরতে পারেনি** (team_pair_key competition-শব্দ কেটে
`""` ফেরত দিত, তাই ওই entry skip হয়ে যেত)। raw title-এর দুই পাশ সরাসরি মিলিয়ে
ঠিক করা হয়েছে, তারপর mutation দিয়ে আবার যাচাই।

---

## ২ঙ. Carried card duplicate — one real match, one card

গত pass-এ যে gap নাম ধরে বলে রেখেছিলাম, সেটাই এই pass-এ fix হয়েছে।

```
আগে (আসল published output):
  Sri Lanka vs India 1st Test    LIVE          5 backup   (Amazon DASH)
  India vs Sri Lanka Willow      END_PENDING   5 backup   (playerr03 HLS)
  ^ একই Test match, দুটো card, প্রত্যেকটায় অন্যটার stream নেই

এখন:
  Sri Lanka vs India 1st Test    LIVE          5 backup + Willow channel (6 stream)
```

### কারণ

`protect_live_events()` merge শেষ হওয়ার **পরে** চলে। তাই carried card কখনও §1/§3-এর
grouping-এর ভিতর দিয়ে যায় না — fold key, kickoff tolerance, §5 কিছুই ওকে দেখে না।
`Sri Lanka vs India 1st Test` আর `India vs Sri Lanka Willow` তাই আলাদা দুটো card
হয়ে থেকে যায়।

**প্রথম চেষ্টায় fix কাজ করেনি, আর সেটা জরুরি একটা জিনিস শিখিয়েছে।** ভেবেছিলাম
shape হল "fresh card-এর পাশে carried card", তাই এই scan-এর output-এ host খুঁজেছিলাম।
Trace করে দেখা গেল আসল shape আলাদা:

```
protect_live_events: today=15  previous=24
  carried candidate: India vs Sri Lanka Willow
  (এই scan-এর 15টা card-এ Sri Lanka vs India কোথাও নেই)
  stats: reconciled_into_canonical=0   carried_forward=9
```

Live playlist খালি থাকলে **দুটো card-ই carried** — fold করার জন্য কোনো fresh card
থাকেই না। তাই reconciliation carried set-এর **ভিতরেই** হতে হয়। Fix restructure
করে decision loop-এর পরে একটা আলাদা phase করা হয়েছে।

### কোনটা canonical, সেটা §5 ঠিক করে

`_canonicalness()` — যে title-এ broadcaster **নেই** সেটাই match-এর নিজের নাম,
আর যেটায় আছে সেটা channel-এর দেওয়া নাম:

```
Sri Lanka vs India 1st Test  -> broadcaster resolve হয় না -> canonical, রাখা হয়
India vs Sri Lanka Willow    -> "Willow" resolve হয়      -> fold হয়ে যায়
```

Tie হলে LIVE > END_PENDING, তারপর বেশি link। দুটো card যে ক্রমেই আসুক ফল একই
(test আছে)।

### চারটে জিনিস একসাথে ধরে রাখা হয়েছে

| দাবি | কীভাবে |
|------|--------|
| **Event হারায় না** | reconcile মানে release নয় — event canonical card-এর নিচে থাকে; `released_*` সব শূন্য থাকে |
| **Currently-playing session ছোঁয়া হয় না** | যে card দেখা হচ্ছে সেটা **কখনও** fold হয় না; নিজের card, নিজের primary, নিজের backup নিয়ে থাকে (`reconciled_playing_kept_separate`) |
| **Playable stream হারায় না** | carried card-এর proven primary canonical card-এর **Backup-1** হয়, অর্থাৎ canonical primary-র ঠিক পরেই try হয়; আর ৬টা stream-ই Willow channel-এ থাকে |
| **Canonical primary demote হয় না** | primary, `url`, `primary_stream_key`, `lifecycle_state` — কিছুই বদলায় না; শুধু backup যোগ হয়। `default_channel_id` খালি রাখা হয় (§27-এর একই যুক্তি) |

আর `absorbed_event_ids: ["india-vs-sri-lanka-willow"]` — event-id continuity, যাতে
পুরনো id-তে pin/bookmark করা কিছু এখনও resolve করা যায়।

### আসল published output (fresh Today scan-এর পরে)

```
name                  : Sri Lanka vs India 1st Test
id                    : sri-lanka-vs-india-1st-test          <- canonical id অটুট
lifecycle_state       : LIVE                                  <- END_PENDING-এ নামেনি
playback_id (primary) : ctv_8d0a51546296fb1f966aca4a484c8ce6  <- আগের primary
available_link_count  : 6
   Backup-1  ctv_f9a2f0d9018810d744cefc229582b5b2             <- Willow-এর proven primary
   Backup-2  ctv_07a84971d1b51b07d4614ab80c97b32e  mz02.playerr03.com
   Backup-3  ctv_7976b23246d5a04ad85ecde26fd2565f  mz01.playerr03.com
   Backup-4  ctv_833aebf4f4b3e6d16c691847dc04661a  mz01.playerr03.com
   Backup-5  ctv_70e82686a0be536f4e995dbc33b97e16  mz01.playerr03.com
channel_count         : 1
   Willow  id=sri-lanka-vs-india-1st-test--willow  renderer=native  streams=6  conf=derived
absorbed_event_ids    : ['india-vs-sri-lanka-willow']
default_channel_id    : ''                                    <- native primary demote হয়নি
```

Today card **24 -> 23**, আর ওই match-এর card **2 -> 1**।

### আরেকটা bug যা নিজের test ধরেছে

প্রথম version carried card-টা **পুরোটা** backup slot-এ copy করছিল — ৪৩টা field,
তার ভিতরে card-এর নিজের `name`, `id`, `lifecycle_state`, এমনকি nested `backups`
list পর্যন্ত। এখন published backup-এর ঠিক যে ১৯টা field থাকে সেগুলোই নাম ধরে
copy হয় (`_BACKUP_FIELDS`), আর test দুটো assert করে: absorbed backup-এ card-এর
field leak হয় না, আর কোনো absorbed stream-এ `url`/`headers` publish হয় না (§17)।

---

## ২চ. Card/UI phase — CLICK_TV_SPORTS_CHANNEL_CARD_DESIGN_UPDATED.md

Backend আগের মতোই আছে। এই phase-এ শুধু Today/Upcoming card area আর channel
selector interaction — design file-এর ২১টা section ধরে।

### Root cause / previous limitation

`channels[]` publish হচ্ছিল, selection state আর playback plan-ও তৈরি ছিল
(`state.channelSelection`, `activeChannelId()`, `channelStreamOrder()`,
`orderSourcesByChannel()`) — কিন্তু **card-এ channel দেখানোর কোনো UI ছিল না**।
`createEventCard()` channel-এর কথাই জানত না। তাই viewer একটা event-এর একাধিক
broadcaster দেখতেও পেত না, বেছেও নিতে পারত না; plumbing পুরোটা অদৃশ্য ছিল।

### সবচেয়ে বড় constraint: §2 hard lock কীভাবে রাখা হল

Main event row hard-locked — `152px`, তিন column, প্রতিটা element বসানো — আর
`cardtest.mjs` assert করে **সব card-এর height সমান**। §4 বলে selector main
row-এর **নিচে** বসবে। দুটো একসাথে রাখার একমাত্র সৎ উপায়: row-টা **ছোঁয়াই না**।

```
.event-card-shell              <- list যেটা layout করে (নতুন)
  .event-ref-card              <- অপরিবর্তিত, এখনও 152px, এখনও uniform
  .event-channel-strip         <- নতুন, channel resolve হলেই তবে আসে
```

Strip row-এর **sibling**, child নয়। তাই row-এর geometry এক pixel-ও বদলায়নি,
uniform-height assertion আগের মতোই pass করে, আর card শুধু তখনই লম্বা হয় যখন
সত্যিই channel আছে। নতুন CSS file আলাদা রাখা হয়েছে (`event-channel-cards.css`)
আর সবগুলো rule `.sidebar-section.event-list-mode`-এ scoped — player, header,
sidebar width, navigation, player controls কোনোটার নামই ওই file-এ নেই, আর
একটা hard-lock test সেই অনুপস্থিতি assert করে।

### Requirement-by-requirement audit

| § | Requirement | আগে | এখন |
|---|-------------|-----|-----|
| 1 | one real match = one main card | **Existing** (backend fold + carried-card reconciliation) | অপরিবর্তিত, UI shell এক card |
| 2 | player/layout/header/sidebar/nav/controls hard lock | **Existing** | **preserved** — sibling shell, scoped CSS, 3টে hard-lock test |
| 3 | main row: rank · artwork · sport · title · competition · status · time · action | **Existing** | অপরিবর্তিত |
| 4 | channel selector area, desktop 2–4/row, mobile 2/row | **Missing** | **Implemented** — auto-fit grid, `data-columns` cap 4, mobile `repeat(2,…)` |
| 5 | icon/logo/initial + name + `1 Primary` `2 Backups` optional `2 Dupes removed` | **Missing** | **Implemented** — `channelChipSummary()` roles থেকে, `dropped_variant_count` থেকে dupes |
| 6 | selected channel clearly highlighted, click = select/play | **Partial** (state ছিল, UI ছিল না) | **Implemented** — `.is-selected` + theme green + `aria-pressed` |
| 7 | click → group selected → Primary → Backups | **Partial** (plan ছিল) | **Implemented** — chip click `selectEventChannel()`, plan reorder, কোনো reload নয় |
| 8 | playing event lightweight highlight + selected channel state | **Partial** | **Implemented** — `.is-playing-event` + chip equaliser |
| 9 | unknown channel → no selector, no fake name | **Missing** | **Implemented** — `channels.length < 1` হলে strip-ই render হয় না |
| 10 | artwork safe-area / contain / VS / initials fallback | **Existing** | অপরিবর্তিত; chip logo-তেও একই `object-fit:contain` |
| 11 | Today example | **Missing** | **Implemented** (§20 test-এ ১/৪/৮ channel) |
| 12 | different match same channel → আলাদা | **Existing** (§10 event-namespaced id) | UI-তেও আলাদা shell |
| 13 | Upcoming row + Bangla countdown + BDT + no fake bar | **Partial** | **Implemented** — countdown/BDT আগেই ছিল, খালি box আর আসে না |
| 14 | responsive, no overflow, 2-line title, ellipsis, tap target | **Missing** (selector-এর জন্য) | **Implemented** — ৮ viewport-এ test |
| 15 | visual hierarchy | **Missing** | **Implemented** — chip font/colour secondary |
| 16 | no DRM/token/cookie/header/URL | **Existing** (backend) | **UI-তেও enforced** — chip builder technical field পড়েই না, test assert করে |
| 17 | Smart Filter preserve, event+channels একসাথে hide | **Existing** | **preserved** — strip shell-এর ভিতরে, filter shell সরায় |
| 18 | keyed refresh, playing card/selection preserve | **Partial** | **Implemented** — shell keyed, playing node reuse, `pruneStaleChannelSelections()` |
| 19 | demo reference, blindly copy নয় | — | production theme/dimension-এ concept |
| 20 | ১৮টা validation case | **Missing** | **Implemented** — `channeluitest.mjs`, **388 pass** |
| 21 | embed fallback UI compat, provider-agnostic | **Existing** (embed layer) | **preserved** — card-এ কোনো renderer label নেই, test assert করে |

### Exact changed / new files

| File | কী |
|------|-----|
| `site/assets/css/event-channel-cards.css` | **নতুন** — ২৫৭ লাইন, shell + strip + chip + responsive |
| `site/assets/js/app.js` | **বদলেছে** — chip builder, strip render, click binding, in-place state, keyed reconcile, football league recognition |
| `site/index.html` | **বদলেছে** — নতুন CSS link, asset version `20260818-event-channel-cards-v1` |
| `site/sw.js` | **বদলেছে** — `CACHE_VERSION` v30, নতুন CSS precache |
| `tests/test_event_channel_card_design.py` | **নতুন** — ৩৬টা contract/hard-lock test |
| `tests/test_final_design_contract.py` | **বদলেছে** — version pin lockstep |

### Final card structure

```
.event-card-shell[data-event-shell][data-uid]         (channel থাকলে)
├── .event-ref-card                                   152px, hard-locked, অপরিবর্তিত
│   ├── .sidebar-channel-num                          serial
│   ├── .event-card-art                               logo / VS initials / sport fallback
│   ├── .event-card-details                           title · competition · status · time
│   └── .event-card-action                            Watch / Playing / Details
└── .event-channel-strip[data-columns="1..4"]
    └── .event-channel-chip[data-channel-id]  ×N      <button>
        ├── .event-channel-chip-icon                  logo বা initials
        ├── .event-channel-chip-name                  channel.name, ellipsis
        └── .event-channel-chip-sub                   1 Primary · 2 Backups · 2 Dupes removed
                                                      + equaliser (playing হলে)
```

Channel না থাকলে shell-ই তৈরি হয় না — bare `.event-ref-card` return হয়, একদম আগের মতো।

### Channel selection behaviour

* Default = `default_channel_id`; viewer বেছে নিলে সেটাই — selection
  `localStorage`-এ per-event রাখা হয়।
* Chip click → `selectEventChannel()` → plan ওই channel-এর Primary দিয়ে শুরু,
  fail করলে **ওই channel-এরই** Backup, তারপর পরের channel। কোনো catalogue fetch
  নেই, list rebuild নেই, page reload নেই (test assert করে)।
* Chip-এর click card-এর play handler-এ **bubble করে না** — strip আলাদা control
  surface, তাই strip-এর padding-এ click করলেও default channel-এ playback restart
  হয় না।
* Channel list বদলালে **healthy selection থেকে যায়**; শুধু যে channel সত্যিই
  চলে গেছে তার selection retire হয় (খালি list retire করার প্রমাণ নয়)।

### Native / embed behaviour

Card **provider-agnostic**। Chip-এ `native`/`embed`/`streamed` কোনো label নেই,
কোনো provider নাম নেই — viewer শুধু match + channel বাছে, renderer backend/player
ঠিক করে। Embed fallback active হলে player-এর width/height/aspect/position
অপরিবর্তিত, card dimension বদলায় না, selected event/channel state থাকে,
native-only control disable হয় (box রেখে, তাই layout jump নেই) আর native-এ
ফিরলে restore হয় — `embedtest.mjs` **38 pass**।

### Desktop / mobile behaviour

| Viewport | Chip/row | বিশেষ |
|----------|----------|-------|
| ≥1000px | auto-fit, সর্বোচ্চ **4** | chip 42px min-height |
| ≤1000px | **2** | chip 44px min-height |
| ≤480px | **2** | icon 22px, ছোট font |
| ≤380px | **2** | dupe note লুকায় (সবচেয়ে কম গুরুত্বপূর্ণ) |
| landscape ≤500px উঁচু | **2**, এক লাইন | summary লুকায়, 40px tap target |

৮টা viewport-এ verified: horizontal overflow নেই, title ≤2 line, channel name
ellipsis, chip strip-এর ভিতরে, player resize হয় না।

> **একটা real CSS bug নিজের test ধরেছে:** desktop-এর `[data-columns="4"]` rule
> class+attribute selector, আর mobile rule ছিল শুধু class — specificity-তে
> desktop জিতে যাচ্ছিল, তাই **phone-এ ৪টা chip এক row-তে** বসছিল। Mobile
> rule-গুলোতেও attribute selector যোগ করে ঠিক করা হয়েছে।

---

## ৩. Deploy order (এই ক্রমেই upload করবে)

**১. `config/` (সবচেয়ে আগে — CI এখানেই fail করছে)**
`channel-aliases.json` · `channel-categories.json` · `event-fixtures.json` ·
`header-profiles.json` · `settings.json` **(বদলেছে)** · `sources.json`

**২. নতুন scanner module**
`channel_resolver.py` **(বদলেছে — §12 team-name fix)** ·
`channel_groups.py` **(বদলেছে — renderer field)** · `event_lifecycle.py` ·
`streamed_provider.py` · `live_protection.py` **(বদলেছে)** ·
`snapshot_publish.py` · `source_coverage.py` · `targeted_scan.py`

**৩. বাকি scanner**
`merger.py` **(বদলেছে — §5 + backspace fix + fold key + event id)** ·
`events.py` **(বদলেছে — attachment pool + embed channel)** ·
`schedule_resolver.py` **(বদলেছে — attachment pool + team matching)** ·
`output.py` · `planner.py` · `playback_profiles.py` **(বদলেছে)** ·
`source_loader.py` · `parsers/json_parser.py`

> **channels[] correction pass-এ ঠিক ৬টা file বদলেছে**, নতুন file নেই:
> `scanner/schedule_resolver.py` · `scanner/merger.py` · `scanner/events.py` ·
> `scanner/channel_resolver.py` · `scanner/channel_groups.py` ·
> `tests/test_sports_channel_system.py`
>
> **Production-readiness pass-এ ৩টা:**
> `.github/workflows/scan.yml` **(বদলেছে — missing file নাম ধরে বলে)** ·
> `tests/test_operational_safety.py` **(বদলেছে — নতুন CI contract assert করে)** ·
> `tests/test_event_fixture_catalogue.py` **(নতুন, ১৮টা test)**।
> `config/event-fixtures.json` **ইচ্ছে করেই byte-identical** — section ২ঘ দেখো।
>
> **Carried-card reconciliation pass-এ ৩টা:**
> `scanner/live_protection.py` **(বদলেছে — reconciliation phase)** ·
> `scanner/merger.py` **(বদলেছে — `same_real_fixture()` public predicate)** ·
> `tests/test_sports_channel_system.py` **(বদলেছে — +২৮ test)**।
>
> **Card/UI phase-এ ৬টা:**
> `site/assets/css/event-channel-cards.css` **(নতুন)** ·
> `site/assets/js/app.js` **(বদলেছে)** · `site/index.html` **(বদলেছে)** ·
> `site/sw.js` **(বদলেছে — v30)** ·
> `tests/test_event_channel_card_design.py` **(নতুন, ৩৬ test)** ·
> `tests/test_final_design_contract.py` **(বদলেছে — version pin)**।
>
> **Frontend upload order:** CSS আগে, তারপর `index.html` + `sw.js` একসাথে
> (version দুটো lockstep-এ যেতে হবে, test pin করে), তারপর `app.js`।

**৪. Entry point / CI / validator**
`scan.py` · `.github/workflows/scan.yml` · `scripts/validate-pages.py`

**৫. Frontend**
`site/assets/css/embed-player.css` **(নতুন)** · `event-cards.css` ·
`smart-filter.css` · `site/assets/js/app.js` **(বদলেছে)** ·
`site/index.html` **(বদলেছে)** · `site/sw.js` **(বদলেছে — v29)** ·
`site/_headers`

**৬. Worker** — `workers/playback-proxy/src/index.js` (**v5.3.2, এই round-এ
বদলায়নি**)। আগেই deploy থাকলে কিছু করতে হবে না।

**৭. Tests** — `tests/` **১৩টা file**। `test_sports_channel_system.py` (১২০টা test) আর `test_event_fixture_catalogue.py` (**নতুন, ১৮টা catalogue-integrity test**)। মোট **508 pass**।

### খেয়াল রাখার কথা

* `config/sources.json` কখনও মুছবে না।
* নতুন state file scan নিজেই বানায়: `state/streamed-provider-cache.json`,
  `state/streamed-provider-health.json`। `state/playing-sessions.json` optional —
  না থাকলে "কেউ দেখছে না" ধরা হয়, বাকি তিনটে protection তখনও কাজ করে।
* `sw.js` CACHE_VERSION `v29-channels`, `index.html` asset version
  `20260817-sports-channels-v1` — দুটো একসাথেই যেতে হবে (test দুটোই pin করে)।

---

## ৪. Data contract (§17/§18 — additive, কিছু remove হয়নি)

Event card-এ **নতুন** field:

```json
{
  "name": "Al Nassr vs Al Fateh",
  "lifecycle_state": "LIVE",
  "default_channel_id": "evt-x--fancode",
  "channel_count": 3,
  "channels": [
    { "id": "evt-x--fancode", "name": "FANCODE", "normalized_name": "fancode",
      "name_confidence": "derived", "primary_stream_id": "evt-x--fancode--1",
      "stream_count": 2, "backup_count": 1, "playback_types": ["native"],
      "renderer": "native",
      "streams": [
        { "id": "evt-x--fancode--1", "role": "primary",
          "playback_type": "native", "playback_id": "ctv_…" },
        { "id": "evt-x--fancode--2", "role": "backup",
          "playback_type": "embed", "embed_url": "https://…" }
      ] }
  ],
  "embed_backups": [ { "playback_type": "embed", "embed_url": "https://…" } ],
  "embed_channel_count": 2
}
```

এই correction pass-এ যোগ হওয়া field:

| Field | কোথায় | মানে |
|-------|-------|------|
| `channels[].renderer` | channel | `native` / `embed` / `mixed` — §26, reader-এর জন্য একটাই value |
| `embed_channel_count` | card | কতগুলো channel provider embed থেকে এসেছে |
| `attached_from_suppressed_pool` | scan-internal | এই stream শুধু একটা fixture তাকে দাবি করেছে বলেই public হয়েছে (audit) |
| `schedule.pool_offered` / `pool_attached` / `pool_unclaimed` / `pooled_for_attachment` | scan report | attachment pool-এর হিসেব |
| `schedule.streamed_enrichment.embed_channels` | scan report | কতগুলো embed channel যোগ হয়েছে |

**Event id এখন broadcaster-মুক্ত** — আগে `al-nassr-vs-al-fateh-sportv-br` হত,
এখন `al-nassr-vs-al-fateh`। Card id বদলালে যাতে card নতুন মনে না হয়, তার জন্য
existing `reuse_published_event_ids()` আগের মতোই published id ফিরিয়ে ব্যবহার করে।

পুরনো সব field যেখানে ছিল সেখানেই আছে: `url`, `backups`, `available_link_count`,
`verification_status`, `primary_stream_key`, `sport_type`, `playback_id` —
frontend/tests/Worker কিছুই ভাঙে না। Raw URL / header / cookie / DRM key
`channels[]`-এ **ঢোকেনি** — native stream শুধু `playback_id` দেয়, existing
protected playback architecture যেমন ছিল তেমনই।

Channel stream-এর `playback_id` merge-এর সময়েই আসল publish-time id হিসেবে হিসাব
হয় (`stable_playback_id`, collector আর channel layer একই function ব্যবহার করে) —
নইলে channel stream unplayable হত। আসল scan-এ **unresolvable playback id: 0**।

---

## ৫. Full regression (সব আসল data-তে)

| Suite | ফল |
|-------|-----|
| Python `unittest discover tests` | **616 pass, 0 fail** (৩৬৮ + ৯০ §20/§35 + ৩০ channel-fix + ২০ CI/catalogue + ২৮ reconciliation + **৩৬ নতুন** Card/UI contract) |
| `tests/playback-worker-runtime.mjs` (CI step) | **PASS**, worker 5.3.2 |
| Build + Pages validator | Channels 406 · Movies 2030 · Series 39 · Episodes 211 · Events 49 · Warnings 0 · **Errors 0** |
| Playback regression (real Worker) | **PASS 15, FAIL 2** — নিচে ব্যাখ্যা |
| Streamed end-to-end, configured test mode | **PASS 32, FAIL 0** |
| Published multi-channel (`publishedchanneltest.py`) | **PASS 23, FAIL 0** |
| Native channel layer, real records | **PASS 17, FAIL 0** |
| Source audit — ২৪টা configured source | ১৭ ঠিক · **৭ খালি/404** (section ০ক) |
| CI validate step, bash block সত্যি চালিয়ে | as-is **exit 0** · ৩টা file লুকিয়ে **exit 1, তিনটেরই নাম** · restore করে **exit 0** |
| Fixture catalogue integrity (`test_event_fixture_catalogue.py`) | **১৮ pass** · mutation দিয়ে ৪/৪ defect ধরা পড়ে |
| Published `channels[]` audit (`channelaudit.py`) | multi-broadcaster ৬টা fixture, ৬টারই কারণ ব্যাখ্যাত · **unexplained 0** |
| Carried-card reconciliation, আসল card-এ (`reconciletest.py`) | **PASS 33, FAIL 0** |
| Duplicate + event-id continuity + channel namespacing (`continuitytest.py`) | **PASS 11, FAIL 0** — published-এ duplicate 0 |
| Embed renderer geometry (real browser, ২ viewport) | **PASS 38, FAIL 0** |
| Card UI regression (৮ viewport, original-এর সাথে তুলনা) | **PASS 1544, FAIL 0** |
| **Card design §20 validation** (`channeluitest.mjs`, ৮ viewport) | **PASS 388, FAIL 0** |
| Player-lock + Smart Filter + responsive | **PASS 173, FAIL 0** |
| Atomic snapshot consistency | **PASS 28, FAIL 0** |
| Snapshot pointer / reader | **PASS 9, FAIL 0** |
| Fresh real scans (এই pass-এ) | `today` ✓ **23 card** · `upcoming` ✓ **38 card** · `upcoming-targeted` ✓ (38-এর 38টা window-এর বাইরে → **০ fetch, ০ replace**) |

### §20-এর চাওয়া test গুলো (সব pass)

Same fixture multiple sources → one event · cricket/football/tennis/unknown sport
grouping · ৫টা exact duplicate → one visible channel · same channel different
URL/DRM/token/cookie/header → Primary + Backups · different channels same event →
separate groups · same Willow different events → never cross-merge ·
Willow/Willow 2/Willow Extra distinct · resolver priority · uncertain name → no
channel bar · Willow select → pinned · Willow primary fail → Willow backup · সব
Willow fail → next independent channel · background scan healthy channel
force-switch করে না · Upcoming channel attach same event · Upcoming → Today same
id · playback worker/profile resolution · atomic snapshot · live-preservation ·
player hard-lock।

### §35-এর চাওয়া test গুলো (সব pass)

Streamed unavailable → native scan/playback unchanged · same fixture GitHub +
Streamed → one event_id · Streamed id never replaces event_id · date/time enrich
without duplicate card · poster/badge fail → existing artwork · healthy native
Primary stays Primary · native fail → embed fallback · embed → native restore
cleans iframe/session · embed mode native control apply করে না · native control
restore · player geometry unchanged native↔embed · Streamed listing disappearance
still-live event মোছে না · Upcoming targeted/on-demand strategy · timeout valid
snapshot নষ্ট করে না · provider-driven Primary flapping নেই · duplicate card নেই।

### §21-এর চাওয়া test গুলো (সব pass)

scheduled end passed + stream playable → preserved · scheduled end passed +
authority LIVE → preserved · authoritative FT → ENDED · authority unavailable +
stream alive → preserved · authority unavailable + dead + repeated → END_PENDING
তারপর ENDED · currently-playing কখনও removed/interrupted হয় না · football extra
time · tennis long match · cricket delayed/multi-day।

আসল scan-এ lifecycle এখন publish হচ্ছে: today `LIVE 6, END_PENDING 2`,
upcoming `UPCOMING 41`।

---

## ৬. সৎভাবে যা বলা দরকার

* **Card/UI phase complete।** ২১টা section-এর প্রতিটা requirement implement বা
  preserve করা হয়েছে (audit table section ২চ-এ), কোনো TODO/placeholder/skip নেই —
  একটা test সেটা assert করেও রাখে।
* **আজকের আসল published data-য় channel strip একটা card-এ দেখা যায়:**
  `Sri Lanka vs India 1st Test` → Willow (৬ stream)। বাকি card-গুলোর broadcaster
  §12 অনুযায়ী resolve হয় না, তাই §9 মেনে **কোনো strip render হয় না** — fake bar
  নয়, খালি box নয়। §20-এর ১/৪/৮ channel, লম্বা football নাম, tennis নাম, Test
  title — এই case গুলো আসল published card আর আসল `channels[]`/`streams[]` shape
  দিয়ে বানিয়ে app-এর নিজের fetch-এ ফেরত দিয়ে verify করা হয়েছে
  (`channeluitest.mjs`)। Static DOM কোথাও লেখা হয়নি; প্রতিটা assertion shipping
  render path যা তৈরি করেছে সেটাই পড়ে।
* **তিনটে harness-এ fixed `waitForTimeout` ছিল, সেগুলো ঠিক করতে হয়েছে —
  assertion দুর্বল করে নয়।** আজকের upstream link সব মরা, তাই player তার failover
  chain ধরে ঘুরতে থাকে আর প্রতিটা retry `cleanupPlayerEngine()` চালায়। ফলে
  embedtest-এর মাপা iframe মাঝপথে unmount হয়ে যেত, আর cardtest/filtertest-এর
  "playback শুরু/pause হয়নি" check retry-টাকেই ধরে ফেলত। Trace করে দেখা গেছে
  t450ms-এ `currentSrc` বদলায় আর iframe সরে যায়। তিনটে harness-এ এখন
  `waitForPlayerQuiet()` — source stable হওয়া পর্যন্ত অপেক্ষা করে, তারপর মাপে।
  Assertion একটাও বদলায়নি বা সরানো হয়নি।
* **Smart Filter-এর একটা assertion আরও কড়া করা হয়েছে।** "filtering-এর পরে card
  কম" proxy আজকের data-য় ভেঙে যাচ্ছিল (lazy loader একই সংখ্যায় অন্য card দিয়ে
  list ভরে দেয়)। §17 আসলে যা চায় সেটাই এখন assert হয়: **filtered list-এ ওই
  sport ছাড়া আর কিছু থাকে না**, আর প্রতিটা hidden event তার strip সঙ্গে নিয়ে যায়।
* **আরেকটা real defect ধরা পড়েছে এবং ঠিক হয়েছে:** ১৩টা আসল football fixture
  (`Eerste Divisie`, `Primera Nacional/C`, `1 Deild`, `Úrvalsdeild`, `NB I`,
  `Jong …`) sport classifier চিনত না, তাই `OTHER`-এ যাচ্ছিল — মানে §17-এর
  Football filter ওগুলো **লুকিয়ে ফেলত**। League নামগুলো recognition-এ যোগ করা
  হয়েছে; এগুলো সত্যিই football, অনুমান নয়।
* **কোনো solved protection দুর্বল করা হয়নি।** Fixture authority আগের মতোই
  stream-only candidate-কে card হতে দেয় না; live-preservation, kickoff
  tolerance, targeted scan (fresh run-এ ৩৮টার ৩৮টাই window-এর বাইরে → **০
  fetch, ০ replace**), snapshot pointer, player pinning, stream stickiness,
  hysteresis — সবগুলোর test সহ **508 pass, 0 fail**।
* **আমার নিজের একটা ভুল ধরা পড়েছে এবং শুধরে নেওয়া হয়েছে।** এই pass শুরু করেছিলাম
  এই ধারণা নিয়ে যে Test match-এর `duration_minutes: 480` alignment defect।
  ফাইলটা সত্যি পড়ে দেখা গেল প্রতিটা Test fixture-এর explicit `end` আগেই আছে আর
  window সত্যিই ৪.৩ দিন। তাই catalogue-এ করা পরিবর্তন **revert করে byte-identical
  রাখা হয়েছে**, আর তার বদলে ওই correctness-টা test দিয়ে বাঁধা হয়েছে। ঠিক থাকা
  data "align" করতে গিয়ে নাড়ানো নিছক ক্ষতি হত।
* **আজকের published card-এ multi-channel নেই, আর প্রতিটার কারণ যাচাই করা।**
  `channelaudit.py` fresh scan-এর পরে চালিয়ে প্রতিটা multi-broadcaster fixture
  আলাদা করে হিসাব করা হয়েছে — **unexplained 0**:

  | Fixture | Broadcaster | কারণ | ঠিক? |
  |---------|-------------|------|------|
  | 1st Test Australia vs Bangladesh | 4 | ৪টার **০টার** link জীবিত | হ্যাঁ |
  | 5th ODI Afghanistan vs Ireland | 2 | ২টার **০টার** link জীবিত | হ্যাঁ |
  | Aus Vs Ban | 3 | ৩টার **০টার** link জীবিত | হ্যাঁ |
  | Al Nassr Vs Al Fateh | 3 জীবিত | match **15 Aug-এ শেষ**, catalogue/authority-তে নেই | হ্যাঁ |
  | Sevilla Vs Rayo Vallecano | 2 জীবিত | match **15 Aug-এ শেষ** | হ্যাঁ |
  | Cpl T20 Vs Cpl T20 | 3 জীবিত | **17 Aug-এ কোনো CPL match নেই** | হ্যাঁ |

  অর্থাৎ blocker code নয়, catalogue-ও নয় — **playlist গুলো দুদিনের পুরনো match আর
  একটা placeholder title নিয়ে বসে আছে**। এগুলো publish করানোর একমাত্র উপায় হত
  শেষ হওয়া match live দেখানো বা নেই-এমন match-এর fixture বানানো, যা তুমি নিষেধ
  করেছ এবং যা solved protection ভাঙে। Fixture যেদিন সত্যি live হবে সেদিন সব
  channel publish হয় — section ২খ-এর প্রমাণ ১ ঠিক সেটাই দেখায় (৩ channel + ২
  channel, আসল playback_id সহ)।
* **গত pass-এর known gap এখন fix — section ২ঙ।** `India vs Sri Lanka Willow`
  আর `Sri Lanka vs India 1st Test` এখন **একটাই card**, canonical id অটুট, Willow
  একটা selectable channel, ছয়টা stream-ই reachable। Currently-playing card
  কখনও fold হয় না, আর কোনো `released_*` বাড়ে না। Published output-এ এখন কোনো দুটো
  card একই real fixture নয় (`continuitytest.py`)।
* **Playback suite PASS 15 / FAIL 2** — দুটো fail হল "proxy path serves
  manifests/segments 0/8"। এই pass-এ fresh data দিয়ে আবার মাপা হয়েছে:
  deployed manifest **2026-08-17T09:35 UTC**, local manifest **17:08 UTC**, আর
  fresh today-card-এর ২৩টা playback id-র **২২টা deployed catalogue-এ নেই**।
  Worker deployed catalogue দেখে, তাই resolve করতে না পেরে 404 দেয়। CI fail
  করছে বলেই deploy আটকে আছে — অর্থাৎ section ০/০খ ঠিক হলে এটাও ঠিক হয়ে যাবে।
  বিস্তারিত:

  | | |
  |---|---|
  | Worker `/health` | **200, v5.3.2** ✓ |
  | Origin allowlist (৮টা origin) | ✓ |
  | Shard lookup | **479/479** playback id, 219 shard ✓ |
  | Allowed hosts | ৩৩৭ host, সবগুলো allowlist-এ ✓ |
  | Local playback id **deployed** catalogue-এ আছে? | **৮টার মধ্যে ৭টা নেই** |

  Deployed catalogue-এর timestamp **09:35 UTC** — CI fail করছে বলে নতুন কিছু
  publish হয়নি। Worker deployed catalogue দেখে, তাই fresh id resolve করতে পারে
  না → 404। যে ১টা deployed আছে তার origin নিজে 403 দিচ্ছে। Origin-কে সরাসরি
  জিজ্ঞেস করা হয়েছে: **৮টার ৬টা origin আসল manifest দিচ্ছে**, তাই stream গুলো
  ঠিক আছে — শুধু publish হয়নি। **Section ০-এর `config/` upload করলেই এটা মিটে
  যাবে।**
* `backuptest`: ৪২টা today card-এ ১৪টা primary চলেছে, ২৮টার কোনো link কাজ করেনি —
  একই কারণ (rotating live link + stale deploy)।
* **Worker এই round-এ বদলায়নি** (5.3.2)। Embed playback Worker-এর ভিতর দিয়ে যায়
  না, provider iframe সরাসরি চলে — তাই proxy contract অপরিবর্তিত।
* Player width/height/aspect-ratio/position, main layout, header, navigation,
  sidebar proportion — কিছুই বদলায়নি; hard-lock test + browser geometry test
  দুটো দিয়েই বাঁধা। কোনো feature সরানো হয়নি।

---

## ৬ক. Final delivery — একটাই folder

> **এই round-এর folder: `claude-solution-16/` — ৫৬টা file.**
> `claude-solution-15` + এই production correction round, একসাথে। আগের কোনো
> folder বা original repository ছোঁয়া হয়নি। নিচের description-টা
> solution-15-এর, আর তার উপরে যা যোগ হয়েছে সেটা section ৬গ–৬ঞ-তে।


`claude-solution-15/` — **৫০টা file**। এর ভিতরে system/backend/scanner/
data-model/playback phase-গুলোর সব changed/new file **আর** এই Card/UI phase-এর
সব changed/new file, exact repository structure-এ। আলাদা কোনো UI folder, card
folder বা frontend folder বানানো হয়নি।

```
claude-solution-15/
├── .github/workflows/scan.yml
├── config/            6 file   (settings · sources · event-fixtures · aliases · categories · header-profiles)
├── scanner/           15 file  (+ parsers/json_parser.py)
├── scripts/           validate-pages.py
├── site/              8 file   (index.html · sw.js · _headers · 4 css · app.js)
├── tests/             15 file  (+ fixtures/ + playback-worker-runtime.mjs)
├── workers/           playback-proxy/src/index.js  (v5.3.2, এই phase-এ বদলায়নি)
└── README_KI_KORTE_HOBE.md
```

**Deploy = এই folder-টা repository-র উপরে কপি করা।** Structure হুবহু মিলে, তাই
প্রতিটা file নিজের জায়গায় বসে। যেসব original file কোনো phase-এ বদলায়নি
(`scanner/normalizer.py`, `site/assets/css/app.css`, `site/assets/js/series.js`,
`manual/`, `data/`, `working/` …) সেগুলো repository-তেই আছে এবং **অপরিবর্তিত** —
delivery folder সেগুলোর copy বহন করে না, কারণ বদলায়নি এমন file duplicate করলে
কোনটা আসল সেটাই অস্পষ্ট হয়ে যায়।

> এর মানে: `claude-solution-15/tests/` folder থেকে সরাসরি
> `python -m unittest` চালালে ১০টা `ModuleNotFoundError` আসবে — কারণ
> `scanner/normalizer.py` বা `manual/` ওখানে নেই। File গুলো repository-তে কপি
> করার **পরে** পুরো suite চলে: **616 pass, 0 fail**।

---

## ৬খ. PASS / FAIL table (সব suite, শেষ run)

| # | Suite | PASS | FAIL |
|---|-------|------|------|
| 1 | Python `unittest discover tests` (full regression) | **616** | **0** |
| 2 | Fresh Today scan | ✔ exit 0 | 0 |
| 2 | Fresh Upcoming scan | ✔ exit 0 | 0 |
| 2 | Fresh targeted scan (৩৩/৩৩ window-এর বাইরে, ০ fetch) | ✔ exit 0 | 0 |
| 3 | one-fixture-one-card / duplicate audit (`continuitytest.py`) | **11** | **0** |
| 4 | channels[] grouping — native layer, আসল record (`nativechanneltest.py`) | **17** | **0** |
| 4 | channels[] grouping — published payload (`publishedchanneltest.py`) | **23** | **0** |
| 4 | published channels[] audit (`channelaudit.py`) — unexplained | **0** | — |
| 5 | channel selector interaction (`channeluitest.mjs`, §20, ৮ viewport) | **388** | **0** |
| 6 | native Primary/Backup failover (`playbacktest.mjs`) | **15** | **2** ↓ |
| 7 | Streamed embed fallback, configured test mode (`streamedtest.py`) | **32** | **0** |
| 8 | live-preservation + carried-card reconciliation (`reconciletest.py`) | **33** | **0** |
| 9 | playing-session / pinning / stickiness (`cardtest.mjs`-এ অন্তর্ভুক্ত) | ✔ | 0 |
| 10 | player hard-lock / geometry (`cardtest.mjs` + `embedtest.mjs`) | **1544** + **38** | **0** |
| 11 | desktop responsive Card UI (1440 · 1280 · 1024 · 820) | ✔ `channeluitest` + `cardtest` | 0 |
| 12 | mobile responsive Card UI (480 · 390 · 360 · landscape 800×420) | ✔ `channeluitest` + `cardtest` | 0 |
| 13 | Smart Filter regression (`filtertest.mjs`) | **173** | **0** |
| 14 | snapshot / pointer / atomic publish | **28** + **9** | **0** |
| 15 | Worker 5.3.2 runtime (`tests/playback-worker-runtime.mjs`) | ✔ PASS | 0 |
| 16 | Pages/build validator | Events **77** · Warnings **0** · Errors **0** | 0 |
| 17 | real published Today/Upcoming inspection | ✔ নিচে | — |

**↓ playbacktest-এর ২টো fail** — regression নয়, আর এই phase-এর সাথেও সম্পর্ক নেই।
Worker health `/health` 200 v5.3.2, origin allowlist, shard lookup, allowed-hosts
সব pass; fail শুধু "proxy path serves manifests/segments 0/8"। কারণ deployed
catalogue পুরনো (CI fail করছে, section ০/০খ) — Worker deployed catalogue দেখে, তাই
fresh playback id resolve করতে পারে না। Config upload করলেই মিটে যাবে।

### ১৭. Real published output inspection

```
today-match.json   : 24 card
upcoming.json      : 33 card
channels[] সহ card : 1   ->  Sri Lanka vs India 1st Test
                            id                 sri-lanka-vs-india-1st-test
                            channel_count      1
                            channel            Willow  (native, 6 stream, conf=derived)
                            channel id         sri-lanka-vs-india-1st-test--willow
                            default_channel_id ''      (native primary demote হয়নি)
                            absorbed_event_ids ['india-vs-sri-lanka-willow']
duplicate fixture  : 0
channel id unique  : ✔      সব id নিজের event-এ namespaced
unexplained gap    : 0
```

---

## ৬গ. Production correction round — deployed site audit করে বারোটা problem

এই round-টা code পড়ে শুরু হয়নি। শুরু হয়েছে **আসল deployed site আর আসল published
JSON** দেখে:

```
https://clicktv.pages.dev/data/today-match.json
https://clicktv.pages.dev/data/upcoming.json
https://clicktv.pages.dev/            (real Chromium, real user flow)
```

Deployed build তখন `claude-solution-15` ছিল (`event-channel-cards.css`,
asset `v=20260818-event-channel-cards-v1`, service worker `...-v30`,
`absorbed_event_ids` present) — অর্থাৎ নিচের প্রতিটা defect **আগের round-এর পরেও
live ছিল**, অনুমান নয়।

### ROOT CAUSE — এক নজরে

| # | Problem | Root cause (আসল কারণ) | Status |
|---|---------|------------------------|--------|
| 1 | একই fixture কয়েকটা card | চারটে আলাদা কারণ, নিচে ৩ক-তে | **FIXED** |
| 2 | Upcoming → Today event id বদলে যায় | `reuse_published_event_ids` এমন id বসাত যেটা **একই scan-এর আরেকটা card-এর** | **FIXED** |
| 3 | `category`/`source_pipeline` ভুল | routing status দেখে হয়, কিন্তু `category` feed থেকে copy হয়ে আর বদলাত না | **FIXED** |
| 4 | Streamed integration কাজ করছে না | code সম্পূর্ণ ছিল, কিন্তু `enabled: false` + `base_url: ""` — কখনো configure হয়নি | **FIXED** |
| 5 | poster/badge ব্যবহার হচ্ছে না | (ক) live API-তে `poster` field নেই, poster দুটো badge দিয়ে address হয়; (খ) card শুধু `item.logo` পড়ত, `artwork_candidates` কখনো পড়ত না | **FIXED** |
| 6 | পরিষ্কার football → `other` | scanner-এর sport rule-এ Dutch `divisie`, Argentine `primera`, Icelandic `deild`, `NB I`, `Jong` ছিল না | **FIXED** |
| 7 | channels[] coverage প্রায় শূন্য | channel শুধু **stream title** থেকে resolve হত; playlist title-এ fixture-এর নাম থাকে, broadcaster-এর নাম থাকে না | **FIXED** |
| 8 | card আর selector দুইটা আলাদা box | row নিজের full border-radius আর status-coloured bottom edge রেখে দিত | **FIXED** |
| 9 | Today/Upcoming ~৮ sec পরে freeze | event buffer **second**-এ লেখা, segment ৪ sec — `maxBufferLength: 5` = ১.২৫ fragment | **FIXED, production-এ reproduce করে প্রমাণ** |
| 10 | ভুল content (sports card → news stream) | **reproduce হয়নি — data-তে সম্ভবই না**, নিচে ৩ঘ | **NOT A BUG** |
| 11 | Streamed embed fallback prove হয়নি | ৪ নম্বরেরই ফল; provider off ছিল | **FIXED** |
| 12 | artwork + identity + channel canonical merge হয় না | enrichment matcher শুধু plain name key মেলাত | **FIXED** |

---

## ৬ঘ. প্রতিটা root cause, একটু বিস্তারে

### ৩ক. একই fixture চারটে card (problem 1 + 12)

Production-এ **একই Test চারটে card** হয়ে ছিল:

```
sri-lanka-vs-india-1st-test                          channels 1   (catalogue card)
india-tour-of-sri-lanka-2026-1st-test-sri-lanka-...  category upcoming (!)
sri-lanka-vs-india                                   previous_event_id ...-1st-test
day-3-1st-test-17-aug-2026-india-tour-of-sri-lanka   CHANNEL_LIVE, teams নেই
```

চারটে আলাদা কারণ ছিল, চারটেই আলাদা করে ঠিক হয়েছে:

1. **Competition prefix.**
   `"India tour of Sri Lanka 2026 1st Test Sri Lanka vs India"` — "A vs B"
   extraction title-এর শুরু থেকে ধরে, তাই পুরো series নামটা left side-এ ঢুকে
   যেত। এখন left side-এর **শেষ round ordinal বা ৪-digit year** পর্যন্ত prefix
   কাটা হয় → key দুটো এক। Team নামের ভিতরের year বাঁচে (`TSG 1899 Hoffenheim`,
   `1860 Munich`) — test আছে।

2. **Round-কে competition ভাবা।**
   Provider `competition: "1st Test"` পাঠায়, catalogue পাঠায়
   `"India Tour of Sri Lanka 2026"`। দুটোকে competition হিসেবে মেলালে
   **contradiction**, তাই fold হত না। এখন পুরো field শুধু round word হলে (এবং
   round word সরানোর পরে যা থাকে সেটা bare letter/roman numeral হলে) field-টা
   ফাঁকা ধরা হয় — অর্থাৎ wildcard, যা সে আসলে।

3. **Multi-day fixture.**
   Test-এর day 3 day 1-এর ২ দিন পরে শুরু হয়; kickoff tolerance ৯০ মিনিট। তাই
   দুটো kickoff "আলাদা fixture" বলত। এখন **catalogue-এর নিজের `[start, end]`
   window** দিয়ে মেলানো হয় — `config/event-fixtures.json`-এ explicit `end`
   ঠিক এই কাজের জন্যই আছে। **শুধু catalogue-কে বিশ্বাস করা হয়**: provider-এর
   guessed long window identity চওড়া করতে পারে না (test আছে), নাহলে দুটো আসল
   match জোড়া লেগে যেত।

4. **Grouping আর routing একমত ছিল না** — এটাই architectural root cause।
   `_destination_for` **status** দেখে Today/Upcoming ঠিক করে, কিন্তু merge
   group key ছিল **`source_pipeline`**। তাই "upcoming" feed-এ configured একটা
   live fixture আলাদা group-এ পড়ত, আর তারপর routing তাকে Today-তেই পাঠাত —
   পাশাপাশি দুটো card। এখন routing rule একটাই জায়গায়
   (`scanner/event_lifecycle.py: event_destination`) এবং **merge ওই একই
   destination দিয়ে group করে**, তাই দুটো আর আলাদা হতে পারে না।

**Participant-হীন label** (`"Day 3 1st Test 17 Aug 2026 | India Tour of Sri
Lanka 2026"`) — এতে team নাম নেই, normalisation-এ শুধু `1-test` থাকে (যা এত
generic যে দুটো আলাদা series-ও এক হয়ে যেতে পারত)। এখন catalogue **competition
alias + round + live window** দিয়ে বেঁধে দেয়, আর চারটে শর্তের সবগুলো মিললে
তবেই — participants আছে এমন title এতে ঢোকে না, round মিলতে হয়, fixture এখন
চলতে হয়, আর দুটো fixture উত্তর দিলে কোনোটাই নেওয়া হয় না।

আর একটা আসল কারণ এখানেই ধরা পড়েছে: **competition-এর নিজের নাম তার alias list-এ
ছিল না**। তাই title যদি series-টা catalogue-এর মতো হুবহু লেখে, তবুও মিলত না —
কেউ যদি `aliases`-এ ওই string আবার না লিখে থাকে। এখন name নিজেই একটা alias।

### ৩খ. Event id continuity (problem 2)

`reuse_published_event_ids` নাম মিলিয়ে published id ফিরিয়ে দিত — কিন্তু চেক
করত না ওই id **এই scan-এর অন্য card** ইতিমধ্যে নিয়েছে কিনা। তাই এক fixture
`sri-lanka-vs-india-1st-test` আর `sri-lanka-vs-india` — দুটো identity-তে
answer করত, frontend-এর পক্ষে বোঝার উপায় ছিল না কোনটা খোলা আছে। এখন collision
guard আছে: id দখলে থাকলে নতুন minted id-ই রাখা হয় — **নিজের identity থাকা card
recoverable, একটা identity ভাগ করা দুটো card নয়।**

### ৩গ. channels[] coverage (problem 7)

Architecture ঠিক ছিল, বাস্তবে প্রায় খালি: production-এর **৩০টা card-এর ০টায়**
channel strip ছিল। কারণ channel resolve হত শুধু **stream-এর নিজের label** থেকে,
আর playlist entry-এর title-এ থাকে fixture-এর নাম — broadcaster-এর নাম নয়।

অথচ প্রতিটা feed **একটাই named broadcaster** relay করে, আর সেটা তার নিজের config
entry-তে লেখা থাকে। তাই:

* `config/sources/today-match.json` আর `upcoming.json`-এ প্রতিটা event source-এ
  একটা `broadcaster` field declare করা হয়েছে (Tapmad, SonyLIV, Willow, CricHD,
  AX Sports, Bingstream, CricketLive, Tapmad BD)। **অনুমান নেই** — যে feed অনেক
  broadcaster mix করে (`sm-sports-data-upcoming`) সে কিছুই declare করে না, কারণ
  ভুল নাম দেখানো নাম না দেখানোর চেয়ে খারাপ (§12)।
* resolver-এ এটা **সবার শেষ step** — যে stream নিজের title-এ channel-এর নাম বলে,
  সে নিজের নামই রাখে (test আছে: Bingstream feed + `"... Willow HD"` title →
  Willow)।
* declaration over-trim হলে আর ফেলে দেওয়া হয় না: `"AX Sports"` noise-strip
  হয়ে bare category `"Sports"` হয়ে যেত আর তারপর declaration-টাই হারিয়ে যেত।

আর একটা আলাদা root cause: **live protection যে card carry করে সে merge-এ ঢোকেই
না**, তাই §6–§10 তার উপরে কখনো চলত না — অর্থাৎ সবচেয়ে দীর্ঘ চলা live fixture
গুলোই channels[] ছাড়া publish হত। এখন publish হওয়ার আগে carried card-এর
নিজের stream গুলো থেকে channels[] গড়ে দেওয়া হয় (merge যা বানিয়েছে সেটা
কখনো overwrite হয় না, আর কোনো url/header/key leak হয় না — test আছে)।

**ফল, fresh Today scan-এ: ২৩টার ২৩টা card-এ আসল channels[]**, আর সেই Test-টায়
তিনটে আসল broadcaster পাশাপাশি:

```
sri-lanka-vs-india-1st-test   channel_count 3
    Bingstream (native)   Tapmad (native)   SonyLIV (native)
```

### ৩ঘ. ভুল content mapping (problem 10) — reproduce হয়নি

Screenshot-এ Sports page-এ player `22Scope News` চালাচ্ছিল। এটা যাচাই করা হয়েছে
দুই ভাবে, আর **দুটোতেই bug পাওয়া যায়নি**:

* **Data:** event card-এর ৪৮টা playback id আর TV channel-এর ৫১২টা playback id
  মিলিয়ে দেখা হয়েছে — **shared = 0**। একটা event card কোনো news channel-এর
  stream-এ resolve করতেই পারে না, কারণ দুজনের কোনো stream common নয়।
* **Browser:** deployed site-এ ৬টা stream ১০০ sec করে চালিয়ে **source change =
  0**। player যেটা চালাচ্ছিল সেটাই চালিয়ে গেছে।

তাই তোমার অনুমানটাই ঠিক: আগে 22Scope চালানো ছিল, Sports page-এ এসেও player
চলতে থাকে — এটা **player pinning feature** (§18 / requirement 14), bug নয়।

### ৩ঙ. Playback freeze (problem 9) — production-এ reproduce করা হয়েছে

প্রথম hypothesis **ভুল ছিল** এবং test করেই ধরা পড়েছে: ভেবেছিলাম token
`inherit_manifest_query` propagate হচ্ছে না। আসল stream দিয়ে মেপে দেখা গেল
segment গুলো **নিজেদের per-segment token** নিয়ে আসে, আর parent-এর token দিলে
উল্টো 403 — অর্থাৎ `inherit_manifest_query: false` ওখানে **ঠিক**।

আসল কারণ frontend-এ, আর production-এ মেপে পাওয়া গেছে:

```
Today/Upcoming :  ২২৯টা request সবই Worker দিয়ে,  buffer ceiling ~১১ sec
Live TV        :  direct CDN (tvsen5.aynaott.com, tv.cdn.xsg.ge), buffer ২৬.৯ sec
```

event profile buffer **second**-এ লেখা, segment-এর দৈর্ঘ্য দেখে নয়। আসল feed-এ
`TARGETDURATION = 4`, আর "Fast Start" profile-এর event branch-এ
`maxBufferLength: 5` — মানে **১.২৫ fragment** reserve, তার উপরে
`lowLatencyMode` on করে live edge chase। Live TV-তে সমস্যা নেই কারণ সে longer
segment + non-low-latency profile + direct route।

**Deployed site-এ reproduce (Fast Start profile, real Chromium, real Workers):**

```
Sri Lanka vs India 1st Test
  played 103.35s / 110s
  freezes 4  ->  6.39s, 8.14s, 8.13s, 5.10s
  freeze শুরু  ->  17.3s, 28.6s, 41.6s, 54.5s   (≈ ১১–১৩ sec পর পর)
  buffer floor 0.05s          waiting events 8
  freeze-এর ঠিক আগে: শুধু manifest re-poll, মাঝে কোনো segment আসেনি
```

এটাই তোমার বলা "৮ sec চলে → freeze → আবার চলে → আবার freeze"। একই stream
`auto` profile-এ ১০০.২ sec, ০ freeze, buffer floor ৬.৬৪ sec — তাই এটা
**profile-specific**, আর সেই profile-ই "Fast Start"।

**Fix:** buffer আর second-এ নয়, **playlist-এর নিজের segment length-এ** মাপা হয় —
`LEVEL_LOADED`-এ playlist যখন নিজের `TARGETDURATION` বলে, তখন reserve কমপক্ষে
৩ fragment করা হয় (৪ sec segment হলে ১২ sec), আর লম্বা segment হলে edge-chasing
বন্ধ। **কেবল বাড়ানো হয়** — ছোট segment-এর stream নিজের fast start ধরে রাখে,
তাই কোথাও regress করে না। শুধু buffer বাড়িয়ে দেওয়া হয়নি; সংখ্যাটা এখন
stream নিজে বলে।

---

## ৬ঙ. REAL PRODUCTION PROOF — deployed site, real Chromium, এই PC

Local/dist/mock/local-Worker কোনোটা final proof নয়। নিচের সব কিছু
`https://clicktv.pages.dev`-এ, আসল Chromium-এ, আসল Cloudflare Worker আর আসল CDN
দিয়ে।

### Environment (যা আসলে load হয়েছে)

```
page URL        https://clicktv.pages.dev/
asset versions  20260818-event-channel-cards-v1  (deployed = solution-15)
service worker  scope https://clicktv.pages.dev/  state activated
caches          click-tv-event-channel-cards-20260818-v30-app / -data
play_proxies    raspy-meadow-9279 / stream-proxy-3 / -4 / -5  .workers.dev
Worker health   /health -> 200, version 5.3.2, protected_playback true  (৪টাই)
```

### Run 1 — normal load, `auto` profile

| tab | stream | played | freezes | waiting | src change | 4xx/5xx | buffer min→max |
|-----|--------|--------|---------|---------|-----------|---------|----------------|
| Today | Sri Lanka vs India 1st Test | 100.2s | **0** | 0 | 0 | 0 | 6.64 → 11.18s |
| Today | CF Pachuca vs Puebla | 100.15s | **0** | 0 | 0 | 0 | 2.73 → 11.66s |
| Upcoming | Sri Lanka vs India | 100.22s | **0** | 0 | 0 | 0 | 5.62 → 10.07s |
| Upcoming | Kingsmen vs Nevis Patriots | 100.22s | **0** | 0 | 0 | 0 | 5.59 → 9.96s |
| Live TV | T Sports | 100.06s | **0** | 0 | 0 | 0 | 9.14 → **26.9s** |
| Live TV | 2TV Sport | 99.25s | **0** | 0 | 0 | 0 | 3.58 → 13.85s |

### Run 2 — same site, "Fast Start" profile → **freeze reproduce**

উপরে ৩ঙ-তে সংখ্যাগুলো আছে: `Sri Lanka vs India 1st Test` → **৪টা freeze
(6.39 / 8.14 / 8.13 / 5.10 sec), buffer floor 0.05 sec**।

### Run 3 — fix সহ, একই profile, একই Worker, production data

Fix পুরোপুরি frontend-এ, আর সেটা এখনো deploy হয়নি — তাই একটাই variable আলাদা
করা হয়েছে: নতুন frontend locally serve, কিন্তু **`/data/` আর
`/runtime-config.json` live `clicktv.pages.dev` থেকে**। অর্থাৎ playback path
পুরোপুরি আসল (আসল catalogue, আসল Worker, আসল CDN, এই PC, এই network), শুধু app
code আলাদা।

```
                              played    freezes   buffer floor
DEPLOYED (pre-fix)            103.35s      4        0.05s
FIXED    (this round)         110.02s      0        9.19s
```

`Sri Lanka vs India` (একই fixture-এর duplicate card): buffer floor
৪.৪ sec → **১১.৬৭ sec**, দুই দিকেই ০ freeze।

### Cache / service-worker exclude করা হয়েছে

তিন অবস্থায় মেপে দেখা হয়েছে — **normal load**, **দ্বিতীয় visit (warm SW)**,
আর **SW unregister + সব cache delete করে reload**। তিনটেতেই ফল **হুবহু এক**
(rendered 30, live file 62, একই `updated_at`)। অর্থাৎ **cache/service-worker এই
সমস্যার কারণ নয়**, আর production code/SW কোথাও permanently বদলানো হয়নি।

### একটা ভুল ধারণা নিজেই যাচাই করে বাতিল করা হয়েছে

মনে হয়েছিল list ৩০টায় cap করা আছে (file-এ ৬২টা, screen-এ ৩০টা)। যাচাই করে
দেখা গেল **cap নেই**: `state.currentItems` = ৬২, আর `applyFilterAndSort`
**ended event গুলো লুকায়** — production-এর ৬২টার মধ্যে ৩৩টা `END_PENDING`
(§21 অনুযায়ী END_PENDING publish হতেই থাকে)। তাই ৩০টা render হওয়াটা ঠিক
behaviour। এই কারণেই আজ playable Today stream কম — অনেক match শেষ হয়ে গেছে
আর তাদের CDN link 404 দিচ্ছে।

---

## ৬চ. FILES CHANGED (এই round)

**Backend / data layer**

| File | কী বদলেছে |
|------|-----------|
| `scanner/event_lifecycle.py` | নতুন `event_destination()` + routing status — grouping আর routing-এর একটাই rule |
| `scanner/merger.py` | competition prefix strip, round-only competition, catalogue multi-day window, destination দিয়ে grouping, football/hockey sport rule |
| `scanner/schedule_resolver.py` | competition name নিজেই alias, `_competition_round_fixture()`, `_resolve_fixture()`, reuse collision guard |
| `scanner/events.py` | `_stamp_final_routing()`, participant দিয়ে Streamed matching, poster/badge field |
| `scanner/channel_resolver.py` | `declared()` helper, `load_source_broadcasters()`, source-declared broadcaster (শেষ step) |
| `scanner/source_loader.py` | `process_single_source` wrapper — প্রতিটা return path-এ `source_broadcaster` stamp |
| `scanner/live_protection.py` | `_rebuild_card_channels()` — carried card-ও channels[] পায় |
| `scanner/streamed_provider.py` | দুই badge দিয়ে poster URL, per-team badge, `embed_label` |
| `config/settings.json` | Streamed provider enable + verified `base_url`/`images_base` + `embed_label` |
| `config/sources/today-match.json`, `config/sources/upcoming.json`, `config/sources.json` | প্রতিটা event feed-এর `broadcaster` declaration |
| `.github/workflows/scan.yml` | `config/sources/*.json` পাঁচটা required file-এ যোগ |

**Frontend**

| File | কী বদলেছে |
|------|-----------|
| `site/assets/js/app.js` | `applySegmentAwareLiveBuffer()` + `LEVEL_LOADED` hook, `eventArtworkChain()`, দুই crest + VS, artwork fallback chain, `0 Backups` না দেখানো, localhost-only buffer/route test hook |
| `site/assets/css/event-channel-cards.css` | row + strip একটাই card, artwork tile proportion, crest layout, column floor 150px |
| `site/index.html`, `site/sw.js` | asset version `20260819-unified-card-playback-v1`, cache `...-v31` |

**Tests** (weaken/remove কিছু হয়নি)

| File | কী |
|------|-----|
| `tests/test_production_correction_round.py` | **নতুন, ৫০টা test** — বারোটা problem-এর প্রতিটার জন্য |
| `tests/test_sports_channel_system.py` | Streamed artwork + "configured before enabled" — pinned count আর `enabled:false` assertion সরিয়ে **requirement** assert করা হয়েছে |
| `tests/test_event_channel_card_design.py` | column floor আর version-lockstep এখন literal নয়, **derive** করে — partial version bump ধরা পড়ে |
| `tests/test_final_design_contract.py` | version literal → regex |

---

## ৬ছ. A/B — একই stream, একই minute, alternating

Upstream-এর অবস্থা মিনিটে মিনিটে বদলায়, তাই একবার করে চালিয়ে তুলনা করা যায় না।
একই stream, একই "Fast Start" profile, একই Worker, **পালা করে** চালানো হয়েছে:

```
Sri Lanka vs India 1st Test — 110s per run, Fast Start profile

  earlier window (freeze reproduced)
    DEPLOYED (pre-fix)    played 103.35s  freezes 4  buffer floor  0.05s  [6.39,8.14,8.13,5.10]
    FIXED    (this round) played 110.02s  freezes 0  buffer floor  9.19s  []

  later interleaved window (conditions improved, neither side froze)
    round 1 DEPLOYED      played 110.23s  freezes 0  buffer floor  2.79s  []
    round 1 FIXED         played 110.16s  freezes 0  buffer floor 11.16s  []
    round 2 DEPLOYED      played 110.04s  freezes 0  buffer floor  4.20s  []
    round 2 FIXED         played 110.05s  freezes 0  buffer floor  6.74s  []
```

পড়ার নিয়মটা এখানে গুরুত্বপূর্ণ। **Freeze নিজে intermittent** — সে তখনই হয় যখন
কোনো transient (একটা 502, একটা ধীর manifest) available margin-এর চেয়ে বড় হয়ে
যায়। তাই একটা run-এ pre-fix freeze করে, আরেকটায় করে না।

যেটা intermittent নয়, সেটা **margin**, আর সেটাই মাপা হয়েছে:

| | buffer floor | hls config |
|---|---|---|
| DEPLOYED (pre-fix) | **0.05 – 2.79s** | `maxBufferLength 5`, `lowLatencyMode true`, `liveSyncDurationCount 2` |
| FIXED (this round) | **9.19 – 11.16s** | `maxBufferLength 12`, `lowLatencyMode false`, `liveSyncDurationCount 3` |

`segment 4s` × ৩ fragment = ১২ sec — অর্থাৎ সংখ্যাটা stream নিজে বলেছে, আমি
বসাইনি। আর যে run-এ freeze আসলে হয়েছিল, সেখানে fix সেটা **৪ → ০** করেছে।

---

## ৬জ. PASS / FAIL — এই round

| # | Suite / check | কোথায় | Result |
|---|---------------|-------|--------|
| 1 | Python full regression | final folder + repo | **PASS 668 / FAIL 0** |
| 2 | নতুন round-এর test file | `test_production_correction_round.py` | **PASS 50 / FAIL 0** |
| 2ক | card + player hard lock | `cardtest.mjs` | **PASS 1037 / FAIL 0** (৩টা skip, নিচে) |
| 2খ | channel selector, ৮ viewport | `channeluitest.mjs` | **PASS 388 / FAIL 0** |
| 2গ | embed renderer / player geometry | `embedtest.mjs` | **PASS 38 / FAIL 0** |
| 2ঘ | Smart Filter | `filtertest.mjs` | **PASS 173 / FAIL 0** |
| 2ঙ | snapshot slot rotation | `snapshottest.mjs` | **PASS 28 / FAIL 0** |
| 2চ | manifest pointer | `pointertest.mjs` | **PASS 9 / FAIL 0** |
| 3 | Fresh Today scan | local, real feed | ✔ exit 0 |
| 4 | Fresh Upcoming scan | local, real feed | ✔ exit 0 |
| 5 | Fresh targeted scan | local, real feed | ✔ exit 0 |
| 6 | duplicate fixture (fresh output) | published JSON | **0 duplicate** |
| 7 | one canonical id per fixture | published JSON | ✔ `absorbed_event_ids` |
| 8 | final Today/Upcoming category | published JSON | ✔ ২৩/২৩ correct, provenance রাখা |
| 9 | channels[] grouping | published JSON | **২৩/২৩ card-এ channels[]** |
| 10 | genuine multi-channel event | published JSON | ✔ Bingstream + Tapmad + SonyLIV |
| 11 | fake channel | published JSON | **0** |
| 12 | sport classification | fresh output (৫২ card) | **other ৭ → ২** (AFL + England vs Pakistan, দুটোই সত্যিই ambiguous) |
| 13 | Streamed API / enrichment | live `streamed.pk` | ✔ ২৫২ fixture, ২১১ artwork, **০ error** |
| 14 | Streamed artwork ব্যবহার | published JSON | ✔ poster + দুই badge |
| 15 | artwork fallback | frontend chain | ✔ poster → badge → initials (last) |
| 16 | native Primary থাকে | published JSON | ✔ native প্রথম, `default_channel_id` native |
| 17 | Streamed embed = শেষ fallback | published JSON | ✔ `Streamed 1/2`, native-এর পরে |
| 18 | embed-এ internal key leak | published JSON | **0** (`admin`/`delta` আর নেই) |
| 19 | **REAL site — Today ≥ 90 sec** | clicktv.pages.dev | ✔ যতগুলো playable ছিল, প্রতিটা ১০০+ sec |
| 20 | **REAL site — Upcoming** | clicktv.pages.dev | ✔ ২টা × ১০০ sec, ০ freeze |
| 21 | **REAL site — Live TV A/B** | clicktv.pages.dev | ✔ ২টা × ১০০ sec, ০ freeze |
| 22 | **REAL site — ৮ sec freeze cycle** | clicktv.pages.dev | reproduce ✔ · fix-এর পরে **০** |
| 23 | unexpected auto-failover | real site | **0 source change** |
| 24 | wrong channel / content | real site + data | **0** |
| 25 | stale playback_id | real Worker | catalogue-এ সব আছে; 404 গুলো dead upstream |
| 26 | cache / service-worker | ৩ mode | তিনটেতেই একই ফল |
| 27 | player geometry hard lock | `cardtest` + `embedtest` | ✔ row এখনো 152px, uniform |
| 28 | Smart Filter | `filtertest` | ✔ |
| 29 | snapshot / pointer | `snapshottest` + `pointertest` | ✔ |
| 30 | Worker 5.3.2 runtime | live `/health` ৪টা | ✔ |
| 31 | Pages validator | build | ✔ Errors 0 |

### cardtest-এর ৩টা SKIP — কী আর কেন

```
SKIPPED 3 (case absent from today-এর data):
  - today-match phone-480 / 390 / 360   clock still shown on phone
```

আজ এই ঘণ্টায় Today-তে মাত্র ৩টা card: একটা চলছে (playing), আর দুটো
**channel-only**। Channel-only card-এ ঘড়ি **ইচ্ছে করেই থাকে না** —
`_today_source_channel_fallback` reusable channel-এর provider clock মুছে দেয়,
যাতে তাকে scheduled match হিসেবে দেখানো না যায়। তাই "phone-এ ঘড়ি দেখা যাচ্ছে"
assertion-এর জন্য আজ কোনো non-playing scheduled card **নেই**।

Assertion দুর্বল করা হয়নি — data-তে case না থাকলে **skip হিসেবে report** হয়,
pass হিসেবে নয়। Case থাকলে আগের মতোই কড়াভাবে check হয়। (আগে এটা silently
fail করত, কারণ sample হিসেবে channel-only card-টা ধরা হচ্ছিল।)

### একটা extra defect এই harness-ই ধরেছে

`cardtest`-এর "sport recognised across the catalogue" fail করছিল — OTHER ৭/৫২।
খুঁজে দেখা গেল **frontend-এ দুটো আলাদা sport classifier** ছিল: Smart Filter
`itemSportType()` দিয়ে published `sport_type` পড়ত, কিন্তু card-এর **badge**
`eventSport()` দিয়ে শুধু নাম/competition regex দেখত। ফলে একই card-এ
filter বলত FOOTBALL আর badge বলত OTHER — CONMEBOL Libertadores/Sudamericana-এর
৫টা tie-তে ঠিক এটাই হচ্ছিল।

দুটো জিনিস করা হয়েছে: scanner-এর rule-এ `conmebol|libertadores|sudamericana`
যোগ, আর `eventSport()` এখন **published `sport_type`-কে আগে মানে** — অর্থাৎ
sport-এর একটাই source of truth, দুটো list আর আলাদা দিকে সরতে পারবে না।

### ⚠️ FAIL হিসেবে যা লিখতে হবে

**`realsite --today=5` এর "৫টা Today stream" criterion আজ পূরণ হয়নি — ২টা হয়েছে।**
কারণ code নয়: production-এর ৬২টা Today card-এর **৩৩টা `END_PENDING`** (match শেষ)
আর তাদের CDN link 404/502 দিচ্ছে। যাচাই করা হয়েছে — Worker-এ ২৪টা playback id
পরীক্ষা করে **৭টা** live পাওয়া গেছে, আর ended card গুলো frontend ঠিকভাবেই লুকায়।
বেশি match live থাকা সময়ে (BD সন্ধ্যা/রাত) একই command ৫টাই পাবে।

---

## ৬ঞ. Final folder integrity (যাচাই করা)

```
claude-solution-16/                        ৫৬ file
  __pycache__ / *.pyc                      0
  stray backspace byte                     0
  python file parse                        ৩২/৩২ OK
  app.js / worker index.js syntax          OK
  JSON well-formed                         সব
  TODO / FIXME / placeholder               0
  config/sources.json                      আছে (CI required, মুছবে না)
  streamed_provider                        enabled, base https://streamed.pk

CI required path                           ৬০
  solution-16-এর ভিতরে                     ৩২
  repo-তে অপরিবর্তিত                        ২৮
  MISSING EVERYWHERE                       none

original repository tracked change         0
আগের solution folder modified              0
```

`claude-solution-15` সহ আগের কোনো folder আর original repository ছোঁয়া হয়নি।

---

## ৬ঝ. KNOWN REMAINING ISSUE — যা এখনো বাকি

এগুলো থাকতে "final/complete" বলছি না।

1. **এই round-এর fix গুলো এখনো deploy হয়নি (blocking)।**
   Deployed site এখনো `solution-15` চালাচ্ছে। তাই production-এ **এখনো**:
   duplicate card আছে, channels[] খালি, football → other, আর "Fast Start"
   profile-এ ৮ sec freeze হয়। `claude-solution-16/` repository-তে copy করে
   push করলেই এগুলো যাবে।

2. **CI এখনো fail করবে যতক্ষণ `config/` upload না হবে (blocking)।**
   এখন আরও পাঁচটা path required list-এ যোগ হয়েছে
   (`config/sources/*.json`) — এগুলোও upload করতে হবে। CI নাম ধরে বলবে
   কোনটা নেই।

3. **`config/sources.json` মুছবে না।** CI তাকে required ধরে, আর
   `load_sources_config` তাকে fallback হিসেবে পড়ে। এই round-এ তাকেও
   `broadcaster` declaration দিয়ে sync করা হয়েছে।

4. **Upstream link health আমাদের হাতে নয়।** আজ Today card-এর বড় অংশের CDN link
   404/502। এর ফলে যা হয়: playable stream কম, আর একটা transient 502 এলে
   segment ৪ sec-এর stream-এ buffer টান পড়ে। Fix সেই টান ৪ গুণ কমায়, কিন্তু
   upstream সম্পূর্ণ মরে গেলে কোনো buffer বাঁচাতে পারে না।

5. **AFL (`Sydney Swans vs Essendon`) এখনো `other`.** Australian rules একটা
   আলাদা code — জোর করে football-এ ঢোকানো হয়নি। চাইলে config-এ বললেই হবে;
   অনুমান করে বসাইনি।

6. **`England vs Pakistan` এখনো `other`.** competition field ফাঁকা, আর নামটা
   cricket-ও হতে পারে football-ও। sport বানিয়ে দেওয়ার চেয়ে `other` সৎ।

7. **Streamed match rate provider-এর coverage-এর উপর নির্ভর করে।** আজ ২৩টার
   ৭টা মিলেছে; `streamed.pk`-এর `all-today`-তে ওই cricket Test-টা নেই বলে
   সেটা enrich হয়নি। এটা আমাদের matcher-এর সীমা নয় — provider-এর listing-এর।

---

## ৬ট. তৃতীয় correction round — deployed site-এ ধরা পড়া তিনটা bug

Deploy হওয়ার পরে (asset version `20260819-unified-card-playback-v1` live) তুমি
আসল browser-এ দুটো screenshot দেখিয়েছিলে — Today Match আর Upcoming, দুটোতেই। তিনটা
আসল bug ধরা পড়েছে, প্রতিটা আসল production JSON টেনে verify করে, guess করে না।

### Bug ১ — একই match এখনও দুইটা card (Today তালিকায় ১ আর ২ নম্বর)

`sri-lanka-vs-india-1st-test` (৮ channel) আর
`day-3-1st-test-17-aug-2026-india-tour-of-sri-lanka-2026` (SonyLIV, ১ channel) —
আসল production data টেনে দেখা গেছে দ্বিতীয়টার `carried_forward_misses: 49` —
৪৯ scan ধরে আটকে আছে।

**কারণ:** এই card `_today_source_channel_fallback`-এর মধ্য দিয়ে অনেক আগে publish
হয়েছিল, যেটা ইচ্ছাকৃতভাবে `fixture_id`/`competition`/`start_time` মুছে দেয় (যাতে
fake scheduled match না দেখায়)। Title-এ কোনো "vs" নেই, তাই
`participant_fold_key` খালি ফেরত দেয় — আর আগের round-এর `same_real_fixture`
reconciler শুধু participant name বা fixture_id দিয়ে কাজ করে। দুটোই এই card-এ
নেই, তাই ৪৯ scan ধরে কখনো মেলেনি।

**Fix:** `_reconcile_carried_cards`-এ একটা catalogue-based fallback যোগ হয়েছে —
`schedule_resolver._competition_round_fixture` (আগের round-এই লেখা, কিন্তু শুধু
fresh scan-এর জন্য চলত) এখন carried zombie card-এর raw title-এর বিরুদ্ধেও চলে:
canonical card-এর `fixture_id` যদি catalogue-এ থাকে, আর zombie card-এর title-এ
সেই একই fixture-এর round + competition alias থাকে, আর সেই fixture এখনো live —
তাহলে এক ধরা হয়। Round না মিললে বা fixture live না থাকলে fold হয় না (test আছে)।

```python
same_real_fixture(canonical, zombie)              -> False   (আগের মতোই)
_same_fixture_via_catalogue(canonical, zombie)     -> True    (নতুন)
```

### Bug ২ — Upcoming card-এ fake channel (কিছুই play হবে না তবুও "1 Primary")

`Kingsmen vs Nevis Patriots`: `metadata_only: True`, `url: ''`,
`playback_id: None` — তবুও `channels: [{"name": "AX Sports", ...role: primary}]`।

**কারণ:** `stream_variant_identity()` url/embed_url খালি থাকলেও কখনো খালি string
দিত না — সবসময় একটা hash দিত (header_profile ইত্যাদি অন্য field থেকে), তাই
`if not variant_key: continue` guard-টা কাজই করত না।

**Fix:** url, embed_url, playback_id — তিনটাই খালি হলে এখন `""` ফেরত দেয়, তাই এমন
stream আর কোনো channel-এ ঢোকে না।

### Bug ৩ — "Bingstream" কোনো channel না; একই match-এর একই link আলাদা channel হওয়া যাবে না

তুমি সরাসরি ধরিয়ে দিয়েছ: `srhady-bingstream-live` feed-কে আগের round-এ
"Bingstream" broadcaster হিসেবে declare করা হয়েছিল — কিন্তু এটা আসলে শুধু
maintainer-এর নিজের GitHub repo-র নাম (`srhady/bingstream`), কোনো real TV
channel না। একই pattern-এ আরও তিনটা ধরা পড়েছে:

| Declared name | আসল কী |
|---|---|
| Bingstream | `srhady/bingstream` — শুধু repo নাম |
| AX Sports | `srhady/axsports` — শুধু repo নাম |
| CricketLive | `srhady/CricketLive` — শুধু repo নাম |
| CricHD | pirate-streaming aggregator site নাম, broadcaster না |

রাখা হয়েছে শুধু genuinely independent, recognizable brand: **Tapmad, Tapmad BD,
SonyLIV, Willow**।

**তোমার exact instruction অনুযায়ী নতুন rule:**

1. আগে stream-এর নিজের title/tvg-name/group-title থেকে real name বের করার
   চেষ্টা হয় (অপরিবর্তিত, ৬ steps)।
2. সেটা না পেলে, আর সত্যিই এটা একই match-এর **আলাদা** stream হলে (আলাদা
   playback content) — এখন honest generic নাম **"Server-1", "Server-2"...**
   দেওয়া হয় (fake brand name নয়, invisible-ও নয়)।
3. কিন্তু **একই effective link দুইটা আলাদা feed থেকে এলে সেটা কখনোই দুইটা
   আলাদা channel হয় না** — automatic ধরা হয় ও backup হিসেবে fold হয়ে যায়,
   নাম না পাওয়া গেলেও।

```
Test A: দুটো সত্যিই আলাদা unnamed stream
  -> Server-1, Server-2   (health অনুযায়ী numbered)

Test B: একই match, একই link দুটো ভিন্ন feed থেকে
  -> ১টা channel-ই থাকে, দ্বিতীয়টা duplicate হিসেবে বাদ

Test C: unnamed stream যেটা আগে থেকেই একটা real-named channel-এর (Willow)
        হুবহু একই link
  -> কোনো Server-N তৈরি হয় না, শুধু duplicate count বাড়ে
```

Carried card-এর নিজস্ব channel-builder (`_rebuild_card_channels`,
live_protection.py) — যেটা merge-এর `build_event_channels` থেকে সম্পূর্ণ আলাদা,
হাতে-লেখা logic — সেখানেও একই Server-N + duplicate-suppression যোগ করা হয়েছে,
কারণ carried card-ই সবচেয়ে বেশি এই সমস্যায় পড়ে (দীর্ঘ সময় ধরে অনেক feed জমা হয়)।

### পোস্টার/banner প্রশ্নের উত্তর — নতুন কিছু যোগ করার দরকার ছিল না

`https://streamed.pk/docs` থেকে আসল তিনটা endpoint (`all-today`, `live`,
`all`) চেক করে দেখা গেছে: poster mechanism **আগে থেকেই কাজ করছে** — production
data-এ ৪৯টার ২৪টা Upcoming card-এ real poster/badge আছে, আর সেই আসল poster URL
সরাসরি fetch করে ২০০ status পাওয়া গেছে। যে card-টায় ছবি নেই
(`Kingsmen vs Nevis Patriots`) সেটা Streamed-এর কোনো endpoint-এই নেই (২৫৬টা
match-এর একটাও মেলেনি) — এটা তাদের coverage-এর সীমা, আমাদের code-এর bug না।
এটা honestly বলা হচ্ছে, নতুন কিছু বানিয়ে "fixed" দেখানো হয়নি।

### Test ফলাফল

```
Full regression (repo + claude-solution-17 merged) : 688 / 0
নতুন এই round-এর test                              : 12টা নতুন/updated
```

নতুন test যোগ হয়েছে:
- `AZombieCardWithNoParticipantsIsStillReconciled` (৪টা test) — Bug ১-এর জন্য,
  round না মিললে বা fixture live না থাকলে fold হয় না সেটাও আলাদা করে check করে
- `test_each_event_feed_declares_the_broadcaster_it_relays` — এখন সরাসরি assert
  করে fake নামগুলো (bingstream/ax sports/cricketlive/crichd) আর declared নেই
- `test_a_removed_fake_declaration_resolves_to_nothing_not_a_new_lie`
- Server-N + cross-bucket dedup-এর জন্য ৩টা নতুন test
  (`test_sports_channel_system.py`, Section 12 class)

`test_an_unresolved_event_publishes_no_channels_at_all` **rename ও rewrite**
করা হয়েছে (`test_an_unresolved_but_genuinely_distinct_stream_becomes_a_labelled_server`)
কারণ তুমি নিজেই এই behaviour বদলাতে বলেছ — assertion দুর্বল করা হয়নি, requirement-ই
বদলেছে তোমার সরাসরি নির্দেশে।

### FILES CHANGED (এই round, শুধু)

```
scanner/channel_groups.py         Server-N fallback + cross-bucket dedup +
                                   stream_variant_identity() empty-guard
scanner/live_protection.py        zombie carried-card catalogue fallback +
                                   carried-card channel builder-এও Server-N
config/sources.json               fake broadcaster declaration সরানো
config/sources/today-match.json   fake broadcaster declaration সরানো
config/sources/upcoming.json      fake broadcaster declaration সরানো
tests/test_production_correction_round.py   ২টা test আপডেট, ১টা নতুন
tests/test_sports_channel_system.py         ১টা rename+rewrite, ৩টা নতুন,
                                             নতুন class ৪টা zombie-card test
README_KI_KORTE_HOBE.md           এই section
```

---

## ৭. পরের phase

Card/UI phase শেষ, আর তার পরে deployed site audit করে **আরও বারোটা problem**
ঠিক করা হয়েছে (section ৬গ–৬ঝ)। System + Card/UI + এই production correction —
সব একসাথে একটাই folder-এ: **`claude-solution-16`**। আলাদা backend/UI folder
করা হয়নি।

**পরের কাজ, ঠিক এই order-এ:**

1. `claude-solution-16/` পুরোটা repository-র উপরে copy করে push করো — নতুন
   `config/sources/*.json` পাঁচটা সহ।
2. CI চলতে দাও। fail করলে সে **নাম ধরে** বলবে কোন file নেই।
3. Deploy হওয়ার পরে আবার `realsite` test চালাও — তখন production-এই
   ৮ sec freeze ০, channels[] প্রতিটা card-এ, আর duplicate ০ দেখা যাবে।

### Known remaining issue (স্পষ্টভাবে)

1. **CI এখনও fail করবে যতক্ষণ `config/` upload না করবে।** পাঁচটা file GitHub-এ
   নেই; এখন CI স্পষ্ট করে নাম ধরে বলে fail করে (section ০খ)। এটাই প্রথম কাজ।
2. **Deployed catalogue পুরনো** (CI fail-এর ফল), তাই Worker fresh playback id
   resolve করতে পারে না — `playbacktest` ২টো fail। ১ নম্বর ঠিক হলেই মিটে যায়।
3. **Channel strip আজ একটাই card-এ দেখা যায়**, কারণ আজকের feed-এ বাকি
   fixture-গুলোর broadcaster §12 অনুযায়ী resolve হয় না। এটা UI-এর সীমা নয় —
   data alignment (section ২ঘ)। Fixture যেদিন multiple live broadcaster নিয়ে
   আসবে, strip নিজে থেকেই ২–৪টা chip দেখাবে (§20 test সেটাই প্রমাণ করে)।
4. **Upstream link health আমাদের হাতে নয়** — আজ Today card-এর বড় অংশের CDN link
   403/404 দিচ্ছে, তাই player failover chain ধরে ঘোরে। Card/UI এতে বদলায় না,
   কিন্তু browser harness-গুলোকে player quiet হওয়ার জন্য অপেক্ষা করতে হয়।
