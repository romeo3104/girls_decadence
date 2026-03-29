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
- 容量の最適化: 視覚品質を維持しつつ、パレット減色（量子化）で圧縮効率を引き上げます。

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

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat, features

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


@dataclass(frozen=True)
class AppConfig:
    """アプリケーションの定数・設定を管理するデータクラス"""

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


@dataclass(frozen=True)
class CompressionCandidate:
    """圧縮候補の情報を保持するデータクラス"""

    label: str
    out_bytes: bytes
    out_size: int
    mean_abs_diff: float
    rms_diff: float
    large_diff_ratio: float


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
        """画像を16:9のアスペクト比になるよう背景色でパディングします。"""
        w, h = img.size
        target_ratio = self.config.target_aspect_ratio
        current_ratio = w / float(h)

        if abs(current_ratio - target_ratio) < self.config.aspect_ratio_epsilon:
            return img

        if current_ratio < target_ratio:
            new_w = int(math.ceil(h * target_ratio))
            new_h = h
        else:
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
                if family_lower in fam_part.lower() and Path(file_part).is_file():
                    candidates.append(file_part)
            except ValueError:
                continue

        for style in self.config.font_preferred_style_keywords:
            for font_path in candidates:
                if style in Path(font_path).name.lower():
                    return font_path

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

    def _get_png_bits(self, image: Image.Image) -> Optional[int]:
        """P モード画像のビット深度を、実際の色数に応じて最小化します。"""
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

    def _save_png_bytes(self, image: Image.Image) -> bytes:
        """PNGバイト列を生成します。"""
        save_kwargs = {
            "format": "PNG",
            "optimize": self.config.png_optimize,
            "compress_level": self.config.png_compress_level,
        }

        png_bits = self._get_png_bits(image)
        if png_bits is not None:
            save_kwargs["bits"] = png_bits

        with io.BytesIO() as buf:
            image.save(buf, **save_kwargs)
            return buf.getvalue()

    def _measure_visual_difference(
        self,
        original: Image.Image,
        candidate_bytes: bytes,
    ) -> Tuple[float, float, float]:
        """元画像と候補画像の差分を数値化します。"""
        try:
            candidate = Image.open(io.BytesIO(candidate_bytes)).convert("RGB")
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

        hist = diff.histogram()
        total_pixels = original.size[0] * original.size[1]
        if total_pixels <= 0:
            raise RuntimeError("画像サイズが不正です。")

        channels = max(1, len(hist) // 256)
        large_count = 0
        for channel_index in range(channels):
            offset = channel_index * 256
            large_count += sum(hist[offset + self.config.large_diff_threshold: offset + 256])

        large_diff_ratio = large_count / float(total_pixels * channels)
        return mean_abs_diff, rms_diff, large_diff_ratio

    def _is_visual_quality_acceptable(
        self,
        mean_abs_diff: float,
        rms_diff: float,
        large_diff_ratio: float,
        *,
        relaxed: bool = False,
    ) -> bool:
        """差分が許容範囲かを判定します。"""
        if relaxed:
            return (
                mean_abs_diff <= self.config.relaxed_max_mean_abs_diff
                and rms_diff <= self.config.relaxed_max_rms_diff
                and large_diff_ratio <= self.config.relaxed_max_large_diff_ratio
            )

        return (
            mean_abs_diff <= self.config.max_mean_abs_diff
            and rms_diff <= self.config.max_rms_diff
            and large_diff_ratio <= self.config.max_large_diff_ratio
        )

    def _get_quantize_methods(self) -> List[Tuple[str, int]]:
        """利用可能な量子化方式を返します。"""
        methods: List[Tuple[str, int]] = []

        if self.config.quantize_enable_libimagequant and features.check_feature("libimagequant"):
            methods.append(("LIBIMAGEQUANT", int(Image.Quantize.LIBIMAGEQUANT)))

        methods.append(("FASTOCTREE", self.config.quantize_method))

        if img_supports_mediancut_rgb():
            methods.append(("MEDIANCUT", int(Image.Quantize.MEDIANCUT)))
            methods.append(("MAXCOVERAGE", int(Image.Quantize.MAXCOVERAGE)))

        seen = set()
        unique_methods: List[Tuple[str, int]] = []
        for label, method in methods:
            if method in seen:
                continue
            seen.add(method)
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

    def _build_candidate(
        self,
        source_img: Image.Image,
        encoded_img: Image.Image,
        label: str,
    ) -> Optional[CompressionCandidate]:
        """圧縮候補を生成して返します。"""
        try:
            out_bytes = self._save_png_bytes(encoded_img)
            mean_abs_diff, rms_diff, large_diff_ratio = self._measure_visual_difference(source_img, out_bytes)
        except Exception as e:
            logger.warning(f"圧縮候補の生成に失敗しました: {label}, error={e}")
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

    def _generate_compression_candidates(self, img: Image.Image) -> List[CompressionCandidate]:
        """圧縮候補を生成します。"""
        candidates: List[CompressionCandidate] = []

        direct_candidate = self._build_candidate(
            source_img=img,
            encoded_img=img,
            label="RGB_DIRECT_SAVE",
        )
        if direct_candidate is not None:
            candidates.append(direct_candidate)

        dither_modes = self._get_dither_modes()
        quantize_methods = self._get_quantize_methods()

        trial_count = 0
        for colors in self.config.palette_colors_steps:
            colors_i = max(
                self.config.quantize_colors_min,
                min(int(colors), self.config.quantize_colors_max),
            )

            for method_label, method in quantize_methods:
                for dither_label, dither in dither_modes:
                    if self.config.max_tries > 0 and trial_count >= self.config.max_tries:
                        return candidates
                    trial_count += 1

                    label = f"{method_label}_{dither_label}_{colors_i}"
                    try:
                        quantized = img.quantize(
                            colors=colors_i,
                            method=method,
                            dither=dither,
                        )
                    except Exception as e:
                        logger.warning(f"量子化に失敗しました: {label}, error={e}")
                        continue

                    candidate = self._build_candidate(
                        source_img=img,
                        encoded_img=quantized,
                        label=label,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

        return candidates

    def _select_best_candidate(
        self,
        candidates: List[CompressionCandidate],
        in_size: int,
    ) -> Tuple[CompressionCandidate, str]:
        """圧縮率と視覚品質のバランスを見て最適候補を選択します。"""
        strict_candidates = [
            c for c in candidates
            if self._is_visual_quality_acceptable(
                c.mean_abs_diff,
                c.rms_diff,
                c.large_diff_ratio,
                relaxed=False,
            )
        ]
        relaxed_candidates = [
            c for c in candidates
            if self._is_visual_quality_acceptable(
                c.mean_abs_diff,
                c.rms_diff,
                c.large_diff_ratio,
                relaxed=True,
            )
        ]

        strict_best = min(strict_candidates, key=lambda c: c.out_size) if strict_candidates else None
        relaxed_best = min(relaxed_candidates, key=lambda c: c.out_size) if relaxed_candidates else None
        direct_candidate = next((c for c in candidates if c.label == "RGB_DIRECT_SAVE"), None)

        # 既定方針:
        # 目標圧縮率よりも視覚品質を優先し、人間の目で分かる劣化を避けます。
        # strict 候補が 1 件でもあれば、target_reduction 未達でも strict の最小サイズを採用します。
        if strict_best is not None:
            strict_reduction = 1.0 - (strict_best.out_size / float(in_size))
            if strict_reduction >= self.config.target_reduction:
                return strict_best, "STRICT"
            return strict_best, "STRICT_VISUAL_PRIORITY"

        # relaxed は既定で無効です。明示的に有効化した場合のみ使用します。
        if self.config.enable_relaxed_profile and relaxed_best is not None:
            return relaxed_best, "RELAXED"

        # strict を満たす候補が無い場合は、見た目を絶対に変えない直接保存へ戻します。
        if direct_candidate is not None:
            return direct_candidate, "DIRECT_SAVE_FALLBACK"

        if relaxed_best is not None:
            return relaxed_best, "RELAXED_FALLBACK"

        return min(candidates, key=lambda c: c.out_size), "MIN_SIZE_FALLBACK"

    def compress_and_save(self, img: Image.Image, input_path: Path, output_path: Path) -> None:
        """
        画像を量子化して保存します。
        既存の処理フローは維持しつつ、圧縮候補を最後まで比較して最小サイズを採用します。
        """
        in_size = input_path.stat().st_size
        if in_size == 0:
            raise ValueError("入力ファイルのサイズが0です。")

        candidates = self._generate_compression_candidates(img)
        if not candidates:
            raise RuntimeError("利用可能な圧縮候補を生成できませんでした。")

        best_candidate, profile_label = self._select_best_candidate(candidates, in_size)
        direct_candidate = next((c for c in candidates if c.label == "RGB_DIRECT_SAVE"), None)

        with open(output_path, "wb") as f:
            f.write(best_candidate.out_bytes)

        reduction = 1.0 - (best_candidate.out_size / float(in_size))
        direct_reduction = None
        if direct_candidate is not None and direct_candidate.out_size > 0:
            direct_reduction = 1.0 - (best_candidate.out_size / float(direct_candidate.out_size))

        if reduction >= self.config.target_reduction:
            logger.info(
                "目標達成: %s [%s] で保存しました: %s (reduction_vs_input=%.1f%%)",
                best_candidate.label,
                profile_label,
                output_path,
                reduction * 100.0,
            )
        else:
            logger.warning(
                "目標未達: %s [%s] を採用しました: %s (target=%.1f%%, actual_vs_input=%.1f%%)",
                best_candidate.label,
                profile_label,
                output_path,
                self.config.target_reduction * 100.0,
                reduction * 100.0,
            )

        if direct_reduction is not None:
            logger.info(
                "圧縮比較: direct_after_transform=%s bytes -> selected=%s bytes (reduction_vs_transformed=%.1f%%)",
                f"{direct_candidate.out_size:,}",
                f"{best_candidate.out_size:,}",
                direct_reduction * 100.0,
            )


def img_supports_mediancut_rgb() -> bool:
    """RGB画像では MEDIANCUT/MAXCOVERAGE が利用可能である前提を明示するための関数です。"""
    return True


def main() -> int:
    setup_logging()

    if len(sys.argv) != 2:
        print(USAGE_TEMPLATE.format(script=sys.argv[0]), file=sys.stderr)
        return EXIT_USAGE

    input_path = Path(sys.argv[1]).resolve()
    config = AppConfig()
    processor = ImageProcessor(config)

    try:
        logger.info(f"処理開始: {input_path}")

        img = processor.load_image(input_path)
        img = processor.pad_to_16_9(img)
        img = processor.draw_title(img)

        output_path = build_output_path(input_path)
        processor.compress_and_save(img, input_path, output_path)

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
