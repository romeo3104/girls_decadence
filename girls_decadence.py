#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
girls_decadence_pngquant.py

【概要】
単一の PNG 画像（入力は .png のみ）を対象に、SNS / Web掲載向けの体裁へ一括整形するスクリプトです。
主に「16:9 比率へのパディング（クロップなし）」「右上タイトル文字の描画」「pngquant によるPNG容量削減」を行います。

【処理フロー】
1) 入力PNGを読み込み（RGBに変換）
2) 元画像のアスペクト比を維持したまま、キャンバスを拡張して16:9比率に整形（クロップなし）
3) 右上にタイトル文字を描画（画像幅に応じてフォントサイズ・最大幅を自動調整）
   - `fc-list` が利用可能な場合、システムフォントから指定ファミリーを探索してランダム選択
   - 見つからない場合はフォールバックフォント（DejaVuSerif等）を使用
4) 一時PNGを生成し、以下の pngquant コマンド相当で圧縮します
   - `pngquant --quality=80-95 --speed 1 --strip --output output.png --force -- input.png`
5) 圧縮後PNGを再入力として再度 pngquant を実行し、前回より 1 バイト以上小さくならなくなるまで反復します
   - 16:9化とタイトル描画は最初の1回だけ行い、反復対象は pngquant 圧縮のみです
6) 最終結果を入力画像と同じディレクトリへ、元プログラムと同じ命名規則で保存します

【出力ファイル名の仕様】
出力は入力ファイルと同じディレクトリに生成されます（上書きしません）。
形式: YYYYMMDD_HHMMSS_ffffff_{元ファイル名}
例: 20260103_235959_123456_input.png

【使用方法】
- 1ファイル処理（入力はPNGのみ）
    python girls_decadence_pngquant.py /path/to/input.png

- 実行例（カレントのPNGを処理）
    python girls_decadence_pngquant.py ./input.png

【依存関係】
- Python: 3.8以上
- Pythonライブラリ:
    pillow (PIL)

  インストール例:
    python3 -m pip install --upgrade pip
    python3 -m pip install pillow

- 推奨システムコマンド:
    fontconfig（`fc-list` / `fc-cache`）
    pngquant

  インストール例（Ubuntu / Linux Mint）:
    sudo apt-get update
    sudo apt-get install -y fontconfig pngquant

【注意】
- 入力はPNGのみ対応です（.png以外はエラー）。
- 透かし文字（タイトル）は右上固定で、テキスト内容は設定クラス AppConfig.title_text で定義されています。
- 圧縮は「前回結果より1バイト以上小さくならない」時点で停止します。
- pngquant は非可逆圧縮です。色数削減によりグラデーション等で差が出る可能性があります。
- `pngquant --quality=80-95` の制約上、この品質条件を満たせない画像では終了コードが非0になる可能性があります。

【設計思想】
- 非破壊的な整形: 元画像のアスペクト比を維持し、クロップ（切り抜き）を行わずに余白（パディング）で16:9化します。
- 動的な可読性確保: 画像サイズに応じてフォントサイズやタイトル最大幅を動的に計算し、レイアウト崩れを抑えます。
- 容量の最適化: pngquant を同一条件で反復実行し、縮小が止まるまで圧縮します。

【前提環境】
- OS: Linux/Unix系（`fc-list` と `pngquant` が利用可能だと本来の動作になります）
- Ubuntu 24.04 / Linux Mint 21.x 系での利用を想定
"""

from __future__ import annotations

import atexit
import datetime
import logging
import math
import random
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

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
    # pngquant 圧縮
    # -------------------------
    pngquant_cmd: str = "pngquant"
    pngquant_quality: str = "80-95"
    pngquant_speed: str = "1"
    pngquant_strip: bool = True
    iterative_recompression_enabled: bool = True
    iterative_max_passes: int = 50
    iterative_min_improvement_bytes: int = 1


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
    dt = now_jst().strftime("%Y%m%d_%H%M%S_%f")
    return input_path.parent / f"{dt}_{input_path.name}"


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

    def get_random_font(self, rng: random.Random) -> Tuple[str, Optional[str]]:
        """利用可能フォントからランダムに1つ返します。"""
        fc_lines = self._get_font_candidates()
        available_fonts: List[Tuple[str, str]] = []

        for family in self.config.font_families:
            font_path = self._find_font_path(family, fc_lines) if fc_lines else None
            if font_path:
                available_fonts.append((family, font_path))

        if available_fonts:
            return rng.choice(available_fonts)

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
        prev_size = size + 1

        while size >= self.config.font_size_min and size < prev_size:
            font = self._load_font(font_path, size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            if text_width <= max_width:
                return font

            prev_size = size
            size = int(size * self.config.font_size_shrink_factor)

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
            rng = random.Random(int(now_jst().timestamp()))
        else:
            rng = random.Random()

        family_name, font_path = self.font_resolver.get_random_font(rng)
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
# pngquant 圧縮
# =========================
class PngQuantCompressor:
    """pngquant を用いた反復圧縮を担当します。"""

    def __init__(self, config: AppConfig):
        """初期設定を保持します。"""
        self.config = config

    def compress_and_save(self, img: Image.Image, input_path: Path, output_path: Path) -> None:
        """整形済み画像を一時保存し、pngquant 反復圧縮後に最終保存します。"""
        if not shutil.which(self.config.pngquant_cmd):
            raise FileNotFoundError(
                f"`{self.config.pngquant_cmd}` コマンドが見つかりません。`sudo apt-get install -y pngquant` を確認してください。"
            )

        input_size = input_path.stat().st_size
        if input_size == 0:
            raise ValueError("入力ファイルのサイズが0です。")

        with tempfile.TemporaryDirectory(prefix="girls_decadence_pngquant_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            seed_path = temp_dir / "seed.png"

            try:
                img.save(seed_path, format="PNG")
            except Exception as e:
                raise RuntimeError(f"一時PNGの保存に失敗しました: {e}") from e

            best_path, pass_count = self._compress_iteratively(seed_path)
            shutil.copy2(best_path, output_path)

        out_size = output_path.stat().st_size
        reduction = 1.0 - (out_size / float(input_size))
        logger.info(
            "圧縮完了: input=%s bytes, output=%s bytes, reduction=%.2f%%, passes=%s, output=%s",
            f"{input_size:,}",
            f"{out_size:,}",
            reduction * 100.0,
            pass_count,
            output_path,
        )

    def _compress_iteratively(self, seed_path: Path) -> Tuple[Path, int]:
        """前回よりサイズが縮まらなくなるまで pngquant を反復実行します。"""
        current_path = seed_path
        current_size = seed_path.stat().st_size
        executed_passes = 0

        max_passes = max(1, int(self.config.iterative_max_passes))
        if not self.config.iterative_recompression_enabled:
            max_passes = 1

        for pass_index in range(1, max_passes + 1):
            output_path = seed_path.parent / f"pass_{pass_index:03d}.png"
            self._run_pngquant(current_path, output_path)

            if not output_path.is_file():
                raise RuntimeError(f"pngquant の出力ファイルが生成されませんでした: {output_path}")

            new_size = output_path.stat().st_size
            improvement_bytes = current_size - new_size
            improvement_ratio = (improvement_bytes / float(current_size) * 100.0) if current_size > 0 else 0.0

            logger.info(
                "圧縮パス %s: before=%s bytes, after=%s bytes, improvement=%s bytes (%.4f%%)",
                pass_index,
                f"{current_size:,}",
                f"{new_size:,}",
                f"{improvement_bytes:,}",
                improvement_ratio,
            )

            if improvement_bytes < self.config.iterative_min_improvement_bytes:
                logger.info(
                    "反復圧縮停止: パス %s で改善が 0 バイト以下になりました。",
                    pass_index,
                )
                output_path.unlink(missing_ok=True)
                break

            current_path = output_path
            current_size = new_size
            executed_passes = pass_index

        if executed_passes == 0:
            logger.info("反復圧縮の結果、初回入力が最終採用されました。")
            return seed_path, 0

        return current_path, executed_passes

    def _run_pngquant(self, input_path: Path, output_path: Path) -> None:
        """指定条件で pngquant を1回実行します。"""
        cmd = [
            self.config.pngquant_cmd,
            f"--quality={self.config.pngquant_quality}",
            "--speed",
            self.config.pngquant_speed,
        ]

        if self.config.pngquant_strip:
            cmd.append("--strip")

        cmd.extend([
            "--output",
            str(output_path),
            "--force",
            "--",
            str(input_path),
        ])

        logger.info("pngquant 実行: %s", " ".join(cmd))

        try:
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except Exception as e:
            raise RuntimeError(f"pngquant の起動に失敗しました: {e}") from e

        if process.stdout.strip():
            logger.info("pngquant stdout: %s", process.stdout.strip())
        if process.stderr.strip():
            logger.info("pngquant stderr: %s", process.stderr.strip())

        if process.returncode != 0:
            raise RuntimeError(
                "pngquant の実行に失敗しました: "
                f"returncode={process.returncode}, input={input_path}, output={output_path}, stderr={process.stderr.strip()}"
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
        self.compressor = PngQuantCompressor(config)

    def process(self, input_path: Path, output_path: Path) -> None:
        """画像を読み込み、整形し、圧縮して保存します。"""
        image = self.loader.load_png_as_rgb(input_path)
        image = self.padder.pad_to_16_9(image)
        image = self.title_renderer.draw_title(image)
        self.compressor.compress_and_save(image, input_path, output_path)


# =========================
# CLI
# =========================
def main() -> int:
    """CLIエントリポイントです。"""
    setup_logging()

    if len(sys.argv) != 2:
        print(USAGE_TEMPLATE.format(script=sys.argv[0]), file=sys.stderr)
        return EXIT_USAGE

    input_path = Path(sys.argv[1]).resolve()
    output_path = build_output_path(input_path)
    processor = ImageProcessor(AppConfig())

    completed = False

    def _cleanup_on_exit() -> None:
        if not completed and output_path.exists():
            try:
                output_path.unlink()
                logger.info("中断により不完全な出力ファイルを削除しました: %s", output_path)
            except OSError:
                pass

    atexit.register(_cleanup_on_exit)

    try:
        logger.info("処理開始: %s", input_path)
        processor.process(input_path, output_path)

        if output_path.exists():
            out_size = output_path.stat().st_size
            in_size = input_path.stat().st_size
            reduction = 1.0 - (out_size / float(in_size))
            logger.info(
                "全工程完了: Original=%s -> Result=%s (%.2f%% reduction)",
                f"{in_size:,}",
                f"{out_size:,}",
                reduction * 100.0,
            )

        completed = True
    except Exception as e:
        logger.error("処理中にエラーが発生しました: %s", e, exc_info=True)
        return EXIT_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
