#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

log() { printf '%s %s\n' "$(date -Is)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

MODE="user"            # user|system
DEST_DIR=""            # empty -> auto
SKIP_APT=0
LEGACY_FONTS_FALLBACK=1  # user mode で ~/.fonts も使う（Mint対策）

usage() {
  cat <<'EOF'
Usage:
  bash girls_decadence_font_setup.sh [--system] [--dest DIR] [--skip-apt] [--no-legacy-fallback]

Options:
  --system              システム領域にインストール（/usr/local/share/fonts/girls-decadence）
  --dest DIR            インストール先ディレクトリを明示指定（user/systemどちらでも可）
  --skip-apt            apt による依存導入をスキップ（git/fontconfig 等が既にある前提）
  --no-legacy-fallback  user mode でも ~/.fonts を使わない（通常は不要）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --system) MODE="system"; shift ;;
    --dest) DEST_DIR="${2:-}"; [[ -n "$DEST_DIR" ]] || die "--dest にはDIRが必要です"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --no-legacy-fallback) LEGACY_FONTS_FALLBACK=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "不明な引数: $1（-h でヘルプ）" ;;
  esac
done

# ---- OS判定（ログ用）----
OS_NAME="unknown"
OS_ID="unknown"
OS_VERSION="unknown"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_NAME="${NAME:-unknown}"
  OS_ID="${ID:-unknown}"
  OS_VERSION="${VERSION_ID:-unknown}"
elif [[ -r /etc/lsb-release ]]; then
  # shellcheck disable=SC1091
  . /etc/lsb-release
  OS_NAME="${DISTRIB_DESCRIPTION:-unknown}"
  OS_ID="${DISTRIB_ID:-unknown}"
  OS_VERSION="${DISTRIB_RELEASE:-unknown}"
fi

log "os=${OS_NAME} (id=${OS_ID}, version=${OS_VERSION})"
log "mode=${MODE}"

if [[ -z "${DEST_DIR}" ]]; then
  if [[ "${MODE}" == "system" ]]; then
    DEST_DIR="/usr/local/share/fonts/girls-decadence"
  else
    DEST_DIR="${HOME}/.local/share/fonts/girls-decadence"
  fi
fi
log "dest=${DEST_DIR}"

# ---- 依存導入（Mint/Ubuntuとも apt） ----
if [[ "${SKIP_APT}" -eq 0 ]]; then
  log "apt: 依存パッケージを導入します（sudoが必要）"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates git fontconfig
else
  log "apt: スキップします（既存環境を前提）"
fi

command -v git >/dev/null 2>&1 || die "git が見つかりません"
command -v fc-cache >/dev/null 2>&1 || die "fc-cache が見つかりません（fontconfig を導入してください）"
command -v fc-list >/dev/null 2>&1 || die "fc-list が見つかりません（fontconfig を導入してください）"
command -v fc-scan >/dev/null 2>&1 || log "WARN: fc-scan が見つかりません（検証が弱くなります）"

TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "${TMPDIR}"; }
trap cleanup EXIT

REPO_URL="https://github.com/google/fonts.git"
REPO_DIR="${TMPDIR}/google-fonts"

log "clone: ${REPO_URL} (sparse)"
git clone --depth 1 --filter=blob:none --sparse "${REPO_URL}" "${REPO_DIR}" >/dev/null
cd "${REPO_DIR}"

FONT_DIRS=(
  "ofl/cormorantgaramond"
  "ofl/cinzeldecorative"
  "ofl/playfairdisplay"
  "ofl/bodonimoda"
)

log "sparse-checkout: 必要フォントのみ取得"
git sparse-checkout set "${FONT_DIRS[@]}" >/dev/null

# ---- インストール先作成 ----
if [[ "${MODE}" == "system" ]]; then
  log "mkdir: ${DEST_DIR}（sudo）"
  sudo mkdir -p "${DEST_DIR}"
else
  log "mkdir: ${DEST_DIR}"
  mkdir -p "${DEST_DIR}"
fi

copy_ttf() {
  local src="$1"
  local dst="$2"

  [[ -d "${src}" ]] || die "フォントディレクトリが見つかりません: ${src}（google/fonts の構成変更の可能性）"
  local count
  count="$(find "${src}" -type f -name '*.ttf' | wc -l | tr -d ' ')"
  [[ "${count}" != "0" ]] || die "TTFが見つかりません: ${src}"

  log "copy: ${src} -> ${dst} (${count} files)"
  if [[ "${MODE}" == "system" ]]; then
    find "${src}" -type f -name '*.ttf' -print0 | sudo xargs -0 -I{} cp -f {} "${dst}/"
  else
    find "${src}" -type f -name '*.ttf' -print0 | xargs -0 -I{} cp -f {} "${dst}/"
  fi
}

for d in "${FONT_DIRS[@]}"; do
  copy_ttf "${REPO_DIR}/${d}" "${DEST_DIR}"
done

# ---- user mode: fontconfig のユーザ設定を「fonts.conf」で強制的に有効化（Mint対策）----
# ※ 既存 fonts.conf がある場合は上書きしない（conf.d 方式が効く環境もあるため）
install_user_fontconfig() {
  local base="${HOME}/.config/fontconfig"
  local fonts_conf="${base}/fonts.conf"
  local confd="${base}/conf.d"
  local snippet="${confd}/99-girls-decadence.conf"

  mkdir -p "${confd}"

  # conf.d スニペット（効く環境ではこれだけでOK）
  cat > "${snippet}" <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>~/.local/share/fonts</dir>
  <dir>~/.local/share/fonts/girls-decadence</dir>
  <dir>~/.fonts</dir>
</fontconfig>
EOF

  # Mint等で「conf.d が読まれない/読まれにくい」場合に備えて fonts.conf を作る（無ければ）
  if [[ ! -f "${fonts_conf}" ]]; then
    log "fontconfig: create user fonts.conf: ${fonts_conf}"
    cat > "${fonts_conf}" <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <!-- User font dirs (Ubuntu 24.04 / Linux Mint 21.x compatible) -->
  <dir>~/.local/share/fonts</dir>
  <dir>~/.local/share/fonts/girls-decadence</dir>
  <dir>~/.fonts</dir>
</fontconfig>
EOF
  else
    log "fontconfig: user fonts.conf exists; keep as-is: ${fonts_conf}"
    log "fontconfig: installed conf.d snippet: ${snippet}"
  fi
}

# ---- 検証関数（プログラムと同じ fc-list 形式）----
verify_fonts() {
  # Bodoni は実体が Bodoni Moda でも部分一致で拾える前提（プログラム仕様）
  local pat='Cormorant[[:space:]]*Garamond|Cinzel[[:space:]]*Decorative|Playfair[[:space:]]*Display|Bodoni'
  fc-list : file family 2>/dev/null | grep -Eiq "${pat}"
}

# ---- fontconfig キャッシュ再生成 ----
log "fontconfig: clear user cache"
rm -rf "${HOME}/.cache/fontconfig" 2>/dev/null || true
rm -rf "${HOME}/.fontconfig" 2>/dev/null || true

if [[ "${MODE}" == "user" ]]; then
  install_user_fontconfig
fi

log "fc-cache: rebuild"
if [[ "${MODE}" == "system" ]]; then
  sudo fc-cache -f -v >/dev/null
else
  fc-cache -f -v >/dev/null
fi

log "verify: fc-list : file family | grep"
if verify_fonts; then
  log "OK: 主要フォントが検出されました（プログラムが拾える状態です）"
  log "done: ${DEST_DIR}"
  exit 0
fi

# ---- user mode fallback: ~/.fonts にも置く（Mintで効きやすい）----
if [[ "${MODE}" == "user" && "${LEGACY_FONTS_FALLBACK}" -eq 1 ]]; then
  LEGACY_DIR="${HOME}/.fonts/girls-decadence"
  log "fallback: install to legacy dir: ${LEGACY_DIR}"
  mkdir -p "${LEGACY_DIR}"

  # symlink で重複回避（古い環境でも読みやすい）
  shopt -s nullglob
  for f in "${DEST_DIR}"/*.ttf; do
    ln -sf "${f}" "${LEGACY_DIR}/$(basename "${f}")"
  done
  shopt -u nullglob

  rm -rf "${HOME}/.cache/fontconfig" 2>/dev/null || true
  fc-cache -f -v "${HOME}/.fonts" >/dev/null || true
  fc-cache -f -v >/dev/null || true

  log "verify(after legacy): fc-list : file family | grep"
  if verify_fonts; then
    log "OK: ~/.fonts フォールバック後に主要フォントが検出されました"
    log "done: ${DEST_DIR}"
    exit 0
  fi
fi

# ---- ここまで来たら: フォントファイル自体はあるが、fontconfig が拾えていない ----
log "WARN: fc-list で主要フォントが検出できませんでした（fontconfig 側の個別調査が必要）"
log "diag: fc-scan で family を確認（参考）"
if command -v fc-scan >/dev/null 2>&1; then
  fc-scan --format '%{file}\t%{family}\t%{style}\n' "${DEST_DIR}"/*.ttf | head -n 80 >&2 || true
fi
log "hint: 最終手段として system install を使用してください:"
log "hint:   bash ./girls_decadence_font_setup.sh --system"
exit 1

