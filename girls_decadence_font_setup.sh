#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '%s %s\n' "$(date -Is)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---- trap で使うテンポラリ（グローバル）----
GD_TMPDIR=""

cleanup() {
  # set -u / set -e の影響を受けないように安全に掃除する
  set +e
  if [[ -n "${GD_TMPDIR:-}" && -d "${GD_TMPDIR}" ]]; then
    rm -rf -- "${GD_TMPDIR}"
  fi
}
trap cleanup EXIT

# ---- OS 検出（Ubuntu / Linux Mint想定） ----
detect_os() {
  local id="unknown" ver="unknown" pretty="unknown"
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    id="${ID:-unknown}"
    ver="${VERSION_ID:-unknown}"
    pretty="${PRETTY_NAME:-unknown}"
  elif [ -r /etc/lsb-release ]; then
    # shellcheck disable=SC1091
    . /etc/lsb-release
    id="$(echo "${DISTRIB_ID:-unknown}" | tr '[:upper:]' '[:lower:]')"
    ver="${DISTRIB_RELEASE:-unknown}"
    pretty="${DISTRIB_DESCRIPTION:-unknown}"
  fi
  echo "${pretty}|${id}|${ver}"
}

# ---- 依存パッケージ ----
ensure_deps() {
  log "apt: 依存パッケージを導入します（sudoが必要）"
  sudo apt-get update -y
  sudo apt-get install -y fontconfig ca-certificates git
}

# ---- fontconfig キャッシュクリア（ユーザ）----
clear_user_fontconfig_cache() {
  log "fontconfig: clear caches"
  rm -rf "${HOME}/.cache/fontconfig" "${HOME}/.fontconfig" 2>/dev/null || true
  fc-cache -r >/dev/null 2>&1 || true
  fc-cache -f -v >/dev/null 2>&1 || true
}

# ---- user 側の drop-in（conf.d）を作る（探索パスを明示して確実化）----
ensure_user_fontconfig_dropin() {
  local d="${HOME}/.config/fontconfig/conf.d"
  local conf="${d}/99-girls-decadence.conf"
  mkdir -p "${d}"
  log "fontconfig: installed user snippet: ${conf}"
  cat > "${conf}" <<'EOFCONF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <!-- Girls' Decadence fonts -->
  <dir>~/.local/share/fonts/girls-decadence</dir>
  <dir>~/.fonts/girls-decadence</dir>
</fontconfig>
EOFCONF
}

# ---- system 側の fontconfig drop-in を作る（探索パスを明示して確実化）----
ensure_system_fontconfig_dropin() {
  local conf="/etc/fonts/conf.d/99-girls-decadence.conf"
  log "fontconfig: install system drop-in: ${conf}（sudo）"
  sudo tee "${conf}" >/dev/null <<'EOFCONF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <!-- Girls' Decadence fonts -->
  <dir>/usr/local/share/fonts/girls-decadence</dir>
  <dir>/usr/share/fonts/truetype/girls-decadence</dir>
</fontconfig>
EOFCONF
}

# ---- Google Fonts を sparse-checkout で取得 ----
clone_google_fonts_sparse() {
  local tmpdir="$1"
  local repo="${tmpdir}/google-fonts"
  log "clone: https://github.com/google/fonts.git (sparse)"

  # 環境差を吸収（depth+filter が失敗したらフォールバック）
  if ! git clone --depth 1 --filter=blob:none --no-checkout https://github.com/google/fonts.git "${repo}" >/dev/null 2>&1; then
    git clone --filter=blob:none --no-checkout https://github.com/google/fonts.git "${repo}" >/dev/null
  fi

  pushd "${repo}" >/dev/null
  git sparse-checkout init --cone >/dev/null
  log "sparse-checkout: 必要フォントのみ取得"
  git sparse-checkout set \
    ofl/cormorantgaramond \
    ofl/cinzeldecorative \
    ofl/playfairdisplay \
    ofl/bodonimoda >/dev/null
  git checkout >/dev/null
  popd >/dev/null
  echo "${repo}"
}

# ---- TTF コピー（固定）----
copy_ttf_from() {
  local srcdir="$1"
  local dstdir="$2"
  mkdir -p "${dstdir}"
  local n=0
  while IFS= read -r -d '' f; do
    cp -f "${f}" "${dstdir}/"
    n=$((n+1))
  done < <(find "${srcdir}" -maxdepth 1 -type f \( -iname '*.ttf' -o -iname '*.otf' \) -print0)
  log "copy: ${srcdir} -> ${dstdir} (${n} files)"
}

# ---- “インストール確認” ：ファミリー名＋別形式＋パス断片を許容 ----
verify_fonts_any() {
  local family_pat
  family_pat='Cormorant[[:space:]]+Garamond|Cinzel[[:space:]]+Decorative|Playfair[[:space:]]+Display|Bodoni([[:space:]]+Moda)?'
  local path_pat
  path_pat='girls-decadence/'

  local out

  # 1) プログラムが使う形式（fc-list ":" file family）
  out="$(fc-list ":" file family 2>/dev/null || true)"
  if grep -Eiq "$family_pat" <<<"$out"; then return 0; fi
  if grep -Eiq "$path_pat" <<<"$out"; then return 0; fi

  # 2) ふつうの形式（fc-list）
  out="$(fc-list 2>/dev/null || true)"
  if grep -Eiq "$family_pat" <<<"$out"; then return 0; fi
  if grep -Eiq "$path_pat" <<<"$out"; then return 0; fi

  return 1
}

verify_program_style() { verify_fonts_any; }
verify_doc_style() { verify_fonts_any; }

show_matches_brief() {
  (fc-list ":" file family 2>/dev/null || true) \
    | grep -Ei 'Cormorant[[:space:]]+Garamond|Cinzel[[:space:]]+Decorative|Playfair[[:space:]]+Display|Bodoni|girls-decadence/' \
    | head -n 20 >&2 || true
}

# ---- 実行モード ----
MODE="user" # user|system
if [ "${1:-}" = "--system" ]; then MODE="system"; fi

main() {
  local osinfo; osinfo="$(detect_os)"
  local pretty="${osinfo%%|*}"; local rest="${osinfo#*|}"
  local id="${rest%%|*}"; local ver="${rest#*|}"

  log "os=${pretty} (id=${id}, version=${ver})"
  log "mode=${MODE}"
  log "env(before): FONTCONFIG_FILE=${FONTCONFIG_FILE-} FONTCONFIG_PATH=${FONTCONFIG_PATH-} FONTCONFIG_SYSROOT=${FONTCONFIG_SYSROOT-} XDG_CONFIG_HOME=${XDG_CONFIG_HOME-}"

  export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

  ensure_deps

  local pre_cnt
  pre_cnt="$(fc-list 2>/dev/null | wc -l || true)"
  log "health: fc-list total fonts (pre)=${pre_cnt}"

  clear_user_fontconfig_cache

  local post_cnt
  post_cnt="$(fc-list 2>/dev/null | wc -l || true)"
  log "health: fc-list total fonts (post-reset)=${post_cnt}"

  GD_TMPDIR="$(mktemp -d)"

  local repo; repo="$(clone_google_fonts_sparse "${GD_TMPDIR}")"

  local user_dest="${HOME}/.local/share/fonts/girls-decadence"
  local legacy_dest="${HOME}/.fonts/girls-decadence"
  local sys_dest="/usr/local/share/fonts/girls-decadence"
  local sys_last="/usr/share/fonts/truetype/girls-decadence"

  log "dest=${user_dest}"

  # ---- user install ----
  copy_ttf_from "${repo}/ofl/cormorantgaramond" "${user_dest}"
  copy_ttf_from "${repo}/ofl/cinzeldecorative" "${user_dest}"
  copy_ttf_from "${repo}/ofl/playfairdisplay" "${user_dest}"
  copy_ttf_from "${repo}/ofl/bodonimoda" "${user_dest}"

  ensure_user_fontconfig_dropin
  clear_user_fontconfig_cache

  log "fc-cache: rebuild (user)"
  fc-cache -f -v >/dev/null 2>&1 || true

  log "verify: fc-list (family/path, both formats)"
  if verify_program_style || verify_doc_style; then
    log "ok: detected (user)"
    show_matches_brief
    log "done: ${user_dest}"
    return 0
  fi

  # ---- legacy dir fallback ----
  log "fallback: install to legacy dir: ${legacy_dest}"
  copy_ttf_from "${repo}/ofl/cormorantgaramond" "${legacy_dest}"
  copy_ttf_from "${repo}/ofl/cinzeldecorative" "${legacy_dest}"
  copy_ttf_from "${repo}/ofl/playfairdisplay" "${legacy_dest}"
  copy_ttf_from "${repo}/ofl/bodonimoda" "${legacy_dest}"

  clear_user_fontconfig_cache
  fc-cache -f -v >/dev/null 2>&1 || true

  log "verify(after legacy): fc-list (family/path, both formats)"
  if verify_program_style || verify_doc_style; then
    log "ok: detected (legacy user dir)"
    show_matches_brief
    log "done: ${legacy_dest}"
    return 0
  fi

  # ---- system install fallback ----
  if [ "${MODE}" = "user" ]; then
    log "WARN: user install では検出できません。system install に切替えます（sudo）"
  fi

  log "system: install to ${sys_dest}"
  sudo mkdir -p "${sys_dest}"
  sudo cp -f "${user_dest}"/* "${sys_dest}/" 2>/dev/null || true
  sudo cp -f "${legacy_dest}"/* "${sys_dest}/" 2>/dev/null || true

  ensure_system_fontconfig_dropin

  log "fc-cache: rebuild (system)"
  sudo fc-cache -f -v >/dev/null 2>&1 || true
  fc-cache -f -v >/dev/null 2>&1 || true

  log "verify: fc-list (family/path, both formats)"
  if verify_program_style || verify_doc_style; then
    log "ok: detected (system)"
    show_matches_brief
    log "done: ${sys_dest}"
    return 0
  fi

  # ---- last resort: /usr/share/fonts/truetype ----
  log "system(last resort): install to ${sys_last}"
  sudo mkdir -p "${sys_last}"
  sudo cp -f "${sys_dest}"/* "${sys_last}/" 2>/dev/null || true

  sudo fc-cache -f -v >/dev/null 2>&1 || true
  fc-cache -f -v >/dev/null 2>&1 || true

  if verify_program_style || verify_doc_style; then
    log "ok: detected (system last resort)"
    show_matches_brief
    log "done: ${sys_last}"
    return 0
  fi

  log "ERROR: system install（/usr/local, /usr/share）でも fc-list に出ませんでした（fontconfig の個別調査が必要）"
  log "diag: fc-list head（参考）"
  fc-list 2>/dev/null | head -n 10 >&2 || true
  log "diag: fc-list \":\" file family head（参考）"
  fc-list ":" file family 2>/dev/null | head -n 10 >&2 || true
  exit 1
}

main "$@"

