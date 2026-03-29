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
4) PNG量子化（減色）＋高圧縮で保存し、視覚品質を極端に崩さない範囲で最小サイズ候補を採用
   - 目標削減率に達しても探索を打ち切らず、最後まで比較します
5) 圧縮後PNGを再入力として再圧縮し、これ以上サイズが縮まらなくなるまで反復します
   - 16:9化とタイトル描画は最初の1回だけ行い、反復対象はPNG圧縮のみです

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
- 圧縮は「前回結果より1バイト以上小さくならない」時点で停止します。
- 量子化により、グラデーション等でバンディングが発生する可能性があります（仕様）。

【設計思想】
- 非破壊的な整形: 元画像のアスペクト比を維持し、クロップ（切り抜き）を行わずに余白（パディング）で16:9化します。
- 動的な可読性確保: 画像サイズに応じてフォントサイズやタイトル最大幅を動的に計算し、レイアウト崩れを抑えます。
- 容量の最適化: 視覚品質を維持しつつ、パレット減色（量子化）で圧縮効率を引き上げます。

【前提環境】
- OS: Linux/Unix系（`fc-list` が利用可能だとフォント選択の幅が広がります）
- Ubuntu 24.04 / Linux Mint 21.x 系での利用を想定（Debian/Ubuntu系のパッケージ管理を前提とした説明を含みます）
"""

from __future__ import annotations

import datetime
import io
import logging
import math
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat, features

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


# =========================
# 設定・データモデル
# =========================
@dataclass(frozen=True)
class AppConfig:
    """アプリケーション全体の定数・設定を保持します。"""

    # -------------------------
    # 画像整形（16:9パディング）
    # -------------------------
    target_aspect_ratio: float = 16.0 / 9.0
    aspect_ratio_epsilon: float = 1e-6
    pad_bg_color: Tuple[int, int, int] = (255, 255, 255)

    # -------------------------
    # タイトル（右上テキスト）
    # -------------------------
    title_text: str = "Girls' Decadence"
    title_color_rgb: Tuple[int, int, int] = (192, 192, 192)
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
    fallback_font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    fallback_font_label: str = "Fallback Font"
    fc_list_cmd: Tuple[str, ...] = ("fc-list", ":", "file", "family")
    font_preferred_style_keywords: Tuple[str, ...] = ("regular", "roman", "book")
    random_seed_use_timestamp: bool = True

    # -------------------------
    # PNG圧縮（量子化 + 保存）
    # -------------------------
    target_reduction: float = 0.70
    max_tries: int = 0  # 0 以下の場合は候補を全件探索します
    palette_colors_steps: List[int] = field(default_factory=lambda: [
        256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48, 40, 32, 24, 16, 12, 10, 8
    ])
    quantize_colors_min: int = 8
    quantize_colors_max: int = 256
    quantize_method: int = 2
    quantize_enable_libimagequant: bool = True
    quantize_use_dither: bool = True
    quantize_try_without_dither: bool = True

    # 厳格モード: ほぼ視認できない差分のみ許容
    max_mean_abs_diff: float = 1.10
    max_rms_diff: float = 2.20
    max_large_diff_ratio: float = 0.0030

    # 拡張モード: 既定では使用しません。
    # 人間の目で分かる劣化を避ける要件を優先するため、通常選定は strict のみです。
    relaxed_max_mean_abs_diff: float = 1.80
    relaxed_max_rms_diff: float = 3.60
    relaxed_max_large_diff_ratio: float = 0.0100
    enable_relaxed_profile: bool = False

    large_diff_threshold: int = 8

    png_optimize: bool = True
    png_compress_level: int = 9
    iterative_recompression_enabled: bool = True
    iterative_max_passes: int = 20
    iterative_min_improvement_bytes: int = 1


@dataclass(frozen=True)
class CompressionCandidate:
    """1件の圧縮候補を保持します。"""

    label: str
    out_bytes: bytes
    out_size: int
    mean_abs_diff: float
    rms_diff: float
    large_diff_ratio: float


@dataclass(frozen=True)
class VisualThresholds:
    """視覚差分の閾値を表します。"""

    mean_abs_diff: float
    rms_diff: float
    large_diff_ratio: float


# =========================
# ユーティリティ関数
# =========================
def setup_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """ログ出力の設定を行います。"""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def now_jst() -> datetime.datetime:
    """現在の日時（JST）を取得します。"""
    tz = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(tz=tz)


def build_output_path(input_path: Path) -> Path:
    """出力ファイルパスを生成します。"""
    dt = now_jst().strftime("%Y%m%d_%H%M%S")
    return input_path.parent / f"{dt}_{input_path.name}"


def img_supports_mediancut_rgb() -> bool:
    """RGB画像では MEDIANCUT/MAXCOVERAGE が利用可能である前提を返します。"""
    return True


# =========================
# 入出力
# =========================
class ImageLoader:
    """画像の入力を担当します。"""

    def load_png_as_rgb(self, path: Path) -> Image.Image:
        """PNG画像を RGB で読み込みます。"""
        if not path.is_file():
            raise FileNotFoundError(f"入力ファイルが見つかりません: {path}")
        if path.suffix.lower() != ".png":
            raise ValueError(f"入力はPNGのみ対応です: {path}")

        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except Exception as e:
            raise RuntimeError(f"画像読み込みエラー: {path}, {e}") from e


# =========================
# 画像整形
# =========================
class CanvasPadder:
    """画像のキャンバス拡張を担当します。"""

    def __init__(self, config: AppConfig):
        """初期設定を保持します。"""
        self.config = config

    def pad_to_16_9(self, img: Image.Image) -> Image.Image:
        """画像を16:9比率にパディングします。"""
        width, height = img.size
        target_ratio = self.config.target_aspect_ratio
        current_ratio = width / float(height)

        if abs(current_ratio - target_ratio) < self.config.aspect_ratio_epsilon:
            return img

        if current_ratio < target_ratio:
            new_width = int(math.ceil(height * target_ratio))
            new_height = height
        else:
            new_width = width
            new_height = int(math.ceil(width / target_ratio))

        canvas = Image.new("RGB", (new_width, new_height), self.config.pad_bg_color)
        offset_x = (new_width - width) // 2
        offset_y = (new_height - height) // 2
        canvas.paste(img, (offset_x, offset_y))

        logger.info(
            "16:9 パディング適用: src=(%s,%s) -> dst=(%s,%s)",
            width,
            height,
            new_width,
            new_height,
        )
        return canvas


# =========================
# フォント選択とタイトル描画
# =========================
class FontResolver:
    """fontconfig とフォールバックを使ったフォント解決を担当します。"""

    def __init__(self, config: AppConfig):
        """初期設定を保持します。"""
        self.config = config

    def get_random_font(self) -> Tuple[str, Optional[str]]:
        """利用可能フォントからランダムに1つ返します。"""
        fc_lines = self._get_font_candidates()
        available_fonts: List[Tuple[str, str]] = []

        for family in self.config.font_families:
            font_path = self._find_font_path(family, fc_lines) if fc_lines else None
            if font_path:
                available_fonts.append((family, font_path))

        if available_fonts:
            return random.choice(available_fonts)

        return self.config.fallback_font_label, None

    def load_fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font_path: Optional[str],
        max_width: int,
        initial_size: int,
    ) -> ImageFont.ImageFont:
        """指定幅に収まるフォントを返します。"""
        size = max(self.config.font_size_min, int(initial_size))

        while size >= self.config.font_size_min:
            font = self._load_font(font_path, size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            if text_width <= max_width:
                return font

            size = max(self.config.font_size_min, int(size * self.config.font_size_shrink_factor))

        return ImageFont.load_default()

    def _get_font_candidates(self) -> List[str]:
        """fc-list の結果を取得します。"""
        if not shutil.which("fc-list"):
            logger.warning("`fc-list` コマンドが見つかりません。フォント検索をスキップします。")
            return []

        try:
            process = subprocess.run(
                list(self.config.fc_list_cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
            )
            return [line.strip() for line in process.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            logger.warning("`fc-list` の実行に失敗しました。")
            return []
        except Exception as e:
            logger.warning("フォント取得中に予期せぬエラーが発生しました: %s", e)
            return []

    def _find_font_path(self, family: str, fc_lines: Sequence[str]) -> Optional[str]:
        """ファミリー名に一致するフォントパスを返します。"""
        family_lower = family.lower()
        candidates: List[str] = []

        for line in fc_lines:
            if ":" not in line:
                continue
            try:
                file_part, family_part = line.split(":", 1)
            except ValueError:
                continue

            if family_lower in family_part.lower() and Path(file_part).is_file():
                candidates.append(file_part)

        for style_keyword in self.config.font_preferred_style_keywords:
            for font_path in candidates:
                if style_keyword in Path(font_path).name.lower():
                    return font_path

        return candidates[0] if candidates else None

    def _load_font(self, font_path: Optional[str], size: int) -> ImageFont.ImageFont:
        """指定サイズのフォントを読み込みます。"""
        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                return ImageFont.load_default()

        fallback_path = Path(self.config.fallback_font_path)
        if fallback_path.is_file():
            try:
                return ImageFont.truetype(str(fallback_path), size)
            except OSError:
                return ImageFont.load_default()

        return ImageFont.load_default()


class TitleRenderer:
    """右上タイトルの描画を担当します。"""

    def __init__(self, config: AppConfig, font_resolver: FontResolver):
        """初期設定と依存サービスを保持します。"""
        self.config = config
        self.font_resolver = font_resolver

    def draw_title(self, img: Image.Image) -> Image.Image:
        """画像の右上にタイトル文字を描画します。"""
        out = img.copy()
        draw = ImageDraw.Draw(out)

        if self.config.random_seed_use_timestamp:
            random.seed(int(now_jst().timestamp()))

        family_name, font_path = self.font_resolver.get_random_font()
        logger.info("フォント選択: %s (path=%s)", family_name, font_path or "Default")

        width, _ = out.size
        initial_size = max(self.config.font_size_min, int(width * self.config.font_size_ratio))
        max_width = max(self.config.title_max_width_min, int(width * self.config.title_max_width_ratio))

        font = self.font_resolver.load_fit_font(
            draw=draw,
            text=self.config.title_text,
            font_path=font_path,
            max_width=max_width,
            initial_size=initial_size,
        )

        bbox = draw.textbbox((0, 0), self.config.title_text, font=font)
        text_width = bbox[2] - bbox[0]
        pos_x = max(0, width - self.config.title_margin_right - text_width)
        pos_y = max(0, self.config.title_margin_top)

        draw.text((pos_x, pos_y), self.config.title_text, fill=self.config.title_color_rgb, font=font)
        return out


# =========================
# 差分評価とPNG保存
# =========================
class PngEncoder:
    """PNG保存とビット深度最適化を担当します。"""

    def __init__(self, config: AppConfig):
        """初期設定を保持します。"""
        self.config = config

    def save_png_bytes(self, image: Image.Image) -> bytes:
        """PNGバイト列を返します。"""
        save_kwargs = {
            "format": "PNG",
            "optimize": self.config.png_optimize,
            "compress_level": self.config.png_compress_level,
        }

        png_bits = self._get_png_bits(image)
        if png_bits is not None:
            save_kwargs["bits"] = png_bits

        with io.BytesIO() as buffer:
            image.save(buffer, **save_kwargs)
            return buffer.getvalue()

    def _get_png_bits(self, image: Image.Image) -> Optional[int]:
        """P モード画像のビット深度を最小化します。"""
        if image.mode != "P":
            return None

        colors = image.getcolors(maxcolors=self.config.quantize_colors_max)
        color_count = len(colors) if colors is not None else self.config.quantize_colors_max

        if color_count <= 2:
            return 1
        if color_count <= 4:
            return 2
        if color_count <= 16:
            return 4
        return 8


class VisualQualityJudge:
    """候補画像の視覚差分測定と閾値判定を担当します。"""

    def __init__(self, config: AppConfig):
        """閾値設定を保持します。"""
        self.strict_thresholds = VisualThresholds(
            mean_abs_diff=config.max_mean_abs_diff,
            rms_diff=config.max_rms_diff,
            large_diff_ratio=config.max_large_diff_ratio,
        )
        self.relaxed_thresholds = VisualThresholds(
            mean_abs_diff=config.relaxed_max_mean_abs_diff,
            rms_diff=config.relaxed_max_rms_diff,
            large_diff_ratio=config.relaxed_max_large_diff_ratio,
        )
        self.large_diff_threshold = config.large_diff_threshold

    def measure_visual_difference(self, original: Image.Image, candidate_bytes: bytes) -> Tuple[float, float, float]:
        """元画像と候補画像の差分を数値化します。"""
        try:
            with Image.open(io.BytesIO(candidate_bytes)) as candidate_image:
                candidate = candidate_image.convert("RGB")
        except Exception as e:
            raise RuntimeError(f"候補画像の再読込に失敗しました: {e}") from e

        if candidate.size != original.size:
            raise RuntimeError(
                f"候補画像サイズが不一致です: original={original.size}, candidate={candidate.size}"
            )

        diff = ImageChops.difference(original, candidate)
        stat = ImageStat.Stat(diff)

        mean_values = stat.mean
        rms_values = stat.rms
        mean_abs_diff = sum(mean_values) / float(len(mean_values)) if mean_values else 0.0
        rms_diff = sum(rms_values) / float(len(rms_values)) if rms_values else 0.0

        histogram = diff.histogram()
        total_pixels = original.size[0] * original.size[1]
        if total_pixels <= 0:
            raise RuntimeError("画像サイズが不正です。")

        channels = max(1, len(histogram) // 256)
        large_count = 0
        for channel_index in range(channels):
            offset = channel_index * 256
            large_count += sum(histogram[offset + self.large_diff_threshold: offset + 256])

        large_diff_ratio = large_count / float(total_pixels * channels)
        return mean_abs_diff, rms_diff, large_diff_ratio

    def is_strictly_acceptable(self, candidate: CompressionCandidate) -> bool:
        """厳格閾値で許容可能か判定します。"""
        return self._matches_thresholds(candidate, self.strict_thresholds)

    def is_relaxed_acceptable(self, candidate: CompressionCandidate) -> bool:
        """緩和閾値で許容可能か判定します。"""
        return self._matches_thresholds(candidate, self.relaxed_thresholds)

    def _matches_thresholds(self, candidate: CompressionCandidate, thresholds: VisualThresholds) -> bool:
        """指定閾値に収まるかを判定します。"""
        return (
            candidate.mean_abs_diff <= thresholds.mean_abs_diff
            and candidate.rms_diff <= thresholds.rms_diff
            and candidate.large_diff_ratio <= thresholds.large_diff_ratio
        )


# =========================
# 圧縮候補生成と選定
# =========================
class CompressionOptimizer:
    """圧縮候補の生成、評価、選定、反復圧縮、保存を担当します。"""

    def __init__(self, config: AppConfig, png_encoder: PngEncoder, quality_judge: VisualQualityJudge):
        """依存サービスを保持します。"""
        self.config = config
        self.png_encoder = png_encoder
        self.quality_judge = quality_judge

    def compress_and_save(self, img: Image.Image, input_path: Path, output_path: Path) -> None:
        """圧縮候補を生成し、必要に応じて再圧縮を繰り返して最終結果を保存します。"""
        input_size = input_path.stat().st_size
        if input_size == 0:
            raise ValueError("入力ファイルのサイズが0です。")

        best_candidate, profile_label, direct_candidate, pass_count = self._optimize_iteratively(img, input_size)

        with open(output_path, "wb") as file_obj:
            file_obj.write(best_candidate.out_bytes)

        self._log_selection_result(
            input_size=input_size,
            output_path=output_path,
            best_candidate=best_candidate,
            profile_label=profile_label,
            direct_candidate=direct_candidate,
            pass_count=pass_count,
        )

    def _optimize_iteratively(
        self,
        reference_img: Image.Image,
        input_size: int,
    ) -> Tuple[CompressionCandidate, str, Optional[CompressionCandidate], int]:
        """初回圧縮後、サイズが縮まらなくなるまで再圧縮を繰り返します。"""
        current_img = reference_img
        previous_best: Optional[CompressionCandidate] = None
        previous_profile_label: Optional[str] = None
        previous_direct_candidate: Optional[CompressionCandidate] = None
        executed_passes = 0

        max_passes = max(1, int(self.config.iterative_max_passes))

        for pass_index in range(1, max_passes + 1):
            baseline_size = input_size if previous_best is None else previous_best.out_size
            candidates = self._generate_candidates(reference_img, current_img)
            if not candidates:
                raise RuntimeError("利用可能な圧縮候補を生成できませんでした。")

            best_candidate, profile_label = self._select_best_candidate(candidates, baseline_size)
            direct_candidate = self._find_direct_candidate(candidates)
            executed_passes = pass_index

            if previous_best is None:
                logger.info(
                    "圧縮パス %s: 初回選定 %s [%s] size=%s bytes",
                    pass_index,
                    best_candidate.label,
                    profile_label,
                    f"{best_candidate.out_size:,}",
                )
                previous_best = best_candidate
                previous_profile_label = profile_label
                previous_direct_candidate = direct_candidate

                if not self.config.iterative_recompression_enabled:
                    return previous_best, previous_profile_label, previous_direct_candidate, executed_passes

                current_img = self._decode_candidate_bytes(best_candidate.out_bytes)
                continue

            improvement_bytes = previous_best.out_size - best_candidate.out_size
            logger.info(
                "圧縮パス %s: selected=%s [%s] size=%s bytes, previous=%s bytes, improvement=%s bytes",
                pass_index,
                best_candidate.label,
                profile_label,
                f"{best_candidate.out_size:,}",
                f"{previous_best.out_size:,}",
                f"{improvement_bytes:,}",
            )

            if improvement_bytes < self.config.iterative_min_improvement_bytes:
                logger.info(
                    "反復圧縮停止: パス %s でこれ以上縮小できませんでした。閾値=%s bytes, 実改善=%s bytes",
                    pass_index,
                    self.config.iterative_min_improvement_bytes,
                    improvement_bytes,
                )
                return previous_best, previous_profile_label or profile_label, previous_direct_candidate, pass_index - 1

            previous_best = best_candidate
            previous_profile_label = profile_label
            previous_direct_candidate = direct_candidate
            current_img = self._decode_candidate_bytes(best_candidate.out_bytes)

        logger.warning(
            "反復圧縮は最大パス数に到達しました: max_passes=%s",
            max_passes,
        )
        if previous_best is None or previous_profile_label is None:
            raise RuntimeError("反復圧縮結果が取得できませんでした。")

        return previous_best, previous_profile_label, previous_direct_candidate, executed_passes

    def _generate_candidates(self, reference_img: Image.Image, working_img: Image.Image) -> List[CompressionCandidate]:
        """圧縮候補を全件生成します。"""
        candidates: List[CompressionCandidate] = []

        direct_candidate = self._build_candidate(reference_img, working_img, "RGB_DIRECT_SAVE")
        if direct_candidate is not None:
            candidates.append(direct_candidate)

        trial_count = 0
        for colors in self._iter_palette_steps():
            for method_label, method in self._get_quantize_methods():
                for dither_label, dither in self._get_dither_modes():
                    if self.config.max_tries > 0 and trial_count >= self.config.max_tries:
                        return candidates
                    trial_count += 1

                    label = f"{method_label}_{dither_label}_{colors}"
                    quantized = self._quantize_image(working_img, colors, method, dither, label)
                    if quantized is None:
                        continue

                    candidate = self._build_candidate(reference_img, quantized, label)
                    if candidate is not None:
                        candidates.append(candidate)

        return candidates

    def _decode_candidate_bytes(self, image_bytes: bytes) -> Image.Image:
        """PNGバイト列を RGB 画像へ戻します。"""
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                return image.convert("RGB")
        except Exception as e:
            raise RuntimeError(f"圧縮結果の再読込に失敗しました: {e}") from e

    def _iter_palette_steps(self) -> Iterable[int]:
        """試行する色数を返します。"""
        for colors in self.config.palette_colors_steps:
            yield max(self.config.quantize_colors_min, min(int(colors), self.config.quantize_colors_max))

    def _get_quantize_methods(self) -> List[Tuple[str, int]]:
        """利用可能な量子化方式を返します。"""
        methods: List[Tuple[str, int]] = []

        if self.config.quantize_enable_libimagequant and features.check_feature("libimagequant"):
            methods.append(("LIBIMAGEQUANT", int(Image.Quantize.LIBIMAGEQUANT)))

        methods.append(("FASTOCTREE", self.config.quantize_method))

        if img_supports_mediancut_rgb():
            methods.append(("MEDIANCUT", int(Image.Quantize.MEDIANCUT)))
            methods.append(("MAXCOVERAGE", int(Image.Quantize.MAXCOVERAGE)))

        unique_methods: List[Tuple[str, int]] = []
        seen_method_ids = set()
        for label, method in methods:
            if method in seen_method_ids:
                continue
            seen_method_ids.add(method)
            unique_methods.append((label, method))

        return unique_methods

    def _get_dither_modes(self) -> List[Tuple[str, int]]:
        """試行するディザ設定を返します。"""
        modes: List[Tuple[str, int]] = []

        if self.config.quantize_use_dither:
            modes.append(("FLOYDSTEINBERG", int(Image.Dither.FLOYDSTEINBERG)))
        if self.config.quantize_try_without_dither:
            modes.append(("NONE", int(Image.Dither.NONE)))
        if not modes:
            modes.append(("FLOYDSTEINBERG", int(Image.Dither.FLOYDSTEINBERG)))

        return modes

    def _quantize_image(
        self,
        img: Image.Image,
        colors: int,
        method: int,
        dither: int,
        label: str,
    ) -> Optional[Image.Image]:
        """量子化を実行し、失敗時は None を返します。"""
        try:
            return img.quantize(colors=colors, method=method, dither=dither)
        except Exception as e:
            logger.warning("量子化に失敗しました: %s, error=%s", label, e)
            return None

    def _build_candidate(
        self,
        source_img: Image.Image,
        encoded_img: Image.Image,
        label: str,
    ) -> Optional[CompressionCandidate]:
        """圧縮候補を生成して返します。"""
        try:
            out_bytes = self.png_encoder.save_png_bytes(encoded_img)
            mean_abs_diff, rms_diff, large_diff_ratio = self.quality_judge.measure_visual_difference(
                source_img,
                out_bytes,
            )
        except Exception as e:
            logger.warning("圧縮候補の生成に失敗しました: %s, error=%s", label, e)
            return None

        logger.info(
            "候補評価: %s, size=%s bytes, mean_abs_diff=%.4f, rms=%.4f, large_diff_ratio=%.5f",
            label,
            f"{len(out_bytes):,}",
            mean_abs_diff,
            rms_diff,
            large_diff_ratio,
        )

        return CompressionCandidate(
            label=label,
            out_bytes=out_bytes,
            out_size=len(out_bytes),
            mean_abs_diff=mean_abs_diff,
            rms_diff=rms_diff,
            large_diff_ratio=large_diff_ratio,
        )

    def _select_best_candidate(
        self,
        candidates: Sequence[CompressionCandidate],
        input_size: int,
    ) -> Tuple[CompressionCandidate, str]:
        """視覚品質優先で最適候補を選択します。"""
        strict_candidates = [candidate for candidate in candidates if self.quality_judge.is_strictly_acceptable(candidate)]
        relaxed_candidates = [candidate for candidate in candidates if self.quality_judge.is_relaxed_acceptable(candidate)]

        strict_best = min(strict_candidates, key=lambda candidate: candidate.out_size) if strict_candidates else None
        relaxed_best = min(relaxed_candidates, key=lambda candidate: candidate.out_size) if relaxed_candidates else None
        direct_candidate = self._find_direct_candidate(candidates)

        if strict_best is not None:
            strict_reduction = 1.0 - (strict_best.out_size / float(input_size))
            if strict_reduction >= self.config.target_reduction:
                return strict_best, "STRICT"
            return strict_best, "STRICT_VISUAL_PRIORITY"

        if self.config.enable_relaxed_profile and relaxed_best is not None:
            return relaxed_best, "RELAXED"

        if direct_candidate is not None:
            return direct_candidate, "DIRECT_SAVE_FALLBACK"

        if relaxed_best is not None:
            return relaxed_best, "RELAXED_FALLBACK"

        return min(candidates, key=lambda candidate: candidate.out_size), "MIN_SIZE_FALLBACK"

    def _find_direct_candidate(self, candidates: Sequence[CompressionCandidate]) -> Optional[CompressionCandidate]:
        """直接保存候補を返します。"""
        return next((candidate for candidate in candidates if candidate.label == "RGB_DIRECT_SAVE"), None)

    def _log_selection_result(
        self,
        input_size: int,
        output_path: Path,
        best_candidate: CompressionCandidate,
        profile_label: str,
        direct_candidate: Optional[CompressionCandidate],
        pass_count: int,
    ) -> None:
        """採用結果をログ出力します。"""
        reduction_vs_input = 1.0 - (best_candidate.out_size / float(input_size))

        if reduction_vs_input >= self.config.target_reduction:
            logger.info(
                "目標達成: %s [%s] で保存しました: %s (reduction_vs_input=%.1f%%, passes=%s)",
                best_candidate.label,
                profile_label,
                output_path,
                reduction_vs_input * 100.0,
                pass_count,
            )
        else:
            logger.warning(
                "目標未達: %s [%s] を採用しました: %s (target=%.1f%%, actual_vs_input=%.1f%%, passes=%s)",
                best_candidate.label,
                profile_label,
                output_path,
                self.config.target_reduction * 100.0,
                reduction_vs_input * 100.0,
                pass_count,
            )

        if direct_candidate is None or direct_candidate.out_size <= 0:
            return

        reduction_vs_transformed = 1.0 - (best_candidate.out_size / float(direct_candidate.out_size))
        logger.info(
            "圧縮比較: direct_after_last_pass=%s bytes -> selected=%s bytes (reduction_vs_transformed=%.1f%%)",
            f"{direct_candidate.out_size:,}",
            f"{best_candidate.out_size:,}",
            reduction_vs_transformed * 100.0,
        )


# =========================
# オーケストレーション
# =========================
class ImageProcessor:
    """画像処理全体の流れを統括します。"""

    def __init__(self, config: AppConfig):
        """依存クラスを初期化します。"""
        self.config = config
        self.loader = ImageLoader()
        self.padder = CanvasPadder(config)
        self.font_resolver = FontResolver(config)
        self.title_renderer = TitleRenderer(config, self.font_resolver)
        self.png_encoder = PngEncoder(config)
        self.quality_judge = VisualQualityJudge(config)
        self.compression_optimizer = CompressionOptimizer(config, self.png_encoder, self.quality_judge)

    def process(self, input_path: Path, output_path: Path) -> None:
        """画像を読み込み、整形し、圧縮して保存します。"""
        image = self.loader.load_png_as_rgb(input_path)
        image = self.padder.pad_to_16_9(image)
        image = self.title_renderer.draw_title(image)
        self.compression_optimizer.compress_and_save(image, input_path, output_path)


def main() -> int:
    """CLIエントリポイントです。"""
    setup_logging()

    if len(sys.argv) != 2:
        print(USAGE_TEMPLATE.format(script=sys.argv[0]), file=sys.stderr)
        return EXIT_USAGE

    input_path = Path(sys.argv[1]).resolve()
    output_path = build_output_path(input_path)
    processor = ImageProcessor(AppConfig())

    try:
        logger.info("処理開始: %s", input_path)
        processor.process(input_path, output_path)

        if output_path.exists():
            out_size = output_path.stat().st_size
            in_size = input_path.stat().st_size
            reduction = 1.0 - (out_size / float(in_size))
            logger.info("全工程完了: Original=%s -> Result=%s (%.1f%% reduction)", f"{in_size:,}", f"{out_size:,}", reduction * 100.0)

    except Exception as e:
        logger.error("処理中にエラーが発生しました: %s", e, exc_info=True)
        return EXIT_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
