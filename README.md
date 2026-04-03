# girls_decadence.py

## 概要

`girls_decadence.py` は、単一の PNG 画像を対象に、次の処理を一括で実行する Linux 向けの Python スクリプトです。

1. 入力 PNG を読み込む
2. 元画像をクロップせずに 16:9 比率へパディングする
3. 右上にタイトル文字を描画する
4. `pngquant` で PNG を圧縮する
5. 前回より 1 バイト以上小さくならなくなるまで、同一条件で `pngquant` 圧縮を反復する
6. 入力画像と同じディレクトリへ、タイムスタンプ付きファイル名で保存する

入力ファイル自体は上書きしません。

---

## 仕様

### 対応入力

- `.png` のみ対応
- 1 回の実行で 1 ファイルを処理

### 画像整形

- 元画像の縦横比は維持
- クロップは行わない
- 足りない領域は白背景でパディング
- 目標比率は `16:9`

### タイトル描画

- 右上固定
- 文字列は既定で `Girls' Decadence`
- フォントは `fc-list` が使える場合、候補フォントからランダム選択
- フォントが見つからない場合はフォールバックフォントを使用

### PNG 圧縮

圧縮は次の `pngquant` コマンド相当です。

```bash
pngquant --quality=80-95 --speed 1 --strip --output output.png --force -- input.png
```

この条件で毎回同じように実行し、前回より 1 バイト以上小さくならなくなった時点で停止します。

### 出力ファイル名

出力ファイルは入力画像と同じディレクトリへ生成されます。

形式:

```text
YYYYMMDD_HHMMSS_ffffff_{元ファイル名}
```

例:

```text
20260404_123456_123456_input.png
```

---

## 前提環境

- Linux / Unix 系
- Python 3.8 以上
- `pngquant` が PATH 上に存在すること
- `Pillow` がインストール済みであること
- `fontconfig` があればフォント探索の精度が上がる

---

## インストール方法

### Ubuntu / Linux Mint

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip fontconfig pngquant
python3 -m pip install --upgrade pip
python3 -m pip install pillow
```

### 実行権限を付与する場合

```bash
chmod +x ./girls_decadence.py
```

---

## 使用方法

### 基本

```bash
python3 girls_decadence.py /path/to/input.png
```

### カレントディレクトリの画像を処理する例

```bash
python3 girls_decadence.py ./input.png
```

### 実行権限付きで実行する例

```bash
./girls_decadence.py /path/to/input.png
```

---

## 処理の流れ

1. 入力ファイルの存在と拡張子を確認
2. PNG を RGB として読み込み
3. 16:9 にパディング
4. タイトル文字を右上に描画
5. 一時 PNG を作成
6. `pngquant` を実行
7. 前回より縮小していれば、その結果を再入力として再実行
8. 縮小が止まった時点の最終結果を保存
9. 異常終了時は不完全な出力ファイルを削除

---

## ログ出力

標準出力へ INFO レベルでログを出します。主に次の内容を確認できます。

- 16:9 パディングの前後サイズ
- 使用フォント
- `pngquant` 実行コマンド
- 各圧縮パスの前後サイズ
- 改善バイト数と改善率
- 最終出力先

---

## 主な設定値

スクリプト内の `AppConfig` で主に次を変更できます。

- `title_text`
  - 右上に描画するタイトル文字列
- `title_color_rgb`
  - タイトル文字色
- `title_margin_top`
  - 上マージン
- `title_margin_right`
  - 右マージン
- `font_families`
  - 探索対象フォントファミリー
- `pngquant_quality`
  - `pngquant` の品質条件
- `pngquant_speed`
  - `pngquant` の速度設定
- `iterative_max_passes`
  - 最大反復回数
- `iterative_min_improvement_bytes`
  - 継続判定の最小改善バイト数

---

## 注意点

- `pngquant` は非可逆圧縮です。寸法と比率は維持されますが、色数削減により見た目がわずかに変わる可能性があります。
- 本スクリプトは、圧縮率よりも「指定条件で縮まなくなるまで繰り返す」ことを優先しています。
- `pngquant --quality=80-95` の条件を満たせない画像では、`pngquant` が失敗することがあります。
- 入力が `.png` 以外の場合はエラー終了します。
- フォント環境によって、タイトル文字の見た目は多少変わります。

---

## よくあるエラー

### `pngquant` コマンドが見つからない

```text
`pngquant` コマンドが見つかりません。
```

対応:

```bash
sudo apt-get update
sudo apt-get install -y pngquant
```

### `PIL` が見つからない

```text
ModuleNotFoundError: No module named 'PIL'
```

対応:

```bash
python3 -m pip install pillow
```

### PNG 以外を渡した

```text
入力はPNGのみ対応です
```

対応:

- `.png` ファイルを指定してください。

---

## 既知の仕様

- 16:9 化はクロップではなく余白追加です。
- タイトル描画は最初の 1 回だけです。
- 圧縮の反復対象は、タイトル描画後の PNG です。
- 改善が 0 バイト以下になった時点で停止します。
- 反復中に最良結果だけを採用し、入力ファイル自体は変更しません。

---

## 想定ファイル構成

```text
.
├── girls_decadence.py
├── README.md
└── input.png
```

---

## 実行例

入力:

```text
./sample.png
```

実行:

```bash
python3 girls_decadence.py ./sample.png
```

出力例:

```text
./20260404_153000_123456_sample.png
```

---

## 補足

この README は、`girls_decadence.py` の現在の実装内容に合わせて作成しています。仕様変更を行った場合は、`AppConfig`、CLI 引数、依存コマンド、出力仕様に差分がないか確認したうえで README も更新してください。
