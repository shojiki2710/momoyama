# リスティンググループ商品ボード 自動更新

Windsor.ai経由でGoogle Ads / Google Merchant Centerのデータを取得し、AG別×商品別のROASボードを毎朝自動生成してGitHub Pagesに公開する。仕様の背景はNotion「仕様書：リスティンググループ商品ボード自動更新（Claude Code引き継ぎ用、2026/7/28）」を参照。

## 構成

```
scripts/generate_board.py   # パイプライン本体（4ステップ取得→突合→HTML生成）
scripts/board_template.html # ボードのHTML/CSS/JSテンプレート（プレースホルダに実データを埋め込む）
scripts/dev_fixtures/       # ローカル動作確認用の実データスナップショット（2026-07-28取得）
docs/index.html             # 生成物。GitHub Pagesの公開対象
.github/workflows/update-board.yml  # 毎朝7:00 JST（22:00 UTC）に自動実行
```

## セットアップ手順

### 1. Windsor.ai REST APIキーの取得

ふなとさんが https://onboard.windsor.ai でAPIキーを取得。**このチャットで使っているWindsor.ai MCP接続とは別物**なので、REST用に新たに取得が必要。

### 2. リポジトリを公開（public）にする

**無料プランでGitHub Pagesを使うには、リポジトリが公開（public）である必要がある**（非公開のままだと無料プランではPagesの固定URLが作れない）。ボードのURLを知っている人なら誰でも閲覧できる状態になる（検索エンジンには載らないが、技術的にはアクセス可能）。

- `shojiki2710/momoyama` が既に存在する場合：そのリポジトリを開き、**Settings**（歯車アイコン）→ 一番下までスクロール →「Danger Zone」内の **Change visibility** → **Change to public** を選択。確認のためリポジトリ名の入力を求められるので `momoyama` と入力して確定。
- まだ存在しない場合：GitHubの右上「+」→ **New repository** → Repository name に `momoyama` → **Public** を選択 → **Create repository**。

### 3. ファイルのアップロード（ブラウザ操作のみ・ターミナル不要）

このフォルダの中身をGitHubに上げる。Gitコマンドは使わず、ブラウザの画面操作だけで完結する方法：

1. `shojiki2710/momoyama` のリポジトリ画面を開く
2. **Add file** ボタン → **Upload files** をクリック
3. Finderでこのプロジェクトフォルダ（`/Users/CozyPlace/Claude/Scheduled/Listing Group Board`）を開き、以下の4つをブラウザの画面にドラッグ＆ドロップする：
   - `docs` フォルダ
   - `scripts` フォルダ
   - `requirements.txt`
   - `README.md`
4. 画面下部の「Commit changes」欄はそのままで **Commit changes** ボタンを押す

**`.github/workflows/update-board.yml` だけは別の方法で追加する**（Finderでは `.github` のように `.`（ドット）で始まるフォルダは通常隠れていて選択しづらいため）：

1. リポジトリ画面で **Add file** → **Create new file**
2. ファイル名の欄に `.github/workflows/update-board.yml` とそのまま入力する（`/` を含めて入力すると自動的にフォルダが作られる）
3. 中身の欄に、このプロジェクトの [.github/workflows/update-board.yml](.github/workflows/update-board.yml) の内容をそのままコピー＆ペースト
4. **Commit changes**

（`.gitignore` は無くても動作に支障はないので省略してよい）

### 4. Secret（APIキー）の登録

リポジトリの **Settings** → 左メニュー **Secrets and variables** → **Actions** → **New repository secret**

- 名前: `WINDSOR_API_KEY`
- 値: 手順1で取得したAPIキー

**Add secret** で保存。コードやリポジトリにAPIキーを平文で書かないこと。

### 5. GitHub Pagesを有効化

リポジトリの **Settings** → 左メニュー **Pages** → Source: `Deploy from a branch` を選択 → Branch: `main` / フォルダ: `/docs` を選択 → **Save**

数分後、同じ画面に表示されるURL（`https://shojiki2710.github.io/momoyama/` のような形）がボードの固定URL。

### 6. 動作確認

1. リポジトリの **Actions** タブを開く
2. 左側の「Update listing group board」をクリック
3. 右側の **Run workflow** ボタン → 緑の **Run workflow** をクリックして手動実行
4. 1分程度で実行が完了したら（緑のチェックマーク）、手順5のURLを開いてボードが表示されるか確認

これでセットアップは完了。翌朝以降は毎日7:00 JSTに自動更新される。

### 7. ローカルで動作確認（任意・スキップ可）

APIキーなしで、実際のアカウントから2026-07-28に取得したデータのスナップショット（`scripts/dev_fixtures/`）を使ってパイプラインとテンプレートの動作を確認できる：

```bash
pip install -r requirements.txt
python scripts/generate_board.py --fixtures
open docs/index.html
```

本番（Windsor.ai REST API使用）で動かす場合：

```bash
export WINDSOR_API_KEY=xxxx
python scripts/generate_board.py
```

### （参考）ターミナル・gitコマンドに慣れている場合

ブラウザ操作の代わりに、以下でも同じことができる：

```bash
cd "/Users/CozyPlace/Claude/Scheduled/Listing Group Board"
git init
git add .
git commit -m "Add listing group board automation pipeline"
git branch -M main
git remote add origin https://github.com/shojiki2710/momoyama.git
git push -u origin main
```

## 既知の制約・要確認事項

- **item_idの大文字小文字が不一致**：Merchant Center側は `shopify_JP_...`、Google Ads側は `shopify_jp_...` と大文字小文字が異なる（2026-07-28実機確認）。`generate_board.py` は突合時に両方を小文字化して対応済み。
- **稼働中AG判定**：仕様書は「クエリ結果に出現するAG＝稼働中」という手順を記載しているが、実際には `asset_group_status` フィールドを直接クエリすると `ENABLED`/`PAUSED` が明示的に返る（一時停止中のAGも行として返ってくる）ため、`generate_board.py` はこのフィールドを直接フィルタする、より単純で確実な方式を採用している。
- **REST APIのフィルタ構文は未検証**：このセッションにはWindsor.aiのREST APIキーがなく、生のREST呼び出しを実地検証できていない（使えたのはこのチャット専用のMCP接続のみで、これは仕様書にある通りREST APIキー方式とは別物）。そのため `generate_board.py` はREST側のフィルタ構文に依存せず、`date_from`/`date_to`と`fields`のみで幅広く取得し、キャンペーン名・ステータス・item_idの絞り込みはすべてPython側で行う設計にしている。**Secret登録後、最初の実行（`workflow_dispatch`推奨）で実際に商品が表示されるか必ず確認すること。** 0件だった場合は生成物を上書きしない安全装置（`generate_board.py`内）が働きエラー終了するので、GitHub Actionsのログでレスポンス内容を確認して調整が必要。
- **"single"ラベル**：用途不明の商品が1件存在（未使用ラベル）。現状のボードには表示されない（稼働中AGに対応しないため）。
