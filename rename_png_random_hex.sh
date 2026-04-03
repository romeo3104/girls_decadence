#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# rename_png_random_hex.sh
#
# 概要:
#   カレントディレクトリ直下の PNG ファイルを、ランダムな 32 桁の16進数 + .png にリネームします。
#
# 仕様:
#   - 対象: カレントディレクトリ直下のみ（サブディレクトリ内は対象外）
#   - 対象拡張子: .png（大文字小文字を区別しない）
#   - 除外:
#       * ファイル名先頭が「日時っぽい」もの（例: 20251231..., 20251231123456..., 2025-12-31...）
#       * 既に「32桁16進数.png」の形になっているもの
#   - 乱数生成:
#       * openssl があれば `openssl rand -hex 16`（16バイト=32桁hex）を使用
#       * openssl が無い場合は `/dev/urandom` + `od` で代替
#   - 衝突回避:
#       * 既存ファイル名と衝突しない新名称が出るまで生成を繰り返します
#       * `mv -n` により上書きはしません
#
# 使い方:
#   cd 対象ディレクトリ
#   chmod +x rename_png_random_hex.sh
#   ./rename_png_random_hex.sh
#
# 出力:
#   旧ファイル名 -> 新ファイル名
#
# 注意:
#   リネームは取り消しが難しいため、必要に応じて事前バックアップを推奨します。
# -----------------------------------------------------------------------------

# 先頭が「日時っぽい」ものは除外する（必要ならパターンを追加/調整）
# - 例: 20251231..., 2025-12-31..., 20251231123456...
is_datetime_prefix() {
  local name="$1"
  [[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]] && return 0  # YYYY-MM-DD...
  [[ "$name" =~ ^[0-9]{14} ]] && return 0                    # YYYYMMDDhhmmss...
  [[ "$name" =~ ^[0-9]{8} ]] && return 0                     # YYYYMMDD...
  return 1
}

# 16進数ランダム文字列を生成（openssl があれば優先）
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16   # 16バイト = 32桁hex
  else
    # openssl が無い場合の代替（/dev/urandom + od）
    dd if=/dev/urandom bs=16 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n'
  fi
}

# カレントディレクトリ直下のみ対象（サブディレクトリは対象外）
find . -maxdepth 1 -type f \( -iname '*.png' \) -print0 |
while IFS= read -r -d '' f; do
  name="${f#./}"

  # 先頭が日時ならスキップ
  if is_datetime_prefix "$name"; then
    continue
  fi

  # 既に 16進数32桁.png の形ならスキップ
  [[ "$name" =~ ^[0-9a-fA-F]{32}\.png$ ]] && continue

  # 衝突しない新ファイル名が出るまで回す
  while :; do
    new="$(rand_hex).png"
    [[ -e "./$new" ]] || break
  done

  mv -n -- "$f" "./$new"
  printf '%s -> %s\n' "$name" "$new"
done

