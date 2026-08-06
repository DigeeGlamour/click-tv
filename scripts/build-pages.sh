#!/usr/bin/env bash

# Click TV Cloudflare Pages Build Script
# শুধু public website file এবং generated JSON data dist/ folder-এ নেবে।

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="${ROOT_DIR}/site"
DATA_DIR="${ROOT_DIR}/data"
DIST_DIR="${ROOT_DIR}/dist"
VALIDATOR="${ROOT_DIR}/scripts/validate-pages.py"

log() {
  printf '\n[Click TV Build] %s\n' "$1"
}

fail() {
  printf '\n[Click TV Build ERROR] %s\n' "$1" >&2
  exit 1
}

require_file() {
  local file_path="$1"

  if [[ ! -f "${file_path}" ]]; then
    fail "Required file পাওয়া যায়নি: ${file_path#${ROOT_DIR}/}"
  fi

  if [[ ! -s "${file_path}" ]]; then
    fail "Required file empty: ${file_path#${ROOT_DIR}/}"
  fi
}

require_directory() {
  local directory_path="$1"

  if [[ ! -d "${directory_path}" ]]; then
    fail "Required folder পাওয়া যায়নি: ${directory_path#${ROOT_DIR}/}"
  fi
}

log "Build preparation শুরু হচ্ছে"

require_directory "${SITE_DIR}"
require_directory "${DATA_DIR}"
require_directory "${DATA_DIR}/channels"
require_directory "${DATA_DIR}/movies"

log "প্রয়োজনীয় public file পরীক্ষা করা হচ্ছে"

require_file "${SITE_DIR}/index.html"
require_file "${SITE_DIR}/runtime-config.json"
require_file "${SITE_DIR}/app.webmanifest"
require_file "${SITE_DIR}/sw.js"
require_file "${SITE_DIR}/_headers"
require_file "${SITE_DIR}/assets/css/app.css"
require_file "${SITE_DIR}/assets/css/series.css"
require_file "${SITE_DIR}/assets/css/final-design.css"
require_file "${SITE_DIR}/assets/js/app.js"
require_file "${SITE_DIR}/assets/js/series.js"

log "প্রয়োজনীয় scanner data পরীক্ষা করা হচ্ছে"

require_file "${DATA_DIR}/manifest.json"
require_file "${DATA_DIR}/today-match.json"
require_file "${DATA_DIR}/upcoming.json"

CHANNEL_CATEGORIES=(
  "bangla"
  "sports"
  "indian"
  "cartoon"
  "islamic"
  "foreign-news"
  "infotainments"
  "other"
)

for category in "${CHANNEL_CATEGORIES[@]}"; do
  require_file "${DATA_DIR}/channels/${category}.json"
done

MOVIE_CATEGORIES=(
  "bangla"
  "hindi"
  "english"
  "dubbed"
  "south-indian"
  "premium"
  "mix"
)

for category in "${MOVIE_CATEGORIES[@]}"; do
  require_directory "${DATA_DIR}/movies/${category}"
  require_file "${DATA_DIR}/movies/${category}/index.json"
done

require_directory "${DATA_DIR}/series"
require_file "${DATA_DIR}/series/manifest.json"

log "পুরোনো dist folder মুছে ফেলা হচ্ছে"

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

log "Website file dist root-এ copy করা হচ্ছে"

cp -a "${SITE_DIR}/." "${DIST_DIR}/"

log "Generated data dist/data folder-এ copy করা হচ্ছে"

mkdir -p "${DIST_DIR}/data"
cp -a "${DATA_DIR}/." "${DIST_DIR}/data/"

log "অপ্রয়োজনীয় placeholder এবং system file পরিষ্কার করা হচ্ছে"

find "${DIST_DIR}" \
  -type f \
  \( \
    -name ".gitkeep" \
    -o -name ".DS_Store" \
    -o -name "Thumbs.db" \
    -o -name "*.pyc" \
  \) \
  -delete

find "${DIST_DIR}" \
  -type d \
  -name "__pycache__" \
  -prune \
  -exec rm -rf {} +

log "Private বা secret file dist-এর মধ্যে গেছে কি না পরীক্ষা করা হচ্ছে"

FORBIDDEN_NAMES=(
  ".env"
  ".dev.vars"
  "wrangler.toml"
  "requirements.txt"
  "scan.py"
)

for forbidden_name in "${FORBIDDEN_NAMES[@]}"; do
  if find "${DIST_DIR}" -type f -name "${forbidden_name}" | grep -q .; then
    fail "Forbidden file public build-এ পাওয়া গেছে: ${forbidden_name}"
  fi
done

FORBIDDEN_DIRECTORIES=(
  "scanner"
  "config"
  "manual"
  "reports"
  "state"
  "working"
  "tests"
  "workers"
  ".github"
)

for forbidden_directory in "${FORBIDDEN_DIRECTORIES[@]}"; do
  if [[ -d "${DIST_DIR}/${forbidden_directory}" ]]; then
    fail "Private folder public build-এ পাওয়া গেছে: ${forbidden_directory}/"
  fi
done

log "Final build-এর গুরুত্বপূর্ণ file পুনরায় পরীক্ষা করা হচ্ছে"

require_file "${DIST_DIR}/index.html"
require_file "${DIST_DIR}/runtime-config.json"
require_file "${DIST_DIR}/app.webmanifest"
require_file "${DIST_DIR}/sw.js"
require_file "${DIST_DIR}/_headers"
require_file "${DIST_DIR}/data/manifest.json"

if [[ -f "${VALIDATOR}" && -s "${VALIDATOR}" ]]; then
  log "Python Pages validator চালানো হচ্ছে"
  python3 "${VALIDATOR}" "${DIST_DIR}"
else
  log "validate-pages.py এখনো empty, তাই validation পরবর্তী ধাপে চালু হবে"
fi

TOTAL_FILES="$(find "${DIST_DIR}" -type f | wc -l | tr -d ' ')"
TOTAL_SIZE="$(du -sh "${DIST_DIR}" | awk '{print $1}')"

log "Build সফল হয়েছে"
printf 'Output folder: %s\n' "${DIST_DIR#${ROOT_DIR}/}"
printf 'Total files: %s\n' "${TOTAL_FILES}"
printf 'Total size: %s\n' "${TOTAL_SIZE}"
