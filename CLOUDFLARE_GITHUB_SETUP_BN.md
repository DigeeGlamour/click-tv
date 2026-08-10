# Click TV: সহজ Git + Pages Catalogue — A-to-Z Setup

## Final scanner policy (এই নিয়মগুলো পরিবর্তন করবেন না)

বর্তমান scanner চারটি ধাপ সবসময় এই ক্রমে চালায়: **Upcoming → Today → TV Channels → Movies**। `all` mode-এ একটি ধাপ পুরো শেষ ও publish হওয়ার পর পরের ধাপ শুরু হয়। কোনো source silently sample/skip করা হয় না; প্রতিটি unique playback configuration network-এ verify হয়।

- Upcoming-এর ৫টি, Today-এর ৬টি, TV-এর ১০টি এবং Movies-এর ২টি configured remote source সব scan হবে। `config/sources.json`-এ এই final list আছে।
- একই URL হলেও Cookie, Authorization, headers, token/query, DRM key/license অথবা playback profile আলাদা হলে scanner সেটিকে আলাদা source হিসেবে রাখবে। সব একই হলে exact duplicate; provenance merge হবে।
- একই channel/movie-এর সবচেয়ে ভালো verified source primary; সর্বোচ্চ ৫টি active backup। আরও valid source থাকলে `standby`-তে থাকবে—হারাবে না।
- Channel, event ও movie publish হতে resolution অবশ্যই জানা এবং কমপক্ষে 720p হতে হবে। Unknown, 480p, 576p ইত্যাদি publish হবে না; review/report-এ কারণ থাকবে।
- Manual movie একই title/year-এর discovered movie-এর আগে primary থাকবে। Remote discovered stream backup/standby হবে। Existing movie index/page JSON structure বদলানো হয়নি।
- Header/Cookie/token/DRM scanner exact form-এ `data/playback-sources.json` catalogue-এ রাখে। Player `playback_id` পাঠায়; Worker catalogue পড়ে একই headers দিয়ে manifest, segment, key এবং license request করে।

## নিজের Windows PC থেকে full scan চালানো

সবচেয়ে সহজ পদ্ধতি: project folder খুলে root-এ থাকা `RUN_CLICK_TV_LOCAL_SCAN.cmd` double-click করুন। কালো window-তে `1` লিখে Enter দিলে recommended full scan শুরু হবে। শুধু Channels-এর জন্য `2`, Movies-এর জন্য `3`, Today-এর জন্য `4`, এবং Upcoming-এর জন্য `5` নির্বাচন করুন। Scan শেষ না হওয়া পর্যন্ত window বন্ধ করবেন না।

Windows warning দেখালে file-টির উপর right-click → **Properties** → নিচে **Unblock** থাকলে tick → **Apply** করুন; তারপর আবার double-click করুন। SmartScreen এলে **More info → Run anyway** দিন। এটি শুধু local project-এর trusted launcher।

BD-only link নিজের বাংলাদেশের Internet/IP দিয়ে পরীক্ষা করতে এই launcher বাংলাদেশের Internet connection-এ চালাবেন। বিকল্পভাবে project folder-এর PowerShell খুলে লিখতে পারেন:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-scan.ps1 -Mode all
```

Script প্রথমে প্রয়োজনীয় Python package install/check করবে, তারপর চার ধাপ sequentially চালাবে। Window বন্ধ করবেন না। Progress দেখতে আরেকটি PowerShell-এ চালাতে পারেন:

```powershell
Get-Content .\working\scan-progress.json
Get-Content .\working\pipeline-checkpoint.json
```

শেষে `data/`, `reports/`, এবং `state/` update হবে, Pages validator চলবে, তারপর script নিজে commit করে `main` branch-এ push করবে। এই auto-push শুধু আসল Git clone-এর ভিতরে কাজ করবে। Downloaded ZIP/snapshot-এ `.git` না থাকলে launcher scan শুরু করার আগেই পরিষ্কার error দেখাবে—প্রথমে নিচের GitHub Desktop clone ধাপ করুন। শুধু test scan করে push বন্ধ রাখতে PowerShell-এ শেষে `-NoPush` দিন।

শুধু একটি অংশ চালাতে `-Mode upcoming`, `-Mode today`, `-Mode channels`, অথবা `-Mode movies` ব্যবহার করা যায়। Final first deployment-এর জন্য `-Mode all` ব্যবহার করাই সঠিক। Windows emoji/Unicode console error আটকাতে launcher নিজেই UTF-8 চালু করে।

এই project-এ playback-এর জন্য আর Cloudflare KV, Cloudflare API token, Worker secret বা dashboard variable লাগবে না। Scanner যে URL এবং headers দিয়ে stream সত্যি play হয় বলে verify করবে, সেগুলো `data/playback-sources.json`-এ লিখবে। GitHub/Cloudflare Pages file-টি publish করবে। চারটি Playback Worker একই file পড়ে channel চালাবে।

> গুরুত্বপূর্ণ: `data/playback-sources.json` public। Cookie, token, Authorization এবং DRM key থাকলে যে কেউ file খুলে দেখতে পারবে। এটি আপনার বেছে নেওয়া সহজ ব্যবস্থা।

## নতুন system কীভাবে কাজ করে

1. Scanner source load করে এবং configured/source headers merge করে।
2. Verifier URL-এর pipe headers-সহ আসল request headers বানিয়ে manifest/segment পরীক্ষা করে।
3. Publish-এর সময় প্রতিটি playable source একটি স্থায়ী `playback_id` পায়।
4. সম্পূর্ণ URL, exact headers, DRM, stream type এবং profile `data/playback-sources.json`-এ যায়।
5. Cookie/token/DRM থাকা protected item-এর ছোট channel/movie JSON-এ raw URL/secret না রেখে শুধু `playback_id` রাখা হয়। সাধারণ item-এ URL-ও থাকে, তাই আগের direct-first playback নষ্ট হয় না।
6. Player protected source-এর জন্য Worker-কে `/hls?id=ctv_...` পাঠায়।
7. Worker Pages catalogue থেকে ID খুঁজে URL ও headers নেয়। Catalogue-এর exact header static profile-এর ওপর বসে, তাই scanner-এ ব্যবহৃত Cookie, Authorization, Referer, Origin এবং User-Agent প্রথম priority পায়।
8. HLS/DASH manifest-এর child segment, key ও subtitle URL Worker আবার নিজের URL-এ rewrite করে এবং একই playback ID বহন করে। ফলে child request-এও একই exact headers যায়।
9. DRM থাকলে Player `/drm?id=...` দিয়ে একই record-এর type-safe DRM data নেয়। Widevine/PlayReady license challenge Worker-এর `POST /license?id=...` route দিয়ে provider-এ যায়। FairPlay হলে certificate `GET /certificate?id=...` এবং license challenge `POST /license?id=...` দিয়ে যায়। Worker catalogue-এ থাকা exact license header-ই ব্যবহার করে; DRM bypass বা key অনুমান করে না।

## সঠিক কাজের order

এই order বদলাবেন না:

1. নতুন Worker v5.1 code চার Worker-এ deploy করুন।
2. চারটি `/health` URL-এ ঠিক `5.1.0` নিশ্চিত করুন।
3. Project-এর modified files GitHub repo-তে push করুন এবং Pages deployment সফল হতে দিন।
4. GitHub Actions থেকে scanner চালান; scanner public catalogue পূরণ করবে।
5. আবার Pages deployment শেষ হলে catalogue-এর `count` শূন্যের বেশি এবং channel/movie playback পরীক্ষা করুন।

Cloudflare Worker-এর **Variables and secrets ইতিমধ্যেই empty**—এটাই বর্তমান system-এর সঠিক অবস্থা। সেখানে কিছু add বা delete করার ধাপ নেই। Worker configuration সরাসরি public Pages-এর `data/playback-sources.json` এবং `data/allowed-hosts.json` থেকে আসে।

## ধাপ 1 — Worker v5.1 code deploy

একই কাজ নিচের চার Worker-এর প্রতিটিতে করতে হবে:

- `raspy-meadow-9279`
- `stream-proxy-3`
- `stream-proxy-4`
- `stream-proxy-5`

প্রতিটি Worker-এর জন্য:

1. Browser-এ `https://dash.cloudflare.com/` খুলে login করুন।
2. বাঁ পাশ থেকে **Workers & Pages** খুলুন।
3. প্রথম Worker-এর নাম চাপুন।
4. উপরের **Edit code** চাপুন।
5. Local project-এর `Proxy code.txt` file Notepad/VS Code-এ খুলুন। এটি `workers/playback-proxy/src/index.js`-এর exact একই v5.1 code; dashboard copy সহজ করার জন্য রাখা হয়েছে।
6. পুরো file select করুন (`Ctrl+A`) এবং copy করুন (`Ctrl+C`)।
7. Cloudflare editor-এর পুরোনো code পুরো select করে নতুন code paste করুন। আংশিক paste করবেন না।
8. **Deploy** চাপুন এবং success message আসা পর্যন্ত অপেক্ষা করুন।
9. একই exact code বাকি তিন Worker-এ deploy করুন। কোনো Worker-এর জন্য code edit করে নাম বদলাতে হবে না; hostname দেখে code নিজেই `play-proxy-1` থেকে `play-proxy-4` ঠিক করে।

## ধাপ 2 — Worker health verify

এক এক করে browser-এ খুলুন:

- `https://raspy-meadow-9279.juelgrsan3679.workers.dev/health`
- `https://stream-proxy-3.juelgrsan3679.workers.dev/health`
- `https://stream-proxy-4.juelgrsan3679.workers.dev/health`
- `https://stream-proxy-5.juelgrsan3679.workers.dev/health`

প্রতিটিতে অন্তত এগুলো থাকতে হবে:

```json
{
  "ok": true,
  "version": "5.1.0",
  "protected_playback": true,
  "configuration_storage": "git_pages_json",
  "dashboard_configuration_required": false
}
```

প্রথমটির `name` হবে `play-proxy-1`, দ্বিতীয়টির `play-proxy-2`, তৃতীয়টির `play-proxy-3`, চতুর্থটির `play-proxy-4`। কোনো একটিতে `5.1.0` ছাড়া অন্য version দেখালে সেই Worker-এ নতুন code deploy হয়নি—আগে সেটি ঠিক করুন।

> Cloudflare Worker variables এবং GitHub Actions secrets এক জিনিস নয়। Worker dashboard empty থাকবে। GitHub repository-এর **Actions secrets**-এ `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TMDB_API_KEY`, `TMDB_API_TOKEN` বা `PRIVATE_MOVIE_SOURCE_TOKEN` থাকলে সেগুলো scanner/notification/movie metadata-এর জন্য; Worker playback configuration নয়।

## ধাপ 3 — এই folder-এর changes GitHub-এ নেওয়া

বর্তমান open folder একটি downloaded snapshot; এখানে `.git` folder নেই। তাই এখান থেকে সরাসরি Push করা যাবে না। সহজ পদ্ধতি GitHub Desktop:

1. GitHub Desktop খুলুন।
2. **File → Clone repository** চাপুন।
3. URL tab-এ `https://github.com/DigeeGlamour/click-tv` দিন।
4. একটি নতুন local folder নির্বাচন করে **Clone** চাপুন।
5. বর্তমান analyzed project folder থেকে নিচের **পুরো folder-গুলো** cloned folder-এ copy করুন এবং merge/replace অনুমতি দিন: `.github`, `config`, `data`, `manual`, `reports`, `scanner`, `scripts`, `site`, `state`, `tests`, `workers`। এতে scanner source, generated verified data এবং workflow একসঙ্গে sync হবে। `archive`, `dist`, `node_modules`, `working`, `__pycache__` copy করবেন না। Clone-এর hidden `.git` folder কখনো copy, replace বা delete করবেন না।
6. Root থেকে এই file-গুলোও cloned folder-এর root-এ copy/replace করুন: `scan.py`, `requirements.txt`, `ClickTV_Colab_FINAL_EASY_5_MODE.ipynb`, `Proxy code.txt`, `CLOUDFLARE_GITHUB_SETUP_BN.md`, `.gitignore`। তারপর বিশেষভাবে নিশ্চিত করুন নিচের critical file-গুলো clone-এ গেছে:

   - `scanner/playback_profiles.py`
   - `scanner/output.py`
   - `scanner/series.py`
   - `scanner/normalizer.py`
   - `scanner/drm.py`
   - `scanner/movies.py`
   - `scanner/parsers/m3u_parser.py`
   - `scanner/parsers/direct_stream.py`
   - `scanner/parsers/json_parser.py`
   - `scanner/parsers/url_list_parser.py`
   - `data/playback-sources.json`
   - `site/assets/js/app.js`
   - `site/runtime-config.json`
   - `site/sw.js`
   - `workers/playback-proxy/src/index.js`
   - `workers/playback-proxy/wrangler.toml`
   - `Proxy code.txt`
   - `.github/workflows/scan.yml`
   - `scripts/build-pages.sh`
   - `scripts/validate-pages.py`
   - `scripts/browser-runtime-check.mjs`
   - `tests/playback-worker-runtime.mjs`
   - `tests/test_playback_profiles.py`
   - `tests/test_drm_contract.py`
   - `tests/test_content_router.py`

7. `scripts/upload_playback_profiles.py` clone-এ পুরোনো version থেকে থাকলে delete করুন—নতুন system-এ KV uploader নেই।
8. GitHub Desktop-এর Changes list review করুন। Cookie/token value accidentalভাবে অন্য report/runtime file-এ যোগ হয়েছে কি না দেখুন। Catalogue public হওয়ায় `data/playback-sources.json` অবশ্যই commit হবে।
9. Commit message দিন: `Sync public playback catalogue and proxy v5.1`
10. **Commit to main**, তারপর **Push origin** চাপুন।

## ধাপ 4 — Cloudflare Pages deploy verify

GitHub Push-এর পরে Cloudflare Dashboard → **Workers & Pages** → Click TV Pages project → **Deployments** খুলুন। নতুন deployment-এর status **Success** হওয়া পর্যন্ত অপেক্ষা করুন। এরপর খুলুন:

- `https://clicktv.pages.dev/`
- `https://clicktv.pages.dev/data/playback-sources.json`

প্রথম push-এ catalogue-এর `count` শূন্য থাকতে পারে; এটি bootstrap file। পুরোনো generated channel data-তে raw URL থাকায় Worker-এর legacy route দিয়ে site চলবে। পরের scanner run catalogue পূরণ করবে।

## ধাপ 5 — Scanner চালানো

1. GitHub repo খুলুন।
2. উপরে **Actions** চাপুন।
3. বাঁ পাশে **Live Signal Scanner** নির্বাচন করুন।
4. **Run workflow** চাপুন।
5. প্রথমে `channels` mode নির্বাচন করে run করুন।
6. Run page-এ সবুজ check আসা পর্যন্ত অপেক্ষা করুন। বিশেষভাবে **Validate scanner files**, **Run scanner**, **Validate generated Cloudflare Pages output**, এবং **Commit and push updated data** step PASS হতে হবে।
7. তারপর প্রয়োজন অনুযায়ী `today`, `upcoming`, এবং `movies` আলাদাভাবে run করুন। প্রথম verification-এ `all` দরকার নেই।

প্রতিটি successful run `data/playback-sources.json` merge/update করে GitHub-এ commit করবে। কোনো Cloudflare API upload step নেই এবং কোনো KV secret দরকার নেই।

### তিন জায়গার auto-push নিয়ম

**GitHub Actions:** `Live Signal Scanner` workflow-এর `contents: write` permission আছে। Scanner এবং Pages validation PASS হলেই workflow `data/`, `reports/`, `state/` commit করে `main`-এ push করবে। Today/Upcoming-এ বর্তমানে publish করার মতো event একটিও না থাকলে scanner এখন আগের event JSON অক্ষত রেখে `completed_preserved` status-এ পরের ধাপে যাবে; এই স্বাভাবিক zero-result-এর জন্য `all` run আর বন্ধ হবে না। Channels/Movies zero হলে data রক্ষার জন্য run এখনো fail করবে।

**Google Colab:** `ClickTV_Colab_FINAL_EASY_5_MODE.ipynb` খুলে Colab-এর বাঁ পাশের key/Secrets icon-এ `GITHUB_TOKEN` দিন এবং Notebook access ON করুন। Token-এ `DigeeGlamour/click-tv` repository-এর Contents read/write permission থাকতে হবে। Private movie source একই token দিয়ে readable হলে আলাদা secret দরকার নেই; অন্য token হলে `PRIVATE_MOVIE_SOURCE_TOKEN` দিন। Poster/year metadata-এর জন্য চাইলে `TMDB_API_KEY` বা `TMDB_API_TOKEN`, notification-এর জন্য `TELEGRAM_BOT_TOKEN` ও `TELEGRAM_CHAT_ID` দিন। Cell 1 একবার, তারপর Cell 2 চালালেই scan → commit → rebase → push হবে। Push মাঝপথে fail হলে Cell 2 আবার চালালে pending local commit আগে push হবে।

**নিজের Windows PC — সবচেয়ে সহজ one-click:** শুধু `CLICK_TV_ONE_CLICK_ALL.cmd` double-click করুন। PAT copy/paste এবং GitHub Desktop লাগবে না। প্রথমবার Git Credential Manager browser খুললে GitHub-এ login করে Authorize/Continue দিন। একই launcher `Downloads\ClickTV-Auto` clone তৈরি, current fix sync, initial push, full local scan, Pages validation এবং final push করবে। পরেরবারও `Downloads\ClickTV-Auto\CLICK_TV_ONE_CLICK_ALL.cmd` এই একটিই চালাবেন।

## ধাপ 6 — Catalogue/header sync হাতে verify

Scanner এবং Pages deployment শেষ হলে:

1. `https://clicktv.pages.dev/data/playback-sources.json` খুলুন।
2. `count` শূন্যের বেশি কি না দেখুন।
3. একটি record-এ `url`, `headers`, `stream_type`, `header_profile` দেখুন। Protected source হলে Cookie/Authorization/DRM-ও এখানেই দেখা যাবে—এটি expected।
4. একটি channel JSON খুলুন, যেমন `https://clicktv.pages.dev/data/channels/bangla.json`।
5. channel-এর `playback_id` copy করে catalogue-এর `records`-এ একই ID search করুন। অবশ্যই মিলতে হবে। Build validator এখন missing ID থাকলে deployment validation fail করাবে।
6. Website-এ channel play করুন। Browser DevTools → Network-এ initial request `/hls?id=ctv_...` হওয়া উচিত। Manifest-এর segment request `/hls?...&pid=ctv_...` হবে।

এই verification PASS হলেই deployment শেষ। Cloudflare Worker-এর Variables and secrets page empty-ই থাকবে; সেখানে আর কোনো cleanup বা নতুন secret যোগ করতে হবে না।

## Manual channel-এ exact header দেওয়ার নিয়ম

Generated `data/playback-sources.json` হাতে edit করবেন না; পরের scan overwrite/merge করবে। Manual source-এ header দিন, scanner catalogue বানাবে। সবচেয়ে পরিষ্কার পদ্ধতি `manual/manual.json`:

```json
{
  "items": [
    {
      "name": "Example Channel",
      "url": "https://media.example/live/master.m3u8?token=example",
      "group_title": "TV: Bangla",
      "headers": {
        "User-Agent": "Exact scanner user agent",
        "Referer": "https://source.example/",
        "Origin": "https://source.example",
        "Cookie": "session=example",
        "Authorization": "Bearer example-token"
      },
      "proxy_mode": "proxy_only",
      "stream_type": "hls",
      "inherit_manifest_query": true
    }
  ]
}
```

শুধু যে header upstream সত্যি চায় সেটিই দিন। নামের spelling রাখুন: `User-Agent`, `Referer`, `Origin`, `Cookie`, `Authorization`। Token/query refresh হলে manual source update করে scanner আবার চালান। Scanner URL pipe header পেলেও merge করে, তবে Cookie-তে `&` বা special character থাকলে JSON format নিরাপদ ও পরিষ্কার।

DRM source হলে DRM type স্পষ্ট করে দিন। এক system-এর value অন্য system হিসেবে ব্যবহার করবেন না। উদাহরণ:

```json
{
  "drm": {
    "type": "widevine",
    "license_url": "https://license.example/widevine",
    "license_headers": {
      "Authorization": "Bearer exact-provider-token",
      "X-Device": "exact-device-value"
    }
  }
}
```

PlayReady-এর জন্য `type` হবে `playready`; FairPlay-এর জন্য `fairplay` এবং প্রয়োজন হলে `certificate_url`/`certificate_headers` দিন। ClearKey-এর জন্য `type: "clearkey"` এবং existing `clear_keys` KID:key data দিন। Scanner type না জেনে Widevine/PlayReady/FairPlay value-কে ClearKey ধরে নেবে না। Kodi/Toffee source-এর `inputstream.adaptive.stream_headers`/`manifest_headers` scanner Cookie, User-Agent ও অন্য safe header হিসেবে decode করবে; unsafe `Host`, connection এবং length header forward করবে না।

## Movie year না থাকলে কী হবে

Scanner কোনো movie-র year অনুমান করে বসাবে না। নিচের safe order ব্যবহার করে:

1. একই normalized title এবং **একই exact stream URL**-এর অন্য manual record-এ year থাকলে সেটি নেয়। একই নামের remake থাকলেও shared stream URL ভুল movie বেছে নেওয়া ঠেকায়।
2. তা না হলে GitHub Actions secret-এ TMDB configured থাকলে exact normalized title search করে। Exact title-এর শুধু একটি unambiguous year পেলেই নেয়।
3. একই title-এর ১৯৯৯ ও ২০২৬-এর মতো একাধিক movie থাকলে year খালি রাখে এবং `reports/manual-movie-year-resolution.json`-এ `ambiguous` লিখে। Guess করে দুই movie merge করে না।

GitHub-এ TMDB চালু করতে repo → **Settings → Secrets and variables → Actions → New repository secret** যান। আপনার credential অনুযায়ী `TMDB_API_TOKEN` অথবা `TMDB_API_KEY` দিন। Value কোনো public JSON/manual source-এ paste করবেন না। তারপর Movies scan আবার চালান। Report-এ প্রতিটি missing-year item-এর `resolved`, `ambiguous`, অথবা `not_found` reason দেখা যাবে। Manual file-এ নিশ্চিত year জানা থাকলে সেটি নিজে যোগ করাই সবচেয়ে নির্ভরযোগ্য। Existing declared year conflict scanner চুপচাপ rewrite করবে না।

## BD-only channel-এর নিশ্চিত নিয়ম

GitHub Actions ও Colab প্রতিটি configured source scan করবে, কিন্তু তাদের server Bangladesh residential IP নয়। তাই সাধারণ/global link সেখানে সম্পূর্ণ verify হবে; BD-only link cloud থেকে fail করলে সেটিকে সঙ্গে সঙ্গে dead ধরে source list থেকে হারানো হবে না—trusted এবং কমপক্ষে 720p evidence থাকলে pending/review state থাকতে পারে। আপনার বাংলাদেশের Internet/IP দিয়ে `scripts/run-local-scan.ps1` চালানোর ফল BD-only link-এর authoritative result। Local scan-এর updated `data/`, `reports/`, `state/` GitHub-এ push করলে Pages সেই verified result পাবে। Unknown resolution বা 720p-এর নিচের link cloud বা local—কোনোটিতেই publish হবে না।

## Proxy অন্য কেউ ব্যবহার ঠেকানোর বাস্তব সীমা

Worker শুধু `https://clicktv.pages.dev` Origin গ্রহণ করে, arbitrary URL-এর আগে generated allowed-host list পরীক্ষা করে, ID format validate করে এবং child URL signature পরীক্ষা করে। সাধারণ website সরাসরি browser থেকে proxy ব্যবহার করতে পারবে না। তবে public catalogue এবং public Worker ব্যবস্থায় একজন advanced user Origin header নকল করতে পারে; secret authentication ছাড়া ১০০% আটকানো সম্ভব নয়। শক্ত protection চাইলে ভবিষ্যতে KV/Access/token system ফিরিয়ে আনতে হবে।

## Final test checklist

- চার Worker `/health`: version `5.1.0` — PASS
- `configuration_storage`: `git_pages_json` — PASS
- Worker variables/KV ছাড়া health — PASS
- Pages catalogue HTTP 200 এবং valid JSON — PASS
- channel/movie `playback_id` catalogue record-এর সঙ্গে মেলে — PASS
- protected HLS manifest load — PASS
- segment/key request load — PASS
- Widevine/PlayReady license binary POST এবং FairPlay certificate/license route — PASS
- direct-first সাধারণ channel আগের মতো চলে — PASS
- Desktop এবং mobile-এ channel/movie/search — PASS
- DevTools Console-এ uncaught error নেই — PASS

কোনো ধাপ FAIL হলে পরের ধাপে যাবেন না। Worker `5.1.0`, Pages catalogue `count > 0`, এবং protected playback—তিনটি যাচাই করুন। Cloudflare Worker-এর Variables and secrets empty থাকাই expected; সেখানে কোনো value যোগ করার দরকার নেই।
