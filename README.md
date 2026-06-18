# arXiv Translator

arXiv 論文を日本語に翻訳・要約するサービスです。arXiv の URL を渡すと、ソースを取得し、Markdown 化、日本語翻訳、構造化された要約までを自動で行います。**React Web UI** と **Chrome 拡張機能** の 2 種類のクライアントが、共通の FastAPI バックエンドを呼び出します。

## 機能

- arXiv ソースの取得 — `arxiv.org/e-print/<id>` から TeX ソースを取得
- 決定的な Markdown 変換（LLM ではなく `pandoc` を使用。画像・脚注・引用・数式を保持）
- マルチファイル TeX (`\input` / `\include`) の再帰展開
- 英語 → 日本語翻訳（Markdown 構造と数式表記を維持）
- 論文タイプ（通常論文 / サーベイ）に応じたテンプレートで要約生成
- 進捗のリアルタイム表示（3 秒ポーリング、進捗は 0–100 の整数）
- 処理中のキャンセル — Web UI と Chrome 拡張のどちらからでも、走行中のジョブを停止可能
- 成果物の ZIP ダウンロード

> **Note**: PDF しか公開されていない論文（TeX ソースが無いもの）は変換できません。検出すると `PdfOnlyPaperError` を返し、UI に日本語のエラーメッセージを表示します。

## 動かし方は 2 パターン

このサービスは **ローカル実行** と **AWS デプロイ** のどちらでも動かせます。バックエンドのコードは共通で、環境変数によって動作が切り替わります。まずは前提の少ない **パターン 1（ローカル）** から始めるのがおすすめです。

| | パターン 1: ローカルで動かす | パターン 2: AWS で動かす |
|---|---|---|
| 用途 | 開発・お試し | 本番運用 |
| クライアント | Web UI + Chrome 拡張 | Web UI（CloudFront 配信）のみ — Chrome 拡張は未対応 |
| 必要なもの | Docker + Google API Key | AWS アカウント + AWS CLI / CDK + Google API Key |
| ジョブ実行 | API プロセス内（asyncio） | Lambda 2 段構成（非同期 invoke） |
| ジョブ状態の保存 | DynamoDB Local | DynamoDB |
| 成果物 ZIP | ローカル保存 → API 経由でダウンロード | S3 → presigned URL |
| 認証 | なし（バイパス） | Cognito（ログイン UI は未実装） |
| コスト | 無料（Gemini API の従量課金のみ） | AWS < $2/月 + Gemini API の従量課金 |

どちらのパターンでも Gemini の API キーが必要です（[Google AI Studio](https://aistudio.google.com/apikey) で取得）。

## パターン 1: ローカルで動かす（開発・お試し）

AWS アカウントは不要です。Docker Compose がバックエンド・Web UI・DynamoDB Local をまとめて起動します。

### 1. 環境変数を設定

```bash
git clone git@github.com:JohnYKiyo/ArxivSummaryChromeExtension.git
cd ArxivSummaryChromeExtension
cp .env.example .env
# .env を編集して GOOGLE_API_KEY を設定
```

### 2. 起動

```bash
docker compose up --build
```

| サービス | URL |
|---|---|
| Web UI | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API ドキュメント (Swagger) | http://localhost:8000/docs |
| DynamoDB Local | http://localhost:8100 |

### 3. 使ってみる

http://localhost:5173 を開き、arXiv の URL を貼り付けて「翻訳開始」をクリックします。詳しい操作は [使い方](#使い方) を参照してください。

ホットリロード対応：バックエンド（`backend/src/`）は `uvicorn --reload`、フロントエンド（`frontend/web/src/`）は Vite が自動反映します。

> **macOS の Docker Desktop で uvicorn のホットリロードが効かないとき** — バインドマウントが inotify を取りこぼし、ファイルの内容は更新されているのに `WatchFiles` が再ロードしないことがあります。`docker compose restart backend` で復帰します。

Docker を使わずに Python / Node.js を直接動かす手順、テストの実行方法、トラブルシューティングは [ローカル開発セットアップ](docs/SETUP_LOCAL.md) を参照してください。

## パターン 2: AWS で動かす（本番デプロイ）

AWS CDK でサーバーレス構成（Lambda / API Gateway / DynamoDB / S3 + CloudFront / Cognito）を構築します。低利用なら AWS 費用は月額 $2 未満です。

### 1. 前提

- AWS アカウントと、認証設定済みの AWS CLI v2（`aws configure`）
- AWS CDK v2（`npm install -g aws-cdk`）
- Python 3.12+ / Node.js 20+

### 2. デプロイ

```bash
# 初回のみ: CDK の依存インストールとブートストラップ
cd infrastructure/cdk
pip install -r requirements.txt
cdk bootstrap
cd ../..

# 全体デプロイ（CDK スタック + Lambda + Frontend）
export GOOGLE_API_KEY=your-actual-google-api-key
./infrastructure/deploy.sh
```

`deploy.sh` が CDK スタックのデプロイ、Lambda への環境変数設定、フロントエンドのビルド → S3 アップロード → CloudFront キャッシュ無効化までを一括で実行します。コード変更後の部分更新も同じスクリプトで行えます：

```bash
./infrastructure/deploy.sh --backend     # Backend のみ更新
./infrastructure/deploy.sh --frontend    # Frontend のみ更新
```

### 3. 確認

デプロイ後の各 URL は `infrastructure/cdk-outputs.json` に出力されます。

```bash
curl https://<API Gateway URL>/api/v1/health
# => {"status":"healthy","version":"0.1.0"}
```

Web UI には CloudFront URL でアクセスします。

> **現状の制限（認証まわり）** — 本番 API は `/health` 以外の全エンドポイントで Cognito JWT 認証が必須ですが、クライアント側のログイン実装が未完了のため、**変換をエンドツーエンドに実行できるのは現状パターン 1（ローカル）のみ**です。Web UI は Cognito ログイン UI が未実装、Chrome 拡張はそもそも AWS 接続に未対応（認証なし + `host_permissions` がローカル URL のみ）。ユーザーフローの整備は今後の課題です。

IAM 権限の詳細、Cognito の設定、ログの確認、リソースの削除（`cdk destroy --all`）などは [AWS デプロイガイド](docs/DEPLOY_AWS.md) を参照してください。

## 使い方

### Web UI

1. Web UI を開く — `http://localhost:5173`（AWS の CloudFront 版は認証未実装のため、現状は画面表示までで変換は開始できません）
2. arXiv の URL を入力欄に貼り付ける（例：`https://arxiv.org/abs/2301.00001`）
3. **「翻訳開始」** をクリック
4. 進捗バーで各ステージ（ソース取得 → Markdown 変換 → 翻訳 → 要約 → パッケージング）を確認。途中で止めたい場合は **「キャンセル」** をクリック
5. 完了したら **「ダウンロード」** ボタンで ZIP を取得

Web UI は **バックエンドの `GOOGLE_API_KEY`** を使って翻訳します（ローカルは `.env`、AWS は `deploy.sh` が Lambda に設定）。

### Chrome 拡張機能

> **接続先について**: 拡張が接続できるのは現状 **ローカルバックエンド（`http://localhost:8000`）のみ** です。AWS の本番 API への接続は未対応です — 本番は Cognito 認証が必須（拡張にログイン機能なし）なうえ、manifest の `host_permissions` にローカル URL しか含まれていないためです。

> **重要**: 現在の拡張はポップアップ専用です。arxiv.org のページ上にボタンが浮かぶ機能（content script）は実装されていません。常にツールバーアイコンから開いて操作します。

#### ビルドと Chrome への読み込み

```bash
cd frontend/chrome-extension
npm install
npm run build      # dist/ にバンドル出力
```

1. Chrome で `chrome://extensions/` を開く
2. 右上の **「デベロッパーモード」** を有効化
3. **「パッケージ化されていない拡張機能を読み込む」** をクリック
4. `frontend/chrome-extension/dist/` を選択

マニフェストは `dist/<path>.js` を参照するため、TypeScript を編集したら必ず再ビルド（開発中は `npm run watch`）が必要です。

#### 初期設定

1. ツールバーの拡張機能アイコンをクリックしてポップアップを開く
2. 下部の **「設定」** をクリックして設定パネルを展開し（API Key 未設定の間は自動で開きます）、以下を入力して **保存**：
   - **API Endpoint**：`http://localhost:8000`（現状はローカルバックエンドのみ対応）
   - **Google API Key**：拡張機能はリクエストヘッダ (`X-Google-Api-Key`) で毎回送信するため、ここに入れない限り翻訳は開始できません

#### 翻訳の実行

1. arXiv の論文ページを開いた状態でポップアップを開くと、URL 欄にそのタブの URL が自動入力される（arXiv 以外のタブから開いた場合は手動で貼り付け）
2. **「翻訳を開始」** をクリック
3. 進捗バーで状態確認 → 完了後 **「ダウンロード (ZIP)」** をクリック。処理中に **「キャンセル」** をクリックすれば数秒以内（LLM 実行中でも約 2 秒）で停止します

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

## アーキテクチャ

### 技術スタック

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
fetch_arxiv_paper        (tools/arxiv.py, TeX e-print を取得)
                          ↓
              tex_to_markdown          (tools/tex_to_markdown.py, pandoc サブプロセス)
                          ↓
              TranslationAgent       (agents/translation.py, LLM)
                          ↓
              SummaryAgent           (agents/summary.py, LLM)
                          ↓
              create_zip_package     (tools/packaging.py)
                          ↓
              upload_to_s3 (AWS) または ローカル保存 (ローカル)
```

ソース → Markdown は LLM を使わない決定的変換（pandoc）です。LLM を呼び出すのは翻訳と要約の 2 段のみ。以前は `arxiv.org/html/<id>` を優先していましたが、pandoc 経由の TeX の方が出力が安定するため TeX 専用にしています（HTML 変換のコードは残存していますがパイプラインからは未使用）。

### 実行モデル — ローカルと AWS で何が変わるか

パイプラインのコードは共通で、環境変数で実行モデルが切り替わります。

| 切り替え点 | ローカル（パターン 1） | AWS（パターン 2） |
|---|---|---|
| パイプライン実行 | API プロセス内で `asyncio.create_task`（`PIPELINE_LAMBDA_NAME` 未設定） | API Lambda が Pipeline Lambda を非同期 invoke（同変数を設定） |
| 成果物 ZIP | ローカル保存し `GET /jobs/{job_id}/download` で配信（`S3_BUCKET_NAME` 空） | S3 にアップロードし presigned URL を返却（同変数を設定） |
| 認証 | バイパス（`APP_ENV=development`） | Cognito JWT 検証 |

**AWS で Lambda が 2 段に分かれている理由**：API Gateway は 29 秒でタイムアウトしますが、パイプライン全体は数分かかるためです。

- **API Lambda** (`src.main:handler`, 256 MB / 29 秒) — HTTP リクエスト処理のみ。`POST /convert` で DynamoDB にジョブを作成し、Pipeline Lambda を非同期 invoke して即時に 202 を返します
- **Pipeline Lambda** (`src.pipeline_handler:handler`, 1024 MB / 15 分 / 2 GB 一時ストレージ) — `run_pipeline()` を最後まで実行します

### 進捗連携はポーリング

クライアントは `GET /api/v1/jobs/{job_id}/status` を **3 秒間隔** でポーリングします（SSE は Lambda 互換性のため廃止済み）。Web UI は [useJobPolling](frontend/web/src/features/job-status/hooks/useJobPolling.ts) フックで、拡張機能は [service worker](frontend/chrome-extension/background/service-worker.ts) のループで、同じ間隔で取得します。

### キャンセルは協調式

`POST /api/v1/jobs/{job_id}/cancel` は DynamoDB の `cancel_requested` フラグを立てるだけで、走行中の Lambda やプロセスを直接停止はしません。`run_pipeline()` は各ステージ境界で [`is_cancel_requested`](backend/src/services/job_manager.py) を確認し、さらに翻訳・要約の LLM 呼び出し中も別タスク（`_run_single_agent`）が約 2 秒間隔でフラグを監視します。検出したら `CANCELLED` 終端状態へクリーンに遷移します。

- レイテンシは数秒程度 — LLM 実行中でも約 2 秒（`_CANCEL_POLL_INTERVAL_SECONDS`）以内に進行中の HTTP リクエストを破棄して中断します。非 LLM ステージ（取得・Markdown 変換・パッケージング）はステージ境界で停止します
- ただし中断は**クライアント側のみ** — Gemini のサーバ側生成は止まらないため、その呼び出し分のトークンは課金され得ます（真に停止するには `run_live()` / BIDI が必要だが Flash・32k 制約のため不採用）
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
├── infrastructure/                # AWS CDK (Python) + deploy.sh
│   └── cdk/stacks/                # AuthStack / StorageStack / BackendStack
├── docs/                          # 仕様書・テンプレート
└── .github/workflows/             # CI (ci, lint-fix, claude, claude-code-review)
```

## API

| メソッド | エンドポイント | 説明 |
|---|---|---|
| POST | `/api/v1/convert` | 変換ジョブを作成。`X-Google-Api-Key` ヘッダ（任意・拡張機能から使用）で API Key を渡せる。202 を即時返却 |
| GET | `/api/v1/jobs/{job_id}/status` | ジョブの進捗をポーリング。完了時に `download_url` を返す。`progress` は 0–100 の整数。終端ステータスは `completed` / `error` / `cancelled` |
| POST | `/api/v1/jobs/{job_id}/cancel` | 走行中ジョブのキャンセル要求。LLM 実行中でも数秒以内に `cancelled` へ遷移（中断はクライアント側のみ・サーバ側生成は継続し得る）。終端ジョブには 409 |
| GET | `/api/v1/jobs/{job_id}/download` | ZIP ダウンロード。**`S3_BUCKET_NAME` が未設定（ローカル実行）のときのみ有効**。AWS では `status` レスポンスの S3 presigned URL を直接使用 |
| GET | `/api/v1/health` | ヘルスチェック |

`/api/v1/jobs/{job_id}/stream`（旧 SSE）は廃止済みです。

## 開発・コントリビューション

- **ブランチ運用**：`develop` から切ってください（Git Flow）。`main` はリリース用です
- **Lint / Format**：Ruff（line-length 120, ターゲット py312, ルール `E F I N W UP B A SIM`）。コミット前に `ruff check` を実行
- **型チェック**：mypy strict モード。新規コードは型を通すこと
- **テスト**：`pytest`（`asyncio_mode = "auto"`、async テストに `@pytest.mark.asyncio` 不要）。LLM 評価テストは実 API を叩くため、PR の CI では対象外（`tests/eval/` を除外）
- **CI**：`.github/workflows/ci.yml`（lint・test・build・cdk synth）と `lint-fix.yml`（自動修正コミット）。`claude-code-review.yml` で PR 上の Claude Code 連携も可

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

設計指針は SOLID / YAGNI / KISS / DRY / SoC に従っています。詳細は [CLAUDE.md](CLAUDE.md) を参照してください。

## ドキュメント

- [仕様書](docs/SPEC.md) — システム全体の詳細仕様
- [ローカル開発セットアップ](docs/SETUP_LOCAL.md) — Docker なしの起動手順・テスト・トラブルシューティング
- [AWS デプロイガイド](docs/DEPLOY_AWS.md) — IAM 権限・Cognito 設定・運用・コスト見積もり
- [要約テンプレート（通常論文）](docs/summary_template.md)
- [要約テンプレート（サーベイ論文）](docs/summary_review_template.md)
- [CLAUDE.md](CLAUDE.md) — Claude Code 向けの設計・実装ガイドライン
