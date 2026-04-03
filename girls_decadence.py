#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
girls_decadence_pngquant_refactored.py

【仕様説明】
このスクリプトは、単一の PNG 画像を対象に、次の処理を 1 回の実行でまとめて行います。

1. 入力 PNG を読み込む
2. 元画像の見た目を切り抜かず、余白追加のみで 16:9 比率へ整形する
3. 右上にタイトル文字を描画する
4. pngquant で PNG を圧縮する
5. 前回より 1 バイト以上小さくならなくなるまで、同一条件で pngquant 圧縮を反復する
6. 最終結果を、入力画像と同じディレクトリにタイムスタンプ付きファイル名で保存する

【圧縮仕様】
圧縮コマンドは、次の pngquant コマンド相当です。

    pngquant --quality=80-95 --speed 1 --strip --output output.png --force -- input.png

本スクリプトでは、上記条件を毎回同じ形で実行し、サイズ改善が止まるまで繰り返します。
なお、pngquant は非可逆圧縮です。解像度や縦横比は維持されますが、色数削減により見た目がわずかに変わる可能性があります。

【出力ファイル名】
出力ファイルは入力画像と同じディレクトリに生成します。入力ファイルは上書きしません。

形式:
    YYYYMMDD_HHMMSS_ffffff_{元ファイル名}

例:
    20260404_123456_123456_input.png

【使用方法】
1ファイル処理:
    python3 girls_decadence_pngquant_refactored.py /path/to/input.png

実行例:
    python3 girls_decadence_pngquant_refactored.py ./input.png

【インストール方法】
Ubuntu / Linux Mint 系の例です。

1. システムパッケージを導入:
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip fontconfig pngquant

2. Pillow を導入:
    python3 -m pip install --upgrade pip
    python3 -m pip install pillow

3. 実行権限を付与して使う場合:
    chmod +x ./girls_decadence_pngquant_refactored.py
    ./girls_decadence_pngquant_refactored.py /path/to/input.png

【前提条件】
- 入力は .png のみ対応です
- Linux / Unix 系を想定しています
- `pngquant` が PATH 上に存在する必要があります
- `fc-list` が存在する場合はシステムフォント探索を行います
- `fc-list` が使えない場合でも、フォールバックフォントがあれば動作します

【注意点】
- 16:9 化はクロップではなくパディングです
- タイトル文字は右上固定です
- 圧縮反復は、前回より 1 バイト以上縮まらなくなった時点で停止します
- pngquant の品質条件を満たせない画像では、pngquant が失敗する可能性があります
"""

from __future__ import annotations

import argparse
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


logger = logging.getLogger(__name__)

DEFAULT_LOG_LEVEL = "INFO"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class AppConfig:
    """アプリケーション全体の設定値を保持します。"""

    target_aspect_ratio: float = 16.0 / 9.0
    aspect_ratio_epsilon: float = 1e-6
    pad_bg_color: Tuple[int, int, int] = (255, 255, 255)

    title_text: str = "Girls' Decadence"
    title_color_rgb: Tuple[int, int, int] = (192, 192, 192)
    title_margin_top: int = 24
    title_margin_right: int = 24
    title_max_width_ratio: float = 0.28
    title_max_width_min: int = 80

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

    pngquant_cmd: str = "pngquant"
    pngquant_quality: str = "80-95"
    pngquant_speed: str = "1"
    pngquant_strip: bool = True
    iterative_recompression_enabled: bool = True
    iterative_max_passes: int = 50
    iterative_min_improvement_bytes: int = 1


@dataclass(frozen=True)
class CompressionPassResult:
    """1回の圧縮パス結果を保持します。"""

    pass_index: int
    input_path: Path
    output_path: Path
    before_size: int
    after_size: int

    @property
    def improvement_bytes(self) -> int:
        """圧縮前後のサイズ差を返します。"""
        return self.before_size - self.after_size

    @property
    def improvement_ratio_percent(self) -> float:
        """圧縮改善率を百分率で返します。"""
        if self.before_size <= 0:
            return 0.0
        return (self.improvement_bytes / float(self.before_size)) * 100.0


@dataclass(frozen=True)
class IterativeCompressionResult:
    """反復圧縮の最終結果を保持します。"""

    best_path: Path
    executed_passes: int
    initial_size: int
    final_size: int


class CliArgumentParser:
    """CLI引数の解析を担当します。"""

    @staticmethod
    def parse(argv: Sequence[str]) -> Path:
        """CLI引数を解析して入力パスを返します。"""
        parser = argparse.ArgumentParser(
            description="PNG画像を16:9整形、右上タイトル描画、pngquant反復圧縮して保存します。",
        )
        parser.add_argument(
            "input_png",
            help="入力PNGファイルのパス",
        )

        args = parser.parse_args(argv)
        return Path(args.input_png).expanduser().resolve()


class LoggingConfigurator:
    """ログ設定を担当します。"""

    @staticmethod
    def setup(level: str = DEFAULT_LOG_LEVEL) -> None:
        """ロギングを初期化します。"""
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


class PathFactory:
    """出力パス生成を担当します。"""

    @staticmethod
    def now_jst() -> datetime.datetime:
        """現在日時を JST で返します。"""
        tz = datetime.timezone(datetime.timedelta(hours=9))
        return datetime.datetime.now(tz=tz)

    @classmethod
    def build_output_path(cls, input_path: Path) -> Path:
        """タイムスタンプ付き出力パスを返します。"""
        timestamp = cls.now_jst().strftime("%Y%m%d_%H%M%S_%f")
        return input_path.parent / f"{timestamp}_{input_path.name}"


class FileValidator:
    """入力ファイルの妥当性確認を担当します。"""

    @staticmethod
    def validate_png_file(path: Path) -> None:
        """PNG入力ファイルとして妥当か確認します。"""
        if not path.is_file():
            raise FileNotFoundError(f"入力ファイルが見つかりません: {path}")
        if path.suffix.lower() != ".png":
            raise ValueError(f"入力はPNGのみ対応です: {path}")
        if path.stat().st_size <= 0:
            raise ValueError(f"入力ファイルのサイズが0です: {path}")


class CommandDependencyChecker:
    """外部コマンド依存の確認を担当します。"""

    @staticmethod
    def require_command(command_name: str, install_hint: str) -> None:
        """必須コマンドの存在を確認します。"""
        if shutil.which(command_name):
            return
        raise FileNotFoundError(
            f"`{command_name}` コマンドが見つかりません。{install_hint}"
        )


class ImageLoader:
    """画像読込を担当します。"""

    def load_png_as_rgb(self, path: Path) -> Image.Image:
        """PNG を RGB 画像として読み込みます。"""
        FileValidator.validate_png_file(path)
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except Exception as error:
            raise RuntimeError(f"画像読み込みエラー: {path}, {error}") from error


class CanvasPadder:
    """16:9 パディング処理を担当します。"""

    def __init__(self, config: AppConfig):
        """設定を保持します。"""
        self.config = config

    def pad_to_target_aspect_ratio(self, image: Image.Image) -> Image.Image:
        """クロップなしで画像を目標比率へパディングします。"""
        width, height = image.size
        current_ratio = width / float(height)
        target_ratio = self.config.target_aspect_ratio

        if abs(current_ratio - target_ratio) < self.config.aspect_ratio_epsilon:
            return image

        if current_ratio < target_ratio:
            padded_width = int(math.ceil(height * target_ratio))
            padded_height = height
        else:
            padded_width = width
            padded_height = int(math.ceil(width / target_ratio))

        canvas = Image.new("RGB", (padded_width, padded_height), self.config.pad_bg_color)
        offset_x = (padded_width - width) // 2
        offset_y = (padded_height - height) // 2
        canvas.paste(image, (offset_x, offset_y))

        logger.info(
            "16:9 パディング適用: src=(%s,%s) -> dst=(%s,%s)",
            width,
            height,
            padded_width,
            padded_height,
        )
        return canvas


class FontResolver:
    """システムフォント探索とフォールバックを担当します。"""

    def __init__(self, config: AppConfig):
        """設定を保持します。"""
        self.config = config

    def get_random_font(self, rng: random.Random) -> Tuple[str, Optional[str]]:
        """利用可能フォントからランダムに1件返します。"""
        candidates = self._collect_available_fonts()
        if candidates:
            return rng.choice(candidates)
        return self.config.fallback_font_label, None

    def load_fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font_path: Optional[str],
        max_width: int,
        initial_size: int,
    ) -> ImageFont.ImageFont:
        """最大幅に収まるフォントを返します。"""
        current_size = max(self.config.font_size_min, int(initial_size))
        previous_size = current_size + 1

        while current_size >= self.config.font_size_min and current_size < previous_size:
            font = self._load_font(font_path, current_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            if text_width <= max_width:
                return font

            previous_size = current_size
            current_size = int(current_size * self.config.font_size_shrink_factor)

        return ImageFont.load_default()

    def _collect_available_fonts(self) -> List[Tuple[str, str]]:
        """設定されたファミリー名に一致する利用可能フォント一覧を返します。"""
        fc_lines = self._run_fc_list()
        if not fc_lines:
            return []

        results: List[Tuple[str, str]] = []
        for family in self.config.font_families:
            font_path = self._find_font_path(family, fc_lines)
            if font_path is not None:
                results.append((family, font_path))
        return results

    def _run_fc_list(self) -> List[str]:
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
        except Exception as error:
            logger.warning("フォント取得中に予期せぬエラーが発生しました: %s", error)
            return []

    def _find_font_path(self, family: str, fc_lines: Sequence[str]) -> Optional[str]:
        """ファミリー名に一致するフォントパスを返します。"""
        family_lower = family.lower()
        candidate_paths: List[str] = []

        for line in fc_lines:
            if ":" not in line:
                continue

            try:
                file_part, family_part = line.split(":", 1)
            except ValueError:
                continue

            if family_lower in family_part.lower() and Path(file_part).is_file():
                candidate_paths.append(file_part)

        for preferred_keyword in self.config.font_preferred_style_keywords:
            for candidate_path in candidate_paths:
                if preferred_keyword in Path(candidate_path).name.lower():
                    return candidate_path

        return candidate_paths[0] if candidate_paths else None

    def _load_font(self, font_path: Optional[str], font_size: int) -> ImageFont.ImageFont:
        """指定パスまたはフォールバックフォントを読み込みます。"""
        if font_path is not None:
            try:
                return ImageFont.truetype(font_path, font_size)
            except OSError:
                logger.warning("指定フォントの読込に失敗しました。フォールバックへ切り替えます: %s", font_path)

        fallback_path = Path(self.config.fallback_font_path)
        if fallback_path.is_file():
            try:
                return ImageFont.truetype(str(fallback_path), font_size)
            except OSError:
                logger.warning("フォールバックフォントの読込に失敗しました: %s", fallback_path)

        return ImageFont.load_default()


class TitleRenderer:
    """右上タイトル描画を担当します。"""

    def __init__(self, config: AppConfig, font_resolver: FontResolver):
        """設定とフォント解決クラスを保持します。"""
        self.config = config
        self.font_resolver = font_resolver

    def draw_title(self, image: Image.Image) -> Image.Image:
        """画像右上にタイトルを描画した新しい画像を返します。"""
        output_image = image.copy()
        draw = ImageDraw.Draw(output_image)
        randomizer = self._build_randomizer()
        family_name, font_path = self.font_resolver.get_random_font(randomizer)

        logger.info("フォント選択: %s (path=%s)", family_name, font_path or "Default")

        image_width, _ = output_image.size
        initial_font_size = max(self.config.font_size_min, int(image_width * self.config.font_size_ratio))
        max_title_width = max(self.config.title_max_width_min, int(image_width * self.config.title_max_width_ratio))

        font = self.font_resolver.load_fit_font(
            draw=draw,
            text=self.config.title_text,
            font_path=font_path,
            max_width=max_title_width,
            initial_size=initial_font_size,
        )

        bbox = draw.textbbox((0, 0), self.config.title_text, font=font)
        text_width = bbox[2] - bbox[0]
        pos_x = max(0, image_width - self.config.title_margin_right - text_width)
        pos_y = max(0, self.config.title_margin_top)

        draw.text(
            (pos_x, pos_y),
            self.config.title_text,
            fill=self.config.title_color_rgb,
            font=font,
        )
        return output_image

    def _build_randomizer(self) -> random.Random:
        """フォント選択用の乱数生成器を返します。"""
        if self.config.random_seed_use_timestamp:
            return random.Random(int(PathFactory.now_jst().timestamp()))
        return random.Random()


class PngQuantCommandBuilder:
    """pngquant 実行コマンドの生成を担当します。"""

    def __init__(self, config: AppConfig):
        """設定を保持します。"""
        self.config = config

    def build(self, input_path: Path, output_path: Path) -> List[str]:
        """pngquant 実行コマンドを返します。"""
        command = [
            self.config.pngquant_cmd,
            f"--quality={self.config.pngquant_quality}",
            "--speed",
            self.config.pngquant_speed,
        ]

        if self.config.pngquant_strip:
            command.append("--strip")

        command.extend([
            "--output",
            str(output_path),
            "--force",
            "--",
            str(input_path),
        ])
        return command


class PngQuantRunner:
    """pngquant の単発実行を担当します。"""

    def __init__(self, config: AppConfig):
        """設定を保持します。"""
        self.config = config
        self.command_builder = PngQuantCommandBuilder(config)

    def run_once(self, input_path: Path, output_path: Path) -> CompressionPassResult:
        """pngquant を1回実行して結果を返します。"""
        before_size = input_path.stat().st_size
        command = self.command_builder.build(input_path, output_path)

        logger.info("pngquant 実行: %s", " ".join(command))

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except Exception as error:
            raise RuntimeError(f"pngquant の起動に失敗しました: {error}") from error

        if process.stdout.strip():
            logger.info("pngquant stdout: %s", process.stdout.strip())
        if process.stderr.strip():
            logger.info("pngquant stderr: %s", process.stderr.strip())

        if process.returncode != 0:
            raise RuntimeError(
                "pngquant の実行に失敗しました: "
                f"returncode={process.returncode}, input={input_path}, output={output_path}, stderr={process.stderr.strip()}"
            )

        if not output_path.is_file():
            raise RuntimeError(f"pngquant の出力ファイルが生成されませんでした: {output_path}")

        after_size = output_path.stat().st_size
        return CompressionPassResult(
            pass_index=0,
            input_path=input_path,
            output_path=output_path,
            before_size=before_size,
            after_size=after_size,
        )


class PngQuantIterativeCompressor:
    """pngquant の反復圧縮を担当します。"""

    def __init__(self, config: AppConfig):
        """設定と実行クラスを保持します。"""
        self.config = config
        self.runner = PngQuantRunner(config)

    def compress_to_output(self, image: Image.Image, input_path: Path, output_path: Path) -> IterativeCompressionResult:
        """整形済み画像を一時保存し、反復圧縮後に最終出力へ保存します。"""
        CommandDependencyChecker.require_command(
            command_name=self.config.pngquant_cmd,
            install_hint="`sudo apt-get install -y pngquant` を確認してください。",
        )

        with tempfile.TemporaryDirectory(prefix="girls_decadence_pngquant_") as temp_dir_string:
            temp_dir = Path(temp_dir_string)
            seed_path = temp_dir / "seed.png"
            self._save_seed_png(image, seed_path)

            result = self._compress_iteratively(seed_path)
            shutil.copy2(result.best_path, output_path)

        logger.info(
            "圧縮完了: input=%s bytes, output=%s bytes, reduction=%.2f%%, passes=%s, output=%s",
            f"{result.initial_size:,}",
            f"{result.final_size:,}",
            self._calculate_reduction_percent(result.initial_size, result.final_size),
            result.executed_passes,
            output_path,
        )
        return result

    def _save_seed_png(self, image: Image.Image, seed_path: Path) -> None:
        """整形済み画像を一時PNGとして保存します。"""
        try:
            image.save(seed_path, format="PNG")
        except Exception as error:
            raise RuntimeError(f"一時PNGの保存に失敗しました: {error}") from error

    def _compress_iteratively(self, seed_path: Path) -> IterativeCompressionResult:
        """改善が止まるまで pngquant を反復実行します。"""
        current_path = seed_path
        current_size = seed_path.stat().st_size
        executed_passes = 0

        max_passes = max(1, int(self.config.iterative_max_passes))
        if not self.config.iterative_recompression_enabled:
            max_passes = 1

        for pass_index in range(1, max_passes + 1):
            output_path = seed_path.parent / f"pass_{pass_index:03d}.png"
            pass_result = self.runner.run_once(current_path, output_path)
            pass_result = CompressionPassResult(
                pass_index=pass_index,
                input_path=pass_result.input_path,
                output_path=pass_result.output_path,
                before_size=pass_result.before_size,
                after_size=pass_result.after_size,
            )

            logger.info(
                "圧縮パス %s: before=%s bytes, after=%s bytes, improvement=%s bytes (%.4f%%)",
                pass_result.pass_index,
                f"{pass_result.before_size:,}",
                f"{pass_result.after_size:,}",
                f"{pass_result.improvement_bytes:,}",
                pass_result.improvement_ratio_percent,
            )

            if pass_result.improvement_bytes < self.config.iterative_min_improvement_bytes:
                logger.info(
                    "反復圧縮停止: パス %s で改善が 0 バイト以下になりました。",
                    pass_result.pass_index,
                )
                output_path.unlink(missing_ok=True)
                break

            current_path = output_path
            current_size = pass_result.after_size
            executed_passes = pass_index

        final_path = seed_path if executed_passes == 0 else current_path
        final_size = final_path.stat().st_size

        if executed_passes == 0:
            logger.info("反復圧縮の結果、初回入力が最終採用されました。")

        return IterativeCompressionResult(
            best_path=final_path,
            executed_passes=executed_passes,
            initial_size=seed_path.stat().st_size,
            final_size=final_size,
        )

    @staticmethod
    def _calculate_reduction_percent(before_size: int, after_size: int) -> float:
        """圧縮率を百分率で返します。"""
        if before_size <= 0:
            return 0.0
        return (1.0 - (after_size / float(before_size))) * 100.0


class ImageProcessor:
    """画像処理全体を統括します。"""

    def __init__(self, config: AppConfig):
        """依存クラスを初期化します。"""
        self.config = config
        self.loader = ImageLoader()
        self.padder = CanvasPadder(config)
        self.font_resolver = FontResolver(config)
        self.title_renderer = TitleRenderer(config, self.font_resolver)
        self.compressor = PngQuantIterativeCompressor(config)

    def process(self, input_path: Path, output_path: Path) -> IterativeCompressionResult:
        """画像を読み込み、整形し、反復圧縮して保存します。"""
        source_image = self.loader.load_png_as_rgb(input_path)
        padded_image = self.padder.pad_to_target_aspect_ratio(source_image)
        titled_image = self.title_renderer.draw_title(padded_image)
        return self.compressor.compress_to_output(titled_image, input_path, output_path)


class ExitFileCleanup:
    """異常終了時の不完全ファイル削除を担当します。"""

    def __init__(self, target_path: Path):
        """削除対象パスを保持します。"""
        self.target_path = target_path
        self.completed = False

    def mark_completed(self) -> None:
        """正常終了済みとしてマークします。"""
        self.completed = True

    def cleanup(self) -> None:
        """未完了時のみ出力ファイルを削除します。"""
        if self.completed:
            return
        if not self.target_path.exists():
            return

        try:
            self.target_path.unlink()
            logger.info("中断により不完全な出力ファイルを削除しました: %s", self.target_path)
        except OSError:
            logger.warning("不完全な出力ファイルの削除に失敗しました: %s", self.target_path)


class Application:
    """CLI アプリケーション本体です。"""

    def __init__(self, config: AppConfig):
        """設定を保持します。"""
        self.config = config
        self.processor = ImageProcessor(config)

    def run(self, argv: Sequence[str]) -> int:
        """アプリケーションを実行します。"""
        LoggingConfigurator.setup()

        try:
            input_path = CliArgumentParser.parse(argv)
        except SystemExit as error:
            return int(error.code) if isinstance(error.code, int) else EXIT_USAGE

        output_path = PathFactory.build_output_path(input_path)
        cleanup_handler = ExitFileCleanup(output_path)
        atexit.register(cleanup_handler.cleanup)

        try:
            logger.info("処理開始: %s", input_path)
            result = self.processor.process(input_path, output_path)
            logger.info(
                "全工程完了: Original=%s -> Result=%s (%.2f%% reduction)",
                f"{input_path.stat().st_size:,}",
                f"{output_path.stat().st_size:,}",
                self._calculate_reduction_percent(input_path.stat().st_size, output_path.stat().st_size),
            )
            logger.info(
                "最終採用: passes=%s, transformed_seed=%s bytes, final=%s bytes",
                result.executed_passes,
                f"{result.initial_size:,}",
                f"{result.final_size:,}",
            )
            cleanup_handler.mark_completed()
            return EXIT_OK
        except Exception as error:
            logger.error("処理中にエラーが発生しました: %s", error, exc_info=True)
            return EXIT_ERROR

    @staticmethod
    def _calculate_reduction_percent(before_size: int, after_size: int) -> float:
        """入力比の圧縮率を百分率で返します。"""
        if before_size <= 0:
            return 0.0
        return (1.0 - (after_size / float(before_size))) * 100.0


def main() -> int:
    """CLI エントリポイントです。"""
    application = Application(AppConfig())
    return application.run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
