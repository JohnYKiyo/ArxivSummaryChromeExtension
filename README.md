# arXiv Translator

arXiv 論文を日本語に翻訳・要約するサービスです。arXiv の URL を入力すると、TeX ソースを取得し、Markdown に変換、日本語に翻訳、構造化された要約を生成します。Web UI と Chrome 拡張機能の両方で利用できます。

## 機能

- arXiv 論文の TeX ソースを自動取得・展開
- TeX → Markdown 変換（画像、脚注、引用、数式を保持）
- 英語 → 日本語翻訳（Markdown 構造を維持）
- 論文タイプ（通常論文 / サーベイ論文）に応じた要約生成
- SSE によるリアルタイム進捗表示
- 成果物を ZIP ファイルとしてダウンロード

## アーキテクチャ

| レイヤー | 技術スタック |
|---|---|
| Frontend (Web) | React 18 + TypeScript + Vite + Tailwind CSS |
| Frontend (Extension) | Chrome Extension Manifest V3 |
| Backend | Python 3.12+ / FastAPI |
| AI Agent | Google ADK (Agent Development Kit) + Gemini |
| Infrastructure | AWS — ECS Fargate, ALB, CloudFront + S3, Cognito |

## ディレクトリ構成

```
ArxivSummaryChromeExtension/
├── backend/                 # FastAPI バックエンド
│   ├── src/
│   │   ├── agents/          # Google ADK エージェント群
│   │   ├── api/             # API ルート・認証
│   │   ├── services/        # ジョブ管理・SSE
│   │   └── tools/           # arXiv 取得・ZIP パッケージング
│   └── tests/               # ユニットテスト・評価
├── frontend/
│   ├── web/                 # React Web UI
│   └── chrome-extension/    # Chrome 拡張機能
├── infrastructure/          # AWS CDK
├── docs/                    # 仕様書・テンプレート
└── .github/workflows/       # CI / Claude Code 連携
```

## セットアップ

### Docker Compose（推奨）

```bash
cp .env.example .env
# .env に GOOGLE_API_KEY などを設定
docker compose up
```

- Web UI: http://localhost:5173
- Backend API: http://localhost:8000

### 個別セットアップ

詳細は以下を参照:

- [ローカル開発セットアップ](docs/SETUP_LOCAL.md)
- [AWS デプロイ](docs/DEPLOY_AWS.md)

## API

| メソッド | エンドポイント | 説明 |
|---|---|---|
| POST | `/api/v1/convert` | 変換ジョブを作成 |
| GET | `/api/v1/jobs/{job_id}/stream` | SSE で進捗をストリーミング |
| GET | `/api/v1/jobs/{job_id}/download` | ZIP ファイルをダウンロード |
| GET | `/api/v1/health` | ヘルスチェック |

## ドキュメント

- [仕様書](docs/SPEC.md) — システム全体の詳細仕様
- [要約テンプレート（通常論文）](docs/summary_template.md)
- [要約テンプレート（サーベイ論文）](docs/summary_review_template.md)
