# ローカル開発セットアップガイド

arXiv Translator をローカル環境で動かすための手順です。
**Docker を使う方法** と **Docker を使わない方法** の2通りを記載しています。

---

## 前提条件

| ツール | バージョン | 確認コマンド |
|--------|-----------|-------------|
| Git | - | `git --version` |
| Docker & Docker Compose | 24+ | `docker --version && docker compose version` |
| Python | 3.12+ | `python3 --version` |
| Node.js | 20+ | `node --version` |
| npm | 10+ | `npm --version` |
| Google API Key | - | [Google AI Studio](https://aistudio.google.com/apikey) で取得 |

> **Docker を使う場合**: Docker & Docker Compose のみで OK です（Python / Node.js のローカルインストールは不要）。

---

## 共通手順: 環境変数の設定

```bash
# リポジトリをクローン
git clone git@github.com:JohnYKiyo/ArxivSummaryChromeExtension.git
cd ArxivSummaryChromeExtension

# .env ファイルを作成
cp .env.example .env
```

`.env` を編集し、最低限以下を設定してください:

```bash
# 必須: Google Gemini API キー
GOOGLE_API_KEY=your-actual-google-api-key

# LLM モデル (必要に応じて変更)
LLM_MODEL=gemini-2.5-pro-preview-05-06

# ローカル開発用
APP_ENV=development
LOG_LEVEL=DEBUG
CORS_ORIGINS=http://localhost:5173

# DynamoDB Local (Docker Compose で自動設定されるため通常は不要)
# DYNAMODB_TABLE_NAME=arxiv-translator-jobs
# DYNAMODB_ENDPOINT_URL=http://localhost:8100
```

> **Note**: ローカル開発では `COGNITO_*` と `S3_*` の設定は不要です。
> - `APP_ENV=development` の場合、Cognito 認証はスキップされます
> - `S3_BUCKET_NAME` が空の場合、ZIP ファイルはローカルに保存され `GET /api/v1/jobs/{job_id}/download` で取得できます（S3 アカウント不要）
> - DynamoDB Local は Docker Compose で自動的に起動・テーブル作成されます

---

## 方法 1: Docker Compose で起動 (推奨)

最も簡単な方法です。コマンド1つで DynamoDB Local + backend + frontend が起動します。

### 起動

```bash
docker compose up --build
```

### アクセス

| サービス | URL |
|---------|-----|
| Frontend (Web UI) | http://localhost:5173 |
| Backend (API) | http://localhost:8000 |
| API ドキュメント (Swagger) | http://localhost:8000/docs |
| ヘルスチェック | http://localhost:8000/api/v1/health |
| DynamoDB Local | http://localhost:8100 |

### ホットリロード

Docker Compose 開発モードでは、ソースコードの変更が自動的に反映されます:

- **Backend**: `backend/src/` を変更 → uvicorn が自動リロード
- **Frontend**: `frontend/web/src/` を変更 → Vite が自動リロード

### 停止

```bash
# フォアグラウンドで起動した場合: Ctrl+C
# バックグラウンドで起動した場合:
docker compose down
```

### 本番ビルドの確認

```bash
docker compose -f docker-compose.prod.yml up --build
```

本番モードでは:
- Frontend は nginx (ポート 80) で配信
- Backend はリロードなしで起動

| サービス | URL |
|---------|-----|
| Frontend (nginx) | http://localhost |
| Backend (API) | http://localhost:8000 |

---

## 方法 2: Docker を使わずに起動

### 2-1. Backend の起動

```bash
cd backend

# 仮想環境を作成・有効化
python3 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# 依存関係をインストール
pip install -e ".[dev]"

# .env を backend ディレクトリにコピー (またはシンボリックリンク)
cp ../.env .env

# 開発サーバー起動
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend が起動したことを確認:

```bash
curl http://localhost:8000/api/v1/health
# => {"status":"healthy","version":"0.1.0"}
```

### 2-2. Frontend の起動

別のターミナルで:

```bash
cd frontend/web

# 依存関係をインストール
npm install

# 開発サーバー起動
npm run dev
```

### アクセス

| サービス | URL |
|---------|-----|
| Frontend (Vite dev) | http://localhost:5173 |
| Backend (API) | http://localhost:8000 |
| API ドキュメント (Swagger) | http://localhost:8000/docs |

> **Note**: Vite の開発サーバーは `/api/*` へのリクエストを `http://localhost:8000` にプロキシします (`vite.config.ts` で設定済み)。

---

## テストの実行

### Backend テスト

```bash
cd backend
source .venv/bin/activate

# 全テストを実行
pytest tests/ -v

# Agent テストのみ
pytest tests/test_agents/ -v

# API テストのみ
pytest tests/test_api/ -v

# カバレッジ付き
pytest tests/ --cov=src --cov-report=html
```

### LLM 評価テスト (Evaluation)

> **Note**: 評価テストは実際に Gemini API を呼び出すため、`GOOGLE_API_KEY` が必要です。

```bash
cd backend

# Tex → Markdown 変換の評価
python -m tests.eval.eval_tex2markdown

# 翻訳の評価
python -m tests.eval.eval_translation

# 要約の評価
python -m tests.eval.eval_summary
```

### Frontend リント & 型チェック

```bash
cd frontend/web

# 型チェック
npx tsc --noEmit

# リント
npm run lint

# ビルド確認
npm run build
```

---

## Chrome 拡張機能の開発

### ビルド

```bash
cd frontend/chrome-extension
npm install
npm run build       # 一回ビルド → dist/ に出力
npm run watch       # ウォッチモード（変更時に自動ビルド）
```

### Chrome への読み込み

1. Chrome で `chrome://extensions/` を開く
2. 右上の「デベロッパーモード」を有効にする
3. 「パッケージ化されていない拡張機能を読み込む」をクリック
4. `frontend/chrome-extension/dist/` フォルダを選択

> **Note**: `dist/` を読み込むので、ソースを変更した後は `npm run build`（またはウォッチ中なら自動ビルド）が必要です。

### 設定

拡張機能のポップアップで API エンドポイントを設定:
- ローカル開発: `http://localhost:8000`

### 動作確認

1. [arXiv](https://arxiv.org/) の論文ページ (例: `https://arxiv.org/abs/2301.00001`) にアクセス
2. ページ上に表示される「翻訳・要約」ボタンをクリック
3. ポップアップが開き、URL が自動入力される
4. 「変換開始」をクリック

---

## トラブルシューティング

### Docker 関連

**Q: `docker compose up` でポートが使用中と出る**

```bash
# 使用中のポートを確認
lsof -i :8000
lsof -i :5173

# 該当プロセスを停止するか、docker-compose.yml でポートを変更
```

**Q: Backend のヘルスチェックが失敗する**

```bash
# ログを確認
docker compose logs backend

# .env の GOOGLE_API_KEY が正しく設定されているか確認
docker compose exec backend env | grep GOOGLE
```

### Backend 関連

**Q: `ModuleNotFoundError: No module named 'src'`**

```bash
# backend/ ディレクトリから実行しているか確認
cd backend
# editable モードでインストール
pip install -e ".[dev]"
```

**Q: `GOOGLE_API_KEY` 関連のエラー**

```bash
# .env ファイルが存在するか確認
ls -la .env

# 環境変数が読み込まれているか確認
python3 -c "from src.config import get_settings; print(get_settings().GOOGLE_API_KEY[:10] + '...')"
```

### Frontend 関連

**Q: API リクエストが `404` を返す**

- Backend が起動しているか確認: `curl http://localhost:8000/api/v1/health`
- `vite.config.ts` の proxy 設定を確認

**Q: `npm install` が失敗する**

```bash
# Node.js のバージョンを確認 (20+ 必要)
node --version

# キャッシュをクリアして再試行
rm -rf node_modules package-lock.json
npm install
```

---

## ディレクトリ構成 (開発時)

```
ArxivSummaryChromeExtension/
├── .env                    ← 環境変数 (git 管理外)
├── docker-compose.yml      ← Docker 開発用
├── docker-compose.prod.yml ← Docker 本番用
│
├── backend/
│   ├── .venv/              ← Python 仮想環境 (Docker 不使用時)
│   ├── src/                ← バックエンドソースコード
│   └── tests/              ← テスト & 評価
│
├── frontend/
│   ├── web/                ← React Web UI
│   │   └── node_modules/   ← Node 依存 (Docker 不使用時)
│   └── chrome-extension/   ← Chrome 拡張機能
│
└── docs/
    ├── SPEC.md
    ├── summary_template.md
    └── summary_review_template.md
```
