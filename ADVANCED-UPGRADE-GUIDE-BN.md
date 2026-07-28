# Click TV Advanced Fast Scanner — সম্পূর্ণ Upgrade Guide

এই package-এ code fragment নেই। পরিবর্তিত প্রতিটি file সম্পূর্ণ অবস্থায় দেওয়া হয়েছে। সবচেয়ে নিরাপদ package হলো **code-only replacement**—এটি `data/`, `state/`, `reports/`, `manual/` এবং `config/sources.json` overwrite করবে না। তাই বর্তমান published result, source list ও manual link অক্ষত থাকবে।

---

## ১. এই Upgrade-এ কোন সমস্যাগুলো ঠিক হয়েছে

### ১.১ Movie ভুল করে TV category-তে ঢোকা বন্ধ

আগে TV source-এর মধ্যে পাওয়া `.mp4`, `.mkv`, `/Movies/`, `/bollywood/`, `/hindidub/`, `/indianbangla/`, `/series/`, `/natok/` ইত্যাদি `source_pipeline: tv` হিসেবেই থেকে যেত। ফলে Bangla এবং Indian TV JSON-এর মধ্যে শত শত movie প্রকাশ হচ্ছিল।

এখন `scanner/content_router.py` URL extension, URL path, group-title এবং title দেখে route নির্ধারণ করে:

```text
TV source + /Movies/.../movie.mkv
→ original_source_pipeline = tv
→ source_pipeline = movies
→ pipeline_rerouted = true
→ routing_reason = vod_extension:.mkv
```

### ১.২ TV output-এ দ্বিতীয় VOD safety barrier

`scanner/channels.py` আবার পরীক্ষা করে। Upstream routing ভুল হলেও direct movie/VOD URL `data/channels/*.json`-এ ঢুকবে না।

### ১.৩ Movie scan TV source-এর ভেতরের movie উদ্ধার করে

`movies` mode এখন configured Movie sources-এর সঙ্গে mixed TV sources পড়বে। Normalizer actual live channel-কে TV রাখবে এবং শুধু movie/VOD item-কে Movies pipeline-এ পাঠাবে। Planner তারপর Movies pipeline ছাড়া সব বাদ দেবে।

### ১.৪ ভাঙা movie title ঠিক

Poster URL-এর মধ্যে comma থাকলে আগে title এমন হতে পারত:

```text
0,380,562 jpg",Aliens Ka Aagman (2026)
```

`scanner/parsers/m3u_parser.py` এখন quoted অংশ বুঝে শেষ safe comma-এর পরের display name নেয়।

### ১.৫ Global ও BD/proxy verification overlap

আগের flow:

```text
সব Global শেষ → তারপর সব BD verification
```

নতুন flow:

```text
Global result প্রস্তুত
→ প্রয়োজন হলে সঙ্গে সঙ্গে BD/proxy queue
→ একই সময়ে বাকি Global verification চলতে থাকবে
```

এটি `scanner/fast_pipeline.py` পরিচালনা করে। Public JSON এখনও সব processing শেষ হওয়ার পরে atomicভাবে publish হয়।

### ১.৬ Adaptive candidate expansion

প্রতি channel-এর সব backup শুরুতেই test হয় না:

```text
প্রথম ranked candidate check
→ target valid link না পেলে পরের candidate
→ target পূরণ হলে unused candidate skip
```

TV quick scan target: ২টি publishable link। Maximum pool: ৬টি candidate। এতে primary/backup verification থাকে, কিন্তু অপ্রয়োজনীয় request কমে।

### ১.৭ Host-aware protection

- একই host-এ একসঙ্গে সর্বোচ্চ ৩টি verification।
- DNS/SSL/connection/network failure বারবার হলে host circuit সাময়িকভাবে open।
- 403/404/410 host-wide failure হিসেবে ধরা হয় না।
- Fast response হলে inflight limit ধীরে বাড়তে পারে।
- Failure বেশি হলে limit কমে।

### ১.৮ Source conditional cache

Source server ETag বা Last-Modified দিলে পরের GitHub run-এ:

```text
If-None-Match / If-Modified-Since
→ 304 Not Modified
→ cached parsed source reuse
```

Workflow cache key প্রতি run-এ নতুন হয় এবং আগের cache restore করে। ফলে cache update হারায় না।

### ১.৯ Quick, Discovery ও Full Audit mode

```text
channels             → নিয়মিত দ্রুত TV health scan
channels-discovery   → বড় TV candidate pool ও বেশি backup discovery
movies               → নিয়মিত movie scan + TV source-এর VOD উদ্ধার
movies-discovery     → গভীর movie discovery
full-audit           → সবচেয়ে বড় manual audit
```

### ১.১০ One-time polluted TV cleanup

প্রথম সফল `channels` run-এ পুরোনো Bangla/Indian TV file-এ direct movie URL পাওয়া গেলে শুধু affected category-র sudden-drop protection একবার bypass হবে।

নিরাপত্তা:

- Incoming valid TV total কমপক্ষে ২০ হতে হবে।
- Previous file-এ সত্যিই VOD/movie URL থাকতে হবে।
- Sports, Cartoon, Islamic ও Foreign News-এর protection বন্ধ হবে না।
- Marker তৈরি হবে: `state/migrations/vod-routing-v1.json`।

### ১.১১ Mode-specific Telegram notification

- `movies` mode → শুধু Movie count।
- `channels` mode → শুধু TV count।
- `events` → Today + Upcoming।
- `all/full-audit` → সব total।

---

## ২. Code-only package-এর exact structure

```text
click-tv-advanced-code-only/
├── .github/
│   └── workflows/
│       └── scan.yml
├── .gitignore
├── config/
│   └── settings.json
├── scanner/
│   ├── __init__.py
│   ├── bd_verifier.py
│   ├── channels.py
│   ├── common.py
│   ├── content_router.py          নতুন
│   ├── events.py
│   ├── fast_pipeline.py           নতুন
│   ├── merger.py
│   ├── movies.py
│   ├── normalizer.py
│   ├── output.py
│   ├── planner.py
│   ├── source_loader.py
│   ├── verifier.py
│   └── parsers/
│       ├── __init__.py
│       ├── direct_stream.py
│       ├── json_parser.py
│       ├── m3u_parser.py
│       └── url_list_parser.py
├── tests/
│   ├── test_content_router.py
│   ├── test_fast_pipeline.py
│   └── test_planner.py
├── colab/
│   └── Live_Signal_Colab_Live_Monitor_V6.ipynb
├── requirements.txt
├── scan.py
├── ADVANCED-UPGRADE-GUIDE-BN.md
├── CHANGED-FILES.txt
└── TEST-REPORT.txt
```

এই package-এ ইচ্ছাকৃতভাবে নিচেরগুলো নেই:

```text
data/
state/
reports/
working/
manual/
config/sources.json
config/channel-aliases.json
config/header-profiles.json
```

কারণ এগুলো upload করলে তোমার current source, manual link ও published result overwrite হতে পারে।

---

## ৩. GitHub Web দিয়ে Upload করার বিস্তারিত নিয়ম

### ধাপ ১ — আগে backup

```text
GitHub → DigeeGlamour/click-tv
→ Code
→ Download ZIP
```

Backup ZIP আলাদা জায়গায় রাখবে।

### ধাপ ২ — নতুন package extract

`click-tv-advanced-code-only.zip` extract করো। Extract করার পরে parent folder-এর ভেতরে `scan.py`, `scanner`, `config`, `.github` দেখা যাবে।

### ধাপ ৩ — Repository root-এ upload

```text
GitHub repository
→ Add file
→ Upload files
```

Extract করা folder-এর **ভেতরের সব content** drag করবে। Parent folder নিজে drag করবে না।

সঠিক path:

```text
/scan.py
/scanner/fast_pipeline.py
/.github/workflows/scan.yml
/config/settings.json
```

ভুল path:

```text
/click-tv-advanced-code-only/scan.py
```

### ধাপ ৪ — Replace নিশ্চিত করা

GitHub existing file replace/update দেখাবে। নতুন দুটি file অবশ্যই upload হয়েছে কি না দেখবে:

```text
scanner/content_router.py
scanner/fast_pipeline.py
```

### ধাপ ৫ — Commit

Commit message:

```text
Install advanced pipelined scanner and repair TV movie routing
```

Directly to `main` নির্বাচন করে commit করবে।

---

## ৪. Upload শেষে Repository-তে exact path check

এই fileগুলো click করে খুলবে:

```text
scan.py
config/settings.json
scanner/content_router.py
scanner/fast_pipeline.py
scanner/output.py
scanner/parsers/m3u_parser.py
.github/workflows/scan.yml
```

কোনোটির path-এর আগে অতিরিক্ত folder থাকলে upload ভুল হয়েছে।

---

## ৫. প্রথম Run-এর order

### Run-১: Channels

```text
Actions
→ Live Signal Scanner
→ Run workflow
→ Branch: main
→ Mode: channels
→ Run workflow
```

এটি আগে চালাতে হবে, কারণ প্রথম Channels run পুরোনো Bangla/Indian TV JSON-এর movie pollution clean করবে।

Expected log:

```text
Candidates: ... raw -> ... normalized -> ... candidate pool
Content routing corrections: tv->movies=...
Adaptive first wave: ...
[Steps 2+3/5] Running adaptive Global + BD verification pipeline...
Global progress: ...
BD pipeline progress: ...
Pipeline completed: ...
[Step 4a/5] Processing Live TV channels...
✅ SCAN COMPLETED
```

Run শেষে check:

```text
state/migrations/vod-routing-v1.json
```

এটি থাকলে one-time cleanup হয়েছে।

### Run-২: Movies

```text
Actions → Run workflow → movies
```

Expected log:

```text
Mode: movies
Content routing corrections: tv->movies=...
[Step 4b/5] Processing Movie VOD pagination...
```

Movie output path:

```text
data/movies/bangla/page-1.json
data/movies/hindi/page-1.json
data/movies/dubbed/page-1.json
data/movies/south-indian/page-1.json
data/movies/english/page-1.json
data/movies/mix/page-1.json
```

### Run-৩: Events

```text
Actions → Run workflow → events
```

Events mode একই run-এ Today Match ও Upcoming update করবে। আলাদাভাবে `today`, `events`, `upcoming` তিনটি পরপর চালানোর দরকার নেই।

---

## ৬. Colab V6 ব্যবহার

Package-এর ভেতরে:

```text
colab/Live_Signal_Colab_Live_Monitor_V6.ipynb
```

Colab-এ:

```text
File → Upload notebook
→ Live_Signal_Colab_Live_Monitor_V6.ipynb
```

Secrets:

```text
GITHUB_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Notebook access ON থাকবে।

Cell order:

```text
Cell 1 → Safe fresh repository setup
Cell 2 → Mode select + scan + push
Cell 3 → Scan সফল কিন্তু push fail হলে শুধু push retry
```

V6 mode list:

```text
channels
channels-discovery
events
today
upcoming
movies
movies-discovery
all
full-audit
```

Live monitor-এ দেখা যাবে:

```text
Global progress: completed/running/queued
BD pipeline progress: completed/submitted/running
candidate pool
adaptive skipped
pipeline elapsed time
changed files
Git commit/push
```

GitHub Actions ও Colab একই সময়ে চালাবে না।

---

## ৭. Automatic schedule

Workflow schedule:

```text
Events             → প্রতি ২ ঘণ্টা
Channels quick     → প্রতি ৬ ঘণ্টা
Channels discovery → প্রতিদিন একবার
Movies             → প্রতি দ্বিতীয় calendar date
```

`full-audit` শুধু manual রাখাই ভালো, কারণ এটি সবচেয়ে বড় candidate pool ব্যবহার করে।

---

## ৮. Speed settings — কী পরিবর্তন করবে না

Default balanced values:

```json
"pipeline": {
  "global_workers": 20,
  "minimum_global_inflight": 12,
  "bd_workers": 6,
  "per_host_limit": 3,
  "host_failure_threshold": 3,
  "host_cooldown_seconds": 90
}
```

একসঙ্গে `global_workers=40` এবং `bd_workers=20` দেবে না। এতে network congestion, timeout ও rate limit বেড়ে scan উল্টো slow হতে পারে।

Verification quality বজায় আছে:

```text
HLS master/media manifest check
variant check
latest segment sample
MP4/MKV media signature/range check
required headers preserved
maximum 2 BD/proxy attempts
```

---

## ৯. Quick ও Discovery mode-এর পার্থক্য

### Channels quick

```text
প্রতি group initial 1 candidate
Target 2 publishable link
Pool সর্বোচ্চ 6
Total pool সর্বোচ্চ 3200
```

### Channels discovery

```text
প্রতি group initial 2 candidate
Target 3 publishable link
বড় pool ও নতুন backup discovery
Total pool সর্বোচ্চ 5000
```

### Movies quick

```text
প্রতি movie initial 1
Target 1 valid
Pool সর্বোচ্চ 2
```

Movie title সাধারণত unique হওয়ায় একটি valid playable URL পেলেই অতিরিক্ত duplicate link পরীক্ষা বন্ধ হয়।

---

## ১০. Reports দিয়ে কীভাবে বুঝবে

### Planner report

```text
reports/preverification-plan.json
```

দেখাবে:

```text
input_candidates
after_exact_deduplication
candidate_pool_count
initial_wave_candidates
rerouted_counts
unknown_tv_category
global_cap
```

### Pipeline performance

```text
reports/pipeline-performance.json
```

দেখাবে:

```text
elapsed_seconds
global_network_checked
bd_proxy_submitted
final_publishable
adaptive_skipped
host_circuits_opened
final_global_inflight_limit
```

### Migration marker

```text
state/migrations/vod-routing-v1.json
```

### Scan summary

```text
reports/scan-summary.json
```

---

## ১১. Scan-এর পরে যা check করবে

TV Bangla/Indian file-এ `.mp4` বা `.mkv` থাকা উচিত নয়:

```text
data/channels/bangla.json
data/channels/indian.json
```

Movie output item-এ থাকা উচিত:

```json
"source_pipeline": "movies"
```

TV source থেকে উদ্ধার হওয়া Movie-তে debug metadata থাকতে পারে:

```json
"original_source_pipeline": "tv",
"pipeline_rerouted": true,
"routing_reason": "vod_extension:.mkv"
```

---

## ১২. Error হলে কী করবে

### Missing content_router/fast_pipeline

```text
test -f scanner/content_router.py
```

fail হলে file ভুল folder-এ upload হয়েছে।

### Push fail কিন্তু scan success

Colab V6-এর Cell 3 চালাবে। Scan আবার চালাবে না।

### `completed_with_warnings`

এটি failure নয়। Scan/output সফল হয়েছে, কিন্তু কিছু source/stream warning report হয়েছে।

### Movie count খুব কম

Check:

```text
reports/preverification-plan.json → rerouted_counts
reports/source-errors.json
reports/bd-verification.json
```

### TV file-এ Movie এখনো আছে

Check:

```text
state/migrations/vod-routing-v1.json
```

Marker migration-এর আগে ভুল করে তৈরি হয়ে থাকলে সেটি delete করে একবার `channels` run দিতে হবে। অন্য state file delete করবে না।

---

## ১৩. Rollback

Upgrade run করার আগে নেওয়া GitHub ZIP backup থেকে পুরোনো code restore করা যাবে। শুধু code rollback করতে হলে:

```text
scan.py
scanner/
config/settings.json
.github/workflows/scan.yml
.gitignore
```

পুরোনো version দিয়ে replace করবে। `data/` ও `state/` না মুছলেও হবে।

---

## ১৪. Validation

Package তৈরি করার পরে চালানো হয়েছে:

```text
python -m py_compile scan.py scanner/*.py scanner/parsers/*.py
python -m pytest -q
```

Unit tests cover:

```text
TV source direct movie → Movies reroute
Normal live manifest → TV থাকে
Movie category detection
Malformed comma title repair
Movies mode planner routing
Adaptive target পূরণ হলে unused candidate skip
```

Third-party public stream speed/availability প্রতিদিন বদলায়। তাই exact runtime guarantee করা যায় না; code-এর time budget, fallback এবং atomic publish পুরোনো good data রক্ষা করবে।
