#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
girls_decadence.py

【概要】
単一のPNG画像を入力とし、SNSやWeb媒体向けに「16:9比率へのパディング」「透かし文字（タイトル）の挿入」「ファイルサイズ削減」を一括で行う画像処理スクリプトです。

【このプログラムでできること（処理フロー）】
1) 入力PNGを読み込み（RGBに変換）
2) 元画像のアスペクト比を維持したまま、キャンバスを拡張して16:9比率に整形（クロップなし）
3) 右上にタイトル文字を描画（画像幅に応じてフォントサイズと最大幅を自動調整）
   - 可能ならシステムフォントから指定ファミリーを探索してランダム選択（fc-list利用）
   - 見つからない場合はフォールバックフォント（DejaVuSerifなど）を使用
4) PNG量子化（減色）＋高圧縮で保存し、所定のサイズ削減率（目標）を満たすまで色数を段階的に下げて試行
   - 目標未達でも、最も小さくできた結果で保存

【出力ファイル名の仕様】
出力は入力ファイルと同じディレクトリに生成されます。
形式: YYYYMMDD_HHMMSS_{元ファイル名}
例: 20260103_235959_input.png

【使用方法】
- 1ファイル処理（入力はPNGのみ）
    python girls_decadence.py /path/to/input.png

- 実行例（カレントのPNGを処理）
    python girls_decadence.py ./input.png

【依存ライブラリのインストール方法（Ubuntu 24.04想定）】
- Pythonライブラリ（必須）
    python3 -m pip install --upgrade pip
    python3 -m pip install pillow

- システムコマンド（推奨：フォント探索に使用）
    sudo apt-get update
    sudo apt-get install -y fontconfig

- フォント（任意：見栄えを良くしたい場合）
  ※ 本スクリプトは「Cormorant Garamond / Cinzel Decorative / Playfair Display / Bodoni」を探索します。
  これらが未導入でも動作しますが、導入すると意図した雰囲気になりやすいです。
  例（パッケージ名はディストリやリポジトリで異なる場合があります）:
    sudo apt-get install -y fonts-cormorant fonts-playfair-display

【注意】
- 入力はPNGのみ対応です（.png以外はエラー）。
- 透かし文字は右上固定で、テキスト内容は設定クラス AppConfig.title_text で定義されています。
- 量子化により、グラデーション等でバンディングが発生する可能性があります（仕様）。

【設計思想】
- **非破壊的な整形**: 元画像のアスペクト比を維持し、クロップ（切り抜き）を行わずに余白（パディング）で16:9化します。
- **動的な可読性確保**: 画像サイズに応じてフォントサイズやタイトルの最大幅を動的に計算し、レイアウト崩れを防ぎます。
- **容量の最適化**: 視覚的な品質を維持しつつ、パレット減色（量子化）を用いて目標の圧縮率（サイズ削減率）達成を試みます。

【前提環境】
- OS: Linux/Unix系（`fc-list`コマンド推奨）
- Python: 3.8以上
- ライブラリ: Pillow (PIL)
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

def setup_logging(level: str = "INFO") -> None:
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
    
    # 目標サイズ削減率 (1 - out/in)
    target_reduction: float = 0.70
    
    # 最大試行回数
    max_tries: int = 5
    
    # PNG量子化の色数ステップ
    palette_colors_steps: List[int] = field(default_factory=lambda: [256, 128, 64, 32, 16])
    
    # 16:9 拡張の背景色
    pad_bg_color: Tuple[int, int, int] = (255, 255, 255)
    
    # タイトル設定
    title_text: str = "Girls' Decadence"
    title_color_rgb: Tuple[int, int, int] = (192, 192, 192)  # シルバー
    title_margin_top: int = 24
    title_margin_right: int = 24
    title_max_width_ratio: float = 0.28
    
    # フォント設定
    font_families: List[str] = field(default_factory=lambda: [
        "Cormorant Garamond",
        "Cinzel Decorative",
        "Playfair Display",
        "Bodoni",
    ])
    font_size_ratio: float = 0.030
    font_size_min: int = 12
    
    # フォールバックフォントパス (Linux環境向け)
    fallback_font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"

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
        target_ratio = 16.0 / 9.0
        current_ratio = w / float(h)

        if abs(current_ratio - target_ratio) < 1e-6:
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
            cmd = ["fc-list", ":", "file", "family"]
            # check=Trueでエラー時に例外を送出させる
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True)
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
        preferred_styles = ["regular", "roman", "book"]
        for style in preferred_styles:
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
        
        return "Fallback Font", None

    def _fit_font_size(self, draw: ImageDraw.ImageDraw, text: str, font_path: Optional[str], max_w: int, init_size: int) -> ImageFont.FreeTypeFont:
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

            size = max(self.config.font_size_min, int(size * 0.92))

        return ImageFont.load_default()

    def draw_title(self, img: Image.Image) -> Image.Image:
        """画像の右上にタイトルテキストを描画します。"""
        out = img.copy()
        draw = ImageDraw.Draw(out)

        # ランダムシードの設定（現在時刻ベース）
        random.seed(int(now_jst().timestamp()))

        fam_name, font_path = self._select_random_font()
        logger.info(f"フォント選択: {fam_name} (path={font_path or 'Default'})")

        w, _ = out.size
        init_size = max(self.config.font_size_min, int(w * self.config.font_size_ratio))
        max_w = max(80, int(w * self.config.title_max_width_ratio))

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
            
            # 量子化処理 (method=2: Fast Octree)
            quantized = img.quantize(colors=max(2, min(int(colors), 256)), method=2)
            
            with io.BytesIO() as buf:
                quantized.save(buf, format="PNG", optimize=True, compress_level=9)
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
        logger.warning(f"目標未達: Target={self.config.target_reduction:.1%}, Best={final_reduction:.1%}. 最小サイズで保存しました: {output_path}")


# =========================
# メイン処理
# =========================
def main() -> int:
    setup_logging()
    
    # 引数チェック
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} /path/to/input.png", file=sys.stderr)
        return 2

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
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
