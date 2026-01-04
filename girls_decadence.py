#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
girls_decadence.py

【概要】
単一の PNG 画像（入力は .png のみ）を対象に、SNS / Web掲載向けの体裁へ一括整形するスクリプトです。
主に「16:9 比率へのパディング（クロップなし）」「右上タイトル文字の描画」「PNG容量削減」を行います。

【処理フロー】
1) 入力PNGを読み込み（RGBに変換）
2) 元画像のアスペクト比を維持したまま、キャンバスを拡張して16:9比率に整形（クロップなし）
3) 右上にタイトル文字を描画（画像幅に応じてフォントサイズ・最大幅を自動調整）
   - `fc-list` が利用可能な場合、システムフォントから指定ファミリーを探索してランダム選択
   - 見つからない場合はフォールバックフォント（DejaVuSerif等）を使用
4) PNG量子化（減色）＋高圧縮で保存し、所定のサイズ削減率（目標）を満たすまで色数を段階的に下げて試行
   - 目標未達でも、最も小さくできた結果で保存

【出力ファイル名の仕様】
出力は入力ファイルと同じディレクトリに生成されます（上書きしません）。
形式: YYYYMMDD_HHMMSS_{元ファイル名}
例: 20260103_235959_input.png

【使用方法】
- 1ファイル処理（入力はPNGのみ）
    python girls_decadence.py /path/to/input.png

- 実行例（カレントのPNGを処理）
    python girls_decadence.py ./input.png

【依存関係】
- Python: 3.8以上
- Pythonライブラリ:
    pillow (PIL)

  インストール例:
    python3 -m pip install --upgrade pip
    python3 -m pip install pillow

- 推奨システムコマンド（フォント探索に使用）:
    fontconfig（`fc-list` / `fc-cache`）

  インストール例（Ubuntu / Linux Mint）:
    sudo apt-get update
    sudo apt-get install -y fontconfig

【フォントについて（任意）】
本スクリプトは、以下のフォントファミリー名を探索対象にします。

- Cormorant Garamond
- Cinzel Decorative
- Playfair Display
- Bodoni

注意:
- fontconfig上の表記揺れにより、例えば「Bodoni」は「Bodoni Moda」として検出される場合があります。
  本スクリプトの探索は “部分一致” のため、Bodoni Moda を含めた同系統ファミリーも拾う可能性があります。
- これらが未導入でも動作します（その場合フォールバックフォントを使用します）。

【フォントの自動セットアップ（推奨）】
同梱/併設の `girls_decadence_font_setup.sh` を使用すると、ユーザ領域（~/.local/share/fonts など）へ
対象フォントを取得・配置し、fontconfigキャッシュ更新まで自動化できます。

- 実行例:
    chmod +x ./girls_decadence_font_setup.sh
    ./girls_decadence_font_setup.sh

- 検出・反映が不安定な環境では、最終手段として system install を使う版（sudo必要）を用いる運用も可能です:
    ./girls_decadence_font_setup.sh --system

【手動セットアップ（ネットワーク制限がある場合など）】
1) 以下のいずれかへ .ttf/.otf を配置:
   - ~/.local/share/fonts/girls-decadence/
   - ~/.fonts/girls-decadence/（互換用）

2) fontconfigキャッシュ更新:
    fc-cache -f -v

3) 検出確認（例）:
    fc-list ":" file family | grep -Ei 'Cormorant|Cinzel|Playfair|Bodoni|girls-decadence/'

【注意】
- 入力はPNGのみ対応です（.png以外はエラー）。
- 透かし文字（タイトル）は右上固定で、テキスト内容は設定クラス AppConfig.title_text で定義されています。
- 量子化により、グラデーション等でバンディングが発生する可能性があります（仕様）。

【設計思想】
- 非破壊的な整形: 元画像のアスペクト比を維持し、クロップ（切り抜き）を行わずに余白（パディング）で16:9化します。
- 動的な可読性確保: 画像サイズに応じてフォントサイズやタイトル最大幅を動的に計算し、レイアウト崩れを抑えます。
- 容量の最適化: 視覚品質を維持しつつ、パレット減色（量子化）で目標の圧縮率達成を試みます。

【前提環境】
- OS: Linux/Unix系（`fc-list` が利用可能だとフォント選択の幅が広がります）
- Ubuntu 24.04 / Linux Mint 21.x 系での利用を想定（Debian/Ubuntu系のパッケージ管理を前提とした説明を含みます）
"""

from __future__ import annotations

import io
import math
import logging
import datetime
import random
import subprocess
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont

# =========================
# ロギング設定
# =========================
logger = logging.getLogger(__name__)

DEFAULT_LOG_LEVEL = "INFO"

# 終了コード
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

USAGE_TEMPLATE = "Usage: python {script} /path/to/input.png"


def setup_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """
    ログ出力の設定を行います。
    
    Args:
        level (str): ログレベル (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


# =========================
# 設定管理クラス
# =========================
@dataclass(frozen=True)
class AppConfig:
    """アプリケーションの定数・設定を管理するデータクラス"""

    # -------------------------
    # 画像整形（16:9パディング）
    # -------------------------
    target_aspect_ratio: float = 16.0 / 9.0
    aspect_ratio_epsilon: float = 1e-6

    # 16:9 拡張の背景色
    pad_bg_color: Tuple[int, int, int] = (255, 255, 255)

    # -------------------------
    # タイトル（右上テキスト）
    # -------------------------
    title_text: str = "Girls' Decadence"
    title_color_rgb: Tuple[int, int, int] = (192, 192, 192)  # シルバー
    title_margin_top: int = 24
    title_margin_right: int = 24
    title_max_width_ratio: float = 0.28
    title_max_width_min: int = 80

    # -------------------------
    # フォント
    # -------------------------
    font_families: List[str] = field(default_factory=lambda: [
        "Cormorant Garamond",
        "Cinzel Decorative",
        "Playfair Display",
        "Bodoni",
    ])
    font_size_ratio: float = 0.030
    font_size_min: int = 12
    font_size_shrink_factor: float = 0.92

    # フォールバックフォントパス (Linux環境向け)
    fallback_font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    fallback_font_label: str = "Fallback Font"

    # fontconfig（fc-list）関連
    fc_list_cmd: Tuple[str, ...] = ("fc-list", ":", "file", "family")
    font_preferred_style_keywords: Tuple[str, ...] = ("regular", "roman", "book")

    # ランダムシードの設定
    random_seed_use_timestamp: bool = True

    # -------------------------
    # PNG圧縮（量子化 + 保存）
    # -------------------------
    # 目標サイズ削減率 (1 - out/in)
    target_reduction: float = 0.70

    # 最大試行回数
    max_tries: int = 5

    # PNG量子化の色数ステップ
    palette_colors_steps: List[int] = field(default_factory=lambda: [256, 128, 64, 32, 16])

    # 量子化パラメータ
    quantize_colors_min: int = 2
    quantize_colors_max: int = 256
    quantize_method: int = 2  # PIL quantize: method=2 (Fast Octree)

    # PNG保存パラメータ
    png_optimize: bool = True
    png_compress_level: int = 9


# =========================
# ユーティリティ関数
# =========================
def now_jst() -> datetime.datetime:
    """現在の日時（JST）を取得します。"""
    tz = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(tz=tz)


def build_output_path(input_path: Path) -> Path:
    """
    出力ファイルのパスを生成します。
    形式: {元ディレクトリ}/YYYYMMDD_HHMMSS_{元ファイル名}
    """
    dt = now_jst().strftime("%Y%m%d_%H%M%S")
    return input_path.parent / f"{dt}_{input_path.name}"


# =========================
# 画像処理クラス
# =========================
class ImageProcessor:
    """画像の読み込み、加工、保存を担当するクラス"""

    def __init__(self, config: AppConfig):
        self.config = config

    def load_image(self, path: Path) -> Image.Image:
        """画像をRGBモードで読み込みます。"""
        if not path.is_file():
            raise FileNotFoundError(f"入力ファイルが見つかりません: {path}")
        if path.suffix.lower() != ".png":
            raise ValueError(f"入力はPNGのみ対応です: {path}")

        try:
            return Image.open(path).convert("RGB")
        except Exception as e:
            raise RuntimeError(f"画像読み込みエラー: {path}, {e}") from e

    def pad_to_16_9(self, img: Image.Image) -> Image.Image:
        """
        画像を16:9のアスペクト比になるよう背景色でパディングします。
        既存の画像はリサイズせず、キャンバスの中央に配置します。
        """
        w, h = img.size
        target_ratio = self.config.target_aspect_ratio
        current_ratio = w / float(h)

        if abs(current_ratio - target_ratio) < self.config.aspect_ratio_epsilon:
            return img

        if current_ratio < target_ratio:
            # 横幅を広げる
            new_w = int(math.ceil(h * target_ratio))
            new_h = h
        else:
            # 縦幅を広げる
            new_w = w
            new_h = int(math.ceil(w / target_ratio))

        canvas = Image.new("RGB", (new_w, new_h), self.config.pad_bg_color)
        x = (new_w - w) // 2
        y = (new_h - h) // 2
        canvas.paste(img, (x, y))

        logger.info(f"16:9 パディング適用: src=({w},{h}) -> dst=({new_w},{new_h})")
        return canvas

    def _get_font_candidates(self) -> List[str]:
        """`fc-list` コマンドを使用してシステムフォントの一覧を取得します。"""
        if not shutil.which("fc-list"):
            logger.warning("`fc-list` コマンドが見つかりません。フォント検索をスキップします。")
            return []

        try:
            cmd = list(self.config.fc_list_cmd)
            # check=Trueでエラー時に例外を送出させる
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
            )
            return [line.strip() for line in p.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            logger.warning("`fc-list` の実行に失敗しました。")
            return []
        except Exception as e:
            logger.warning(f"フォント取得中に予期せぬエラーが発生しました: {e}")
            return []

    def _find_font_path(self, family: str, fc_lines: List[str]) -> Optional[str]:
        """指定されたフォミリー名に対応するフォントファイルのパスを検索します。"""
        family_lower = family.lower()
        candidates: List[str] = []

        for line in fc_lines:
            if ":" not in line:
                continue
            try:
                file_part, fam_part = line.split(":", 1)
                if family_lower in fam_part.lower():
                    if Path(file_part).is_file():
                        candidates.append(file_part)
            except ValueError:
                continue

        # 優先順位: Regular/Roman/Book -> 見つかった最初のもの
        for style in self.config.font_preferred_style_keywords:
            for f in candidates:
                if style in Path(f).name.lower():
                    return f

        return candidates[0] if candidates else None

    def _select_random_font(self) -> Tuple[str, Optional[str]]:
        """設定されたフォントファミリーの中からランダムに一つ選択します。"""
        fc_lines = self._get_font_candidates()
        available: List[Tuple[str, str]] = []

        for fam in self.config.font_families:
            fp = self._find_font_path(fam, fc_lines) if fc_lines else None
            if fp:
                available.append((fam, fp))

        if available:
            return random.choice(available)

        return self.config.fallback_font_label, None

    def _fit_font_size(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font_path: Optional[str],
        max_w: int,
        init_size: int
    ) -> ImageFont.FreeTypeFont:
        """指定された幅(max_w)に収まるようにフォントサイズを調整します。"""
        size = max(self.config.font_size_min, int(init_size))

        while size >= self.config.font_size_min:
            if font_path:
                try:
                    font = ImageFont.truetype(font_path, size)
                except OSError:
                    font = ImageFont.load_default()
            else:
                # フォールバック処理
                if Path(self.config.fallback_font_path).is_file():
                    try:
                        font = ImageFont.truetype(self.config.fallback_font_path, size)
                    except OSError:
                        font = ImageFont.load_default()
                else:
                    font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]

            if text_width <= max_w:
                return font

            size = max(self.config.font_size_min, int(size * self.config.font_size_shrink_factor))

        return ImageFont.load_default()

    def draw_title(self, img: Image.Image) -> Image.Image:
        """画像の右上にタイトルテキストを描画します。"""
        out = img.copy()
        draw = ImageDraw.Draw(out)

        # ランダムシードの設定（現在時刻ベース）
        if self.config.random_seed_use_timestamp:
            random.seed(int(now_jst().timestamp()))

        fam_name, font_path = self._select_random_font()
        logger.info(f"フォント選択: {fam_name} (path={font_path or 'Default'})")

        w, _ = out.size
        init_size = max(self.config.font_size_min, int(w * self.config.font_size_ratio))
        max_w = max(self.config.title_max_width_min, int(w * self.config.title_max_width_ratio))

        font = self._fit_font_size(draw, self.config.title_text, font_path, max_w, init_size)

        bbox = draw.textbbox((0, 0), self.config.title_text, font=font)
        tw = bbox[2] - bbox[0]

        x = max(0, w - self.config.title_margin_right - tw)
        y = max(0, self.config.title_margin_top)

        draw.text((x, y), self.config.title_text, fill=self.config.title_color_rgb, font=font)

        return out

    def compress_and_save(self, img: Image.Image, input_path: Path, output_path: Path) -> None:
        """
        画像を量子化して保存します。
        目標の削減率に達するまで、色数を減らしながら試行します。
        """
        in_size = input_path.stat().st_size
        if in_size == 0:
            raise ValueError("入力ファイルのサイズが0です。")

        best_bytes: Optional[bytes] = None
        best_size: int = sys.maxsize

        tries = min(self.config.max_tries, len(self.config.palette_colors_steps))

        for i in range(tries):
            colors = self.config.palette_colors_steps[i]

            # 量子化処理
            colors_i = max(
                self.config.quantize_colors_min,
                min(int(colors), self.config.quantize_colors_max),
            )
            quantized = img.quantize(colors=colors_i, method=self.config.quantize_method)

            with io.BytesIO() as buf:
                quantized.save(
                    buf,
                    format="PNG",
                    optimize=self.config.png_optimize,
                    compress_level=self.config.png_compress_level,
                )
                out_bytes = buf.getvalue()

            out_size = len(out_bytes)
            reduction = 1.0 - (out_size / float(in_size))

            logger.info(f"圧縮試行 {i + 1}/{tries}: colors={colors}, size={out_size:,} bytes, reduction={reduction:.1%}")

            if out_size < best_size:
                best_bytes = out_bytes
                best_size = out_size

            if reduction >= self.config.target_reduction:
                with open(output_path, "wb") as f:
                    f.write(out_bytes)
                logger.info(f"目標達成: reduction={reduction:.1%} で保存しました: {output_path}")
                return

        # 目標未達の場合、最もサイズが小さかったものを保存
        if best_bytes is None:
            raise RuntimeError("圧縮データの生成に失敗しました。")

        with open(output_path, "wb") as f:
            f.write(best_bytes)

        final_reduction = 1.0 - (best_size / float(in_size))
        logger.warning(
            f"目標未達: Target={self.config.target_reduction:.1%}, Best={final_reduction:.1%}. "
            f"最小サイズで保存しました: {output_path}"
        )


# =========================
# メイン処理
# =========================
def main() -> int:
    setup_logging()

    # 引数チェック
    if len(sys.argv) != 2:
        print(USAGE_TEMPLATE.format(script=sys.argv[0]), file=sys.stderr)
        return EXIT_USAGE

    input_path = Path(sys.argv[1]).resolve()
    config = AppConfig()
    processor = ImageProcessor(config)

    try:
        logger.info(f"処理開始: {input_path}")

        # 1. ロード
        img = processor.load_image(input_path)

        # 2. 16:9 パディング
        img = processor.pad_to_16_9(img)

        # 3. タイトル描画
        img = processor.draw_title(img)

        # 4. 圧縮保存
        output_path = build_output_path(input_path)
        processor.compress_and_save(img, input_path, output_path)

        # 結果確認
        if output_path.exists():
            out_size = output_path.stat().st_size
            in_size = input_path.stat().st_size
            reduction = 1.0 - (out_size / float(in_size))
            logger.info(f"全工程完了: Original={in_size:,} -> Result={out_size:,} ({reduction:.1%} reduction)")

    except Exception as e:
        logger.error(f"処理中にエラーが発生しました: {e}", exc_info=True)
        return EXIT_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

