# arXiv Translator

arXiv 論文を日本語に翻訳・要約するサービスです。arXiv の URL を渡すと、ソースを取得し、Markdown 化、日本語翻訳、構造化された要約までを自動で行います。**React Web UI** と **Chrome 拡張機能** の 2 種類のクライアントが、共通の FastAPI バックエンドを呼び出します。

## 機能

- arXiv ソースの自動取得 — `arxiv.org/html/<id>` を優先し、無ければ `arxiv.org/e-print/<id>` を取得
- 決定的な Markdown 変換（LLM ではなく `markdownify` / `pandoc` を使用。画像・脚注・引用・数式を保持）
- マルチファイル TeX (`\input` / `\include`) の再帰展開
- 英語 → 日本語翻訳（Markdown 構造と数式表記を維持）
- 論文タイプ（通常論文 / サーベイ）に応じたテンプレートで要約生成
- 進捗のリアルタイム表示（3 秒ポーリング、進捗は 0–100 の整数）
- 処理中のキャンセル — Web UI と Chrome 拡張のどちらからでも、走行中のジョブを停止可能
- 成果物の ZIP ダウンロード

> **Note**: PDF しか公開されていない論文（TeX/HTML ソースが無いもの）は変換できません。検出すると `PdfOnlyPaperError` を返し、UI に日本語のエラーメッセージを表示します。

## アーキテクチャ

| レイヤー | 技術スタック |
|---|---|
| Frontend (Web) | React 18 + TypeScript + Vite + Tailwind CSS |
| Frontend (Extension) | Chrome Extension Manifest V3 + TypeScript + esbuild |
| Backend | Python 3.12+ / FastAPI（Lambda 上では Mangum 経由で実行）|
| AI Agent | Google ADK (Agent Development Kit) + Gemini 2.5 Pro |
| Infrastructure | AWS — Lambda × 2 / API Gateway / DynamoDB / S3 + CloudFront / Cognito |

### バックエンドパイプライン

`backend/src/agents/orchestrator.py` の `run_pipeline()` が単一のソース・オブ・トゥルースで、以下を直列実行します（各段の終わりに DynamoDB へ進捗を書き込みます）。

```
fetch_arxiv_paper        (tools/arxiv.py)
  ├ kind="html" → html_to_markdown   (tools/html_to_markdown.py, markdownify)
  └ kind="tex"  → tex_to_markdown    (tools/tex_to_markdown.py, pandoc サブプロセス)
                          ↓
              TranslationAgent       (agents/translation.py, LLM)
                          ↓
              SummaryAgent           (agents/summary.py, LLM)
                          ↓
              create_zip_package     (tools/packaging.py)
                          ↓
              upload_to_s3 (本番) または ローカル保存 (開発)
```

ソース → Markdown は LLM を使わない決定的変換です。LLM を呼び出すのは翻訳と要約の 2 段のみ。

### Lambda 2 段構成（本番）

- **API Lambda** (`src.main:handler`, 256 MB / 29 秒) — HTTP リクエスト処理。`POST /convert` で DynamoDB にジョブを作成し、Pipeline Lambda を非同期 invoke します（API Gateway の 29 秒上限内で即時に 202 を返すため、自身ではパイプラインを実行しません）。
- **Pipeline Lambda** (`src.pipeline_handler:handler`, 1024 MB / 15 分 / 2 GB 一時ストレージ) — `run_pipeline()` を最後まで実行します。

ローカル開発（`PIPELINE_LAMBDA_NAME` 未設定）では、`asyncio.create_task` で API プロセス内に直接パイプラインを起動します。

### 進捗連携はポーリング

クライアントは `GET /api/v1/jobs/{job_id}/status` を **3 秒間隔** でポーリングします（SSE は Lambda 互換性のため廃止済み）。Web UI は [useJobPolling](frontend/web/src/features/job-status/hooks/useJobPolling.ts) フックで、拡張機能は [service worker](frontend/chrome-extension/background/service-worker.ts) のループで、同じ間隔で取得します。

### キャンセルは協調式

`POST /api/v1/jobs/{job_id}/cancel` は DynamoDB の `cancel_requested` フラグを立てるだけで、走行中の Lambda やプロセスを直接停止はしません。`run_pipeline()` は各ステージ境界で [`is_cancel_requested`](backend/src/services/job_manager.py) を確認し、検出したら `CANCELLED` 終端状態へクリーンに遷移します。

- レイテンシは最大 1 ステージぶん — 翻訳・要約は LLM ストリーミング中に割り込めないため、その呼び出しが終わるまで待ちます
- ステータスと別フラグなので、パイプラインの進捗書き込みでキャンセル意図が上書きされません
- すでに終端 (`completed` / `error` / `cancelled`) のジョブには 409 を返します（冪等）

## ディレクトリ構成

```
ArxivSummaryChromeExtension/
├── backend/                       # FastAPI バックエンド (Python 3.12+)
│   ├── src/
│   │   ├── agents/                # ADK エージェント (translation, summary, orchestrator)
│   │   ├── api/                   # ルート定義 + Cognito 認証
│   │   ├── services/              # JobManager (DynamoDB) / PipelineDispatcher
│   │   ├── tools/                 # arxiv 取得, pandoc/markdownify, ZIP/S3
│   │   ├── main.py                # FastAPI + Mangum エントリ
│   │   └── pipeline_handler.py    # Pipeline Lambda エントリ
│   └── tests/                     # pytest 単体テスト + eval/ (LLM 評価)
├── frontend/
│   ├── web/                       # React Web UI
│   └── chrome-extension/          # Chrome 拡張機能 (Manifest V3)
├── infrastructure/                # AWS CDK (Python)
│   └── cdk/stacks/                # AuthStack / StorageStack / BackendStack
├── docs/                          # 仕様書・テンプレート
└── .github/workflows/             # CI (ci, lint-fix, claude, claude-code-review)
```

## セットアップ

### Docker Compose（推奨）

バックエンド・Web UI・DynamoDB Local を一括で起動します。

```bash
cp .env.example .env
# .env に GOOGLE_API_KEY を設定（Web UI を使うなら必須）
docker compose up --build
```

| サービス | URL |
|---|---|
| Web UI | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| DynamoDB Local | http://localhost:8100 |

ホットリロード対応：バックエンドは `uvicorn --reload`、フロントエンドは Vite。

> **macOS の Docker Desktop で uvicorn のホットリロードが効かないとき** — バインドマウントが inotify を取りこぼし、ファイルの内容は更新されているのに `WatchFiles` が再ロードしないことがあります。`docker compose restart backend` で復帰します。

### 個別セットアップ

詳しくは以下を参照してください。

- [ローカル開発セットアップ](docs/SETUP_LOCAL.md)
- [AWS デプロイガイド](docs/DEPLOY_AWS.md)
- [仕様書 (SPEC)](docs/SPEC.md)

### よく使うコマンド

```bash
# Backend
cd backend
pip install -e ".[dev]"
uvicorn src.main:app --reload --port 8000
pytest tests/ -v                                    # 単体テスト
python -m tests.eval.eval_translation               # LLM 評価 (GOOGLE_API_KEY 必須)
python -m tests.eval.eval_summary
ruff check src/ tests/ && ruff format src/ tests/   # Lint / Format
mypy src/                                            # 厳格型チェック

# Web UI
cd frontend/web
npm install && npm run dev

# Chrome 拡張
cd frontend/chrome-extension
npm install && npm run build      # dist/ にバンドル出力
npm run watch                      # 開発中の自動リビルド

# Infrastructure (AWS CDK)
cd infrastructure/cdk
pip install -r requirements.txt
cdk synth && cdk deploy --all
```

## 使い方

### 方法 1：Web UI

1. `http://localhost:5173`（ローカル）または CloudFront URL（本番）を開く
2. arXiv の URL を入力欄に貼り付ける（例：`https://arxiv.org/abs/2301.00001`）
3. **「翻訳開始」** をクリック
4. 進捗バーで各ステージ（ソース取得 → Markdown 変換 → 翻訳 → 要約 → パッケージング）を確認。途中で止めたい場合は **「キャンセル」** をクリック
5. 完了したら **「ダウンロード」** ボタンで ZIP を取得

Web UI は **バックエンドの `GOOGLE_API_KEY`** を使って翻訳します（`.env` で設定）。

### 方法 2：Chrome 拡張機能（ポップアップ）

> **重要**: 現在の拡張はポップアップ専用です。arxiv.org のページ上にボタンが浮かぶ機能（content script）は実装されていません。常にツールバーアイコンから開いて操作します。

1. ツールバーの拡張機能アイコンをクリックしてポップアップを開く
2. arXiv のタブを開いていれば URL 欄に手動で URL を貼り付ける（自動入力は現状なし）
3. 初回は **下部の「設定」** をクリックして以下を入力し、**保存**：
   - **API Endpoint**：`http://localhost:8000`（ローカル）/ `https://<API Gateway URL>`（本番）
   - **Google API Key**：拡張機能はリクエストヘッダ (`X-Google-Api-Key`) で毎回送信するため、ここに入れない限り翻訳は開始できません
4. **「翻訳を開始」** をクリック
5. 進捗バーで状態確認 → 完了後 **「ダウンロード (ZIP)」** をクリック。処理中に **「キャンセル」** をクリックすれば次のステージ境界で停止します

#### 拡張機能の挙動メモ

- **複数ジョブ並走**：拡張は arxiv URL ごとにジョブを並列で保持できます（タブごとに別の論文を翻訳可能）
- **バッジ表示**：エラー時は `!`、それ以外は進行中ジョブ数（個数）を表示。進捗 % はポップアップ内で確認します
- **ジョブ復元**：変換中にポップアップを閉じても、`chrome.storage.local` に状態が保存されており、再度開くと進行中ジョブが復元されます（24 時間で破棄）
- **API Key の扱い**：Web UI と異なり、拡張は **ユーザー入力した Key をヘッダで送信** します。バックエンドの `.env` に何が設定されていてもこのヘッダ値が優先されます

### ZIP の中身

ファイル名にはダウンロード時の取り違えを防ぐため arXiv ID がプレフィックスされます。

```
<arxiv_id>.zip
├── <arxiv_id>_paper_en.md     # 英語 Markdown
├── <arxiv_id>_paper_ja.md     # 日本語翻訳
├── <arxiv_id>_summary_ja.md   # 日本語要約
└── images/                    # 抽出された図表 (PNG / JPG など)
```

---

## Chrome 拡張機能のインストール

### ビルド

```bash
cd frontend/chrome-extension
npm install
npm run build
```

`dist/` 配下にバンドルが出力されます。マニフェストは `dist/<path>.js` を参照するため、TypeScript を編集したら必ず再ビルドが必要です。

### Chrome への読み込み

1. Chrome で `chrome://extensions/` を開く
2. 右上の **「デベロッパーモード」** を有効化
3. **「パッケージ化されていない拡張機能を読み込む」** をクリック
4. `frontend/chrome-extension/dist/` を選択

### 初期設定

1. ツールバーの拡張機能アイコンをクリック
2. ポップアップ下部の **「設定」** をクリックして設定パネルを展開
3. **API Endpoint** と **Google API Key** を入力して **「保存」**

> **本番環境の認証について** — `APP_ENV=development` のときは Cognito 認証はバイパスされます。本番（AWS デプロイ後）の Cognito ログイン UI は現時点では未実装で、ユーザーフローの整備は今後の課題です。

## API

| メソッド | エンドポイント | 説明 |
|---|---|---|
| POST | `/api/v1/convert` | 変換ジョブを作成。`X-Google-Api-Key` ヘッダ（任意・拡張機能から使用）で API Key を渡せる。202 を即時返却 |
| GET | `/api/v1/jobs/{job_id}/status` | ジョブの進捗をポーリング。完了時に `download_url` を返す。`progress` は 0–100 の整数。終端ステータスは `completed` / `error` / `cancelled` |
| POST | `/api/v1/jobs/{job_id}/cancel` | 走行中ジョブのキャンセル要求。次のステージ境界で `cancelled` に遷移する。終端ジョブに対しては 409 |
| GET | `/api/v1/jobs/{job_id}/download` | ZIP ダウンロード。**`S3_BUCKET_NAME` が未設定（ローカル開発）のときのみ有効**。本番では `status` レスポンスの S3 presigned URL を直接使用 |
| GET | `/api/v1/health` | ヘルスチェック |

`/api/v1/jobs/{job_id}/stream`（旧 SSE）は廃止済みです。

## 開発・コントリビューション

- **ブランチ運用**：`develop` から切ってください（Git Flow）。`main` はリリース用です
- **Lint / Format**：Ruff（line-length 120, ターゲット py312, ルール `E F I N W UP B A SIM`）。コミット前に `ruff check` を実行
- **型チェック**：mypy strict モード。新規コードは型を通すこと
- **テスト**：`pytest`（`asyncio_mode = "auto"`、async テストに `@pytest.mark.asyncio` 不要）。LLM 評価テストは実 API を叩くため、PR の CI では対象外（`tests/eval/` を除外）
- **CI**：`.github/workflows/ci.yml`（lint・test・build・cdk synth）と `lint-fix.yml`（自動修正コミット）。`claude-code-review.yml` で PR 上の Claude Code 連携も可

設計指針は SOLID / YAGNI / KISS / DRY / SoC に従っています。詳細は [CLAUDE.md](CLAUDE.md) を参照してください。

## ドキュメント

- [仕様書](docs/SPEC.md) — システム全体の詳細仕様
- [ローカル開発セットアップ](docs/SETUP_LOCAL.md)
- [AWS デプロイガイド](docs/DEPLOY_AWS.md)
- [要約テンプレート（通常論文）](docs/summary_template.md)
- [要約テンプレート（サーベイ論文）](docs/summary_review_template.md)
- [CLAUDE.md](CLAUDE.md) — Claude Code 向けの設計・実装ガイドライン
