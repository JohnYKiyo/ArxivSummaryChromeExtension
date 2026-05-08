# arXiv Translator

arXiv 論文を日本語に翻訳・要約するサービスです。arXiv の URL を入力すると、TeX ソースを取得し、Markdown に変換、日本語に翻訳、構造化された要約を生成します。**Web UI** と **Chrome 拡張機能** の両方で利用できます。

## 機能

- arXiv 論文の TeX ソースを自動取得・展開
- TeX → Markdown 変換（画像、脚注、引用、数式を保持）
- 英語 → 日本語翻訳（Markdown 構造を維持）
- 論文タイプ（通常論文 / サーベイ論文）に応じた要約生成
- リアルタイム進捗表示（ポーリング方式）
- 成果物を ZIP ファイルとしてダウンロード

## アーキテクチャ

| レイヤー | 技術スタック |
|---|---|
| Frontend (Web) | React 18 + TypeScript + Vite + Tailwind CSS |
| Frontend (Extension) | Chrome Extension Manifest V3 |
| Backend | Python 3.12+ / FastAPI (Mangum 経由で Lambda 上で実行) |
| AI Agent | Google ADK (Agent Development Kit) + Gemini |
| Infrastructure | AWS — Lambda, API Gateway, DynamoDB, CloudFront + S3, Cognito |

## ディレクトリ構成

```
ArxivSummaryChromeExtension/
├── backend/                 # FastAPI バックエンド
│   ├── src/
│   │   ├── agents/          # Google ADK エージェント群
│   │   ├── api/             # API ルート・認証
│   │   ├── services/        # DynamoDB ジョブ管理
│   │   └── tools/           # arXiv 取得・ZIP パッケージング
│   └── tests/               # ユニットテスト・評価
├── frontend/
│   ├── web/                 # React Web UI
│   └── chrome-extension/    # Chrome 拡張機能
├── infrastructure/          # AWS CDK
├── docs/                    # 仕様書・テンプレート
└── .github/workflows/       # CI
```

## セットアップ

### Docker Compose（推奨）

```bash
cp .env.example .env
# .env に GOOGLE_API_KEY を設定
docker compose up
```

- Web UI: http://localhost:5173
- Backend API: http://localhost:8000
- DynamoDB Local: http://localhost:8100

### 個別セットアップ

詳細は以下を参照:

- [ローカル開発セットアップ](docs/SETUP_LOCAL.md)
- [AWS デプロイ](docs/DEPLOY_AWS.md)

## Chrome 拡張機能のインストール

### ビルド

```bash
cd frontend/chrome-extension
npm install
npm run build
```

### Chrome への読み込み

1. Chrome で `chrome://extensions/` を開く
2. 右上の **「デベロッパーモード」** を有効にする
3. **「パッケージ化されていない拡張機能を読み込む」** をクリック
4. `frontend/chrome-extension/dist/` フォルダを選択

### 初期設定

1. Chrome ツールバーの拡張機能アイコンをクリック
2. 設定画面で **API エンドポイント** を入力
   - ローカル: `http://localhost:8000`
   - 本番: `https://<API Gateway URL>`
3. Cognito アカウントでログイン

### 使い方

1. [arxiv.org](https://arxiv.org) の論文ページ（例: `https://arxiv.org/abs/2301.00001`）を開く
2. ページ上に表示される **「翻訳・要約」** ボタンをクリック、またはツールバーアイコンから拡張機能を開く
3. URL が自動入力された状態で **「変換開始」** をクリック
4. 進捗バーで各ステージ（TeX取得 → Markdown変換 → 翻訳 → 要約）を確認
5. 完了後、**「ダウンロード」** ボタンから ZIP ファイルを取得

### ZIP の中身

```
output.zip
├── paper_en.md     # 英語 Markdown
├── paper_ja.md     # 日本語翻訳
├── summary_ja.md   # 日本語要約
└── images/         # 論文中の図表
```

## API

| メソッド | エンドポイント | 説明 |
|---|---|---|
| POST | `/api/v1/convert` | 変換ジョブを作成（202 即時返却） |
| GET | `/api/v1/jobs/{job_id}/status` | ジョブ進捗をポーリング |
| GET | `/api/v1/health` | ヘルスチェック |

## ドキュメント

- [仕様書](docs/SPEC.md) — システム全体の詳細仕様
- [ローカル開発セットアップ](docs/SETUP_LOCAL.md)
- [AWS デプロイガイド](docs/DEPLOY_AWS.md)
- [要約テンプレート（通常論文）](docs/summary_template.md)
- [要約テンプレート（サーベイ論文）](docs/summary_review_template.md)
