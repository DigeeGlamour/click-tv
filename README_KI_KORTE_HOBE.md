# এই ফোল্ডারে কী আছে

এই session-এ পাওয়া ২টা নতুন bug-এর fix — দুটোই আমি নিজে scan চালিয়ে reproduce করে, fix করে, আবার চালিয়ে প্রমাণ করেছি।

## Bug ১ — Local PC script-এ rebase fail (তোমার screenshot ২)

`CLICK_TV_EASY_PAT_SCAN.cmd` স্ক্রিপ্টের ভিতরে (এটা আসলে embedded PowerShell code, `.cmd` extension দিয়ে wrap করা) নিজস্ব `git rebase origin/main` logic ছিল, কিন্তু সেখানে আগেরবার GitHub Actions workflow-এ যে fix (`-X theirs`) দিয়েছিলাম সেটা বসানো হয়নি। তাই GitHub Actions আর তোমার Local PC — দুটো আলাদা scan প্রায় একসাথে চললে, GitHub Actions push করার পর Local PC-র push রেবেসে গিয়ে conflict-এ আটকে যেত। ঠিক তোমার screenshot-এর error।

## Bug ২ — "manifest count mismatch" / Cloudflare build fail (স্ক্রিনশট ১ আর ৩)

এটা একদম নতুন bug, যেটা আমার আগের fix-এরই একটা side-effect ছিল। আমি real scanner (`scan.py`) দুইবার parallel চালিয়ে GitHub Actions + Local PC-র race অবস্থা নিজে তৈরি করে দেখেছি:

- দুই scan যখন প্রায় একসাথে `data/playback-sources.json`-এ নতুন নতুন channel/movie যোগ করে, git rebase সেই বড় list-টা ঠিকই merge করে ফেলে (কোনো conflict ছাড়াই, কারণ দুইজন আলাদা entry যোগ করছে)।
- কিন্তু সেই ফাইলের ভিতরে একটা ছোট সংখ্যা থাকে — `"count": 18672` — যেটা দুইজনই বদলায়। এই একটা লাইনেই আসল conflict হয়, আর সমাধানের নিয়ম (`-X theirs`) শুধু একজনের পুরনো সংখ্যাটাই রেখে দেয়।
- ফল: list-এ আছে 18828টা record, কিন্তু ফাইলে লেখা আছে 18672। এটাই Cloudflare/validator ধরে ফেলে আর build fail করায়। ঠিক একই কারণে `data/manifest.json`-এর channel count-ও mismatch হতে পারে।

**সমাধান:** rebase হওয়ার ঠিক পরে, push করার আগে, একটা নতুন ছোট script (`scripts/reconcile-generated-counts.py`) চালানো হয় — এটা আসল ফাইলের ভিতরের list গুনে declared count-টা ঠিক করে দেয়। GitHub Actions workflow আর Local PC script — দুই জায়গাতেই এটা বসানো হয়েছে।

আমি এই পুরো scenario (দুইটা real scan.py চালানো → push → rebase → mismatch তৈরি হওয়া → নতুন script দিয়ে ঠিক হওয়া → validator-এ Errors: 0) নিজে বারবার চালিয়ে test করেছি।

---

# কোন ফাইল কোথায় বসাবে (path অনুযায়ী)

তোমার GitHub repo-তে (`DigeeGlamour/click-tv`) এই ৩টা **replace** আর ২টা **নতুন** ফাইল upload করো — নিচে ঠিক এই ফোল্ডারেরই structure দেওয়া আছে, তাই path মেলাতে সমস্যা হবে না।

| এই ফোল্ডারে path | GitHub-এ path | কী |
|---|---|---|
| `CLICK_TV_EASY_PAT_SCAN.cmd` | `CLICK_TV_EASY_PAT_SCAN.cmd` | **Replace** — rebase-এ `-X theirs` + reconciliation step যোগ হয়েছে |
| `.github/workflows/scan.yml` | `.github/workflows/scan.yml` | **Replace** — rebase-এর পর reconciliation step যোগ হয়েছে |
| `scanner/output.py` | `scanner/output.py` | **Replace** — নতুন `_reconcile_manifest_counts()` ফাংশন |
| `scripts/reconcile-generated-counts.py` | `scripts/reconcile-generated-counts.py` | **নতুন ফাইল** |
| `tests/test_manifest_reconciliation.py` | `tests/test_manifest_reconciliation.py` | **নতুন ফাইল** (ঐচ্ছিক, তবে দিলে ভবিষ্যতে এই bug আবার ফিরলে ধরা পড়বে) |

## Upload করার পর

1. এই ৫টা ফাইল GitHub-এ upload/replace করো (path মিলিয়ে)।
2. তোমার Local PC-তে `%USERPROFILE%\Downloads\ClickTV-Data-Scanner` ফোল্ডারটা (এটা `CLICK_TV_EASY_PAT_SCAN.cmd`-এর নিজস্ব clone, তোমার আসল project folder থেকে আলাদা) **মুছে দিও না** — পরের বার script চালালেই এটা নিজে থেকে `git pull` করে নতুন code নিয়ে নেবে।
3. পরের বার GitHub Actions বা Local PC scan চালালে rebase আর manifest mismatch — দুটো সমস্যাই আর হবে না।

## যা যাচাই করেছি

- Local-এ real `scan.py channels` আর `scan.py today` দুটো mode আলাদা clone-এ সত্যিকারের চালিয়েছি (কোনো mock/fake data না)।
- দুটোর push-কে race অবস্থায় ফেলে (A push করার পর B rebase করেছে) দেখেছি কোথায় mismatch তৈরি হয়।
- নতুন fix দিয়ে ঠিক একই race আবার চালিয়ে দেখেছি push সফল হয়েছে **আর** `bash scripts/build-pages.sh` চালিয়ে `Errors: 0` পেয়েছি।
- `CLICK_TV_EASY_PAT_SCAN.cmd`-এর ভিতরের PowerShell code আলাদাভাবে PowerShell parser দিয়ে syntax check করেছি (কোনো error নেই) এবং real fixed logic PowerShell-এ চালিয়ে একই race-এ push সফল হয়েছে।
- পুরো test suite চালিয়েছি: **164টা test pass**।
