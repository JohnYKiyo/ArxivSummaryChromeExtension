# AWS デプロイガイド

arXiv Translator を AWS 上に本番デプロイするための手順です。

---

## アーキテクチャ

```
                Internet
                   │
           ┌───────┴───────┐
           │               │
    ┌──────▼──────┐  ┌─────▼──────────┐
    │ CloudFront  │  │  API Gateway   │
    │  (HTTPS)    │  │  (HTTP API)    │
    │             │  │                │
    │ Frontend    │  │  /api/v1/*     │
    └──────┬──────┘  └─────┬──────────┘
           │               │
    ┌──────▼──────┐  ┌─────▼──────────────┐
    │ S3 Bucket   │  │  API Lambda        │
    │ (Static)    │  │  (FastAPI+Mangum)  │
    └─────────────┘  │  timeout: 29s      │
                     └─────┬──────┬───────┘
                           │      │
                     ┌─────▼──┐   │ async invoke
                     │Cognito │   │
                     │User    │   ▼
                     │Pool    │  ┌──────────────────┐
                     └────────┘  │ Pipeline Lambda   │
                                 │ (Agent Pipeline)  │
                                 │ timeout: 15min    │
                                 └──┬──────┬────────┘
                                    │      │
                              ┌─────▼──┐ ┌─▼───────────┐
                              │DynamoDB │ │ S3 Bucket    │
                              │(Jobs)   │ │ (ZIP Output) │
                              │TTL: 1h  │ │ TTL: 1 day   │
                              └─────────┘ └──────────────┘
```

### 設計判断

| 項目 | 選択 | 理由 |
|------|------|------|
| Lambda + API Gateway | **採用** | サーバーレスで使用時のみ課金。月額 $1 未満 |
| SSE | **不採用** | Lambda/API GW のタイムアウト制約。代わりにポーリング方式を採用 |
| DynamoDB | **採用** | ジョブ状態の永続化。TTL で自動クリーンアップ |
| VPC/NAT Gateway | **不要** | Lambda は外部 API (Gemini, arXiv) と AWS サービスに直接アクセス |

### コスト比較 (旧アーキテクチャ vs 新アーキテクチャ)

| 項目 | 旧 (ECS Fargate) | 新 (Lambda) |
|------|------------------|-------------|
| NAT Gateway | ~$33/月 | $0 |
| ALB | ~$18/月 | $0 |
| ECS Fargate | ~$15/月 | $0 |
| Lambda | - | ~$0.10/月 |
| API Gateway | - | ~$0.01/月 |
| DynamoDB | - | ~$0.05/月 |
| **合計** | **~$70/月** | **~$0.20/月** |

---

## 前提条件

### ツール

```bash
# AWS CLI v2
aws --version        # 2.x 以上

# AWS CDK v2
cdk --version        # 2.x 以上 (npm install -g aws-cdk)

# Python
python3 --version    # 3.12+

# Node.js
node --version       # 20+
```

### AWS アカウント設定

```bash
# AWS CLI にクレデンシャルを設定
aws configure
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, Region (ap-northeast-1) を入力

# 設定確認
aws sts get-caller-identity
```

### 必要な IAM 権限

デプロイユーザー/ロールには以下の権限が必要です:

- `lambda:*` (Function, Layer)
- `apigateway:*` (HTTP API)
- `dynamodb:*` (Table)
- `s3:*` (Bucket, Object)
- `cloudfront:*` (Distribution, Invalidation)
- `cognito-idp:*` (User Pool)
- `logs:*` (CloudWatch Logs)
- `iam:*` (Role, Policy for Lambda)
- `cloudformation:*` (CDK がスタック管理に使用)

> **Tip**: 初回は `AdministratorAccess` で試し、本番では最小権限ポリシーに絞ってください。

---

## デプロイ手順

### Step 1: Google API Key の準備

[Google AI Studio](https://aistudio.google.com/apikey) で API キーを取得します。

### Step 2: CDK の初期セットアップ

```bash
# CDK の Python 依存をインストール
cd infrastructure/cdk
pip install -r requirements.txt

# CDK Bootstrap (初回のみ、リージョンごとに 1 回)
cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/ap-northeast-1
```

### Step 3: CDK スタックの確認

```bash
cd infrastructure/cdk

# デプロイされるスタック一覧
cdk ls
# => ArxivTranslatorAuth
# => ArxivTranslatorStorage
# => ArxivTranslatorBackend

# 変更差分の確認 (dry-run)
cdk diff

# CloudFormation テンプレートの生成 (確認用)
cdk synth
```

### Step 4: 全体デプロイ (deploy.sh)

```bash
# プロジェクトルートに戻る
cd /path/to/ArxivSummaryChromeExtension

# GOOGLE_API_KEY を設定
export GOOGLE_API_KEY=your-actual-google-api-key

# 全体デプロイ (CDK + Lambda + Frontend)
./infrastructure/deploy.sh
```

`deploy.sh` は以下を順に実行します:

1. **CDK Bootstrap** — 初回のみ、S3 バケットなどを準備
2. **CDK Deploy** — 3 つのスタックを順次デプロイ (Lambda, API Gateway, DynamoDB 等)
3. **Lambda 環境変数設定** — GOOGLE_API_KEY を Lambda に設定
4. **Frontend Build & Upload** — Vite ビルド → S3 にアップロード → CloudFront キャッシュ無効化

### 部分デプロイ

```bash
# Backend のみ更新 (CDK deploy + Lambda env update)
./infrastructure/deploy.sh --backend

# Frontend のみ更新 (S3 アップロード + CloudFront 無効化)
./infrastructure/deploy.sh --frontend

# CDK スタックのみ (インフラ変更時)
./infrastructure/deploy.sh --cdk-only
```

### Step 5: Cognito の設定

CDK デプロイにより Cognito User Pool が自動作成されます。

```bash
# デプロイ出力から Cognito の情報を取得
cat infrastructure/cdk-outputs.json | python3 -m json.tool
```

#### Frontend の環境変数を設定

`frontend/web/.env.production` を作成:

```bash
VITE_API_BASE_URL=https://<api-gateway-url>
VITE_COGNITO_USER_POOL_ID=ap-northeast-1_XXXXXXXXX
VITE_COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
VITE_COGNITO_DOMAIN=arxiv-translator-xxxxxxxx.auth.ap-northeast-1.amazoncognito.com
```

その後、Frontend を再ビルド・アップロード:

```bash
./infrastructure/deploy.sh --frontend
```

---

## デプロイ後の確認

### ヘルスチェック

```bash
# API Gateway の URL を取得
API_URL=$(cat infrastructure/cdk-outputs.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in data.get('ArxivTranslatorBackend', {}).items():
    if 'ApiUrl' in k:
        print(v.rstrip('/'))
        break
")

# ヘルスチェック
curl ${API_URL}/api/v1/health
# => {"status":"healthy","version":"0.1.0"}
```

### テスト変換

```bash
# API に直接リクエスト
curl -X POST ${API_URL}/api/v1/convert \
  -H "Content-Type: application/json" \
  -d '{"arxiv_url": "https://arxiv.org/abs/2301.00001"}'
```

---

## 運用

### ログの確認

```bash
# API Lambda のログ
aws logs tail /lambda/arxiv-translator-api --follow --region ap-northeast-1

# Pipeline Lambda のログ
aws logs tail /lambda/arxiv-translator-pipeline --follow --region ap-northeast-1

# 直近 1 時間のエラーログ
aws logs filter-log-events \
    --log-group-name /lambda/arxiv-translator-pipeline \
    --start-time $(date -d '1 hour ago' +%s000) \
    --filter-pattern "ERROR" \
    --region ap-northeast-1
```

### Lambda の更新

コード変更後:

```bash
# CDK 再デプロイで Lambda コードも更新される
./infrastructure/deploy.sh --backend
```

### Frontend の更新

コード変更後:

```bash
# Vite ビルド → S3 アップロード → CloudFront キャッシュ無効化
./infrastructure/deploy.sh --frontend
```

---

## コスト見積もり (ap-northeast-1)

サーバーレス構成のため、使用量に応じた課金です:

| サービス | 概算コスト |
|---------|-----------|
| Lambda (API + Pipeline) | ~$0.10/月 (低利用時) |
| API Gateway (HTTP API) | ~$0.01/月 |
| DynamoDB (オンデマンド) | ~$0.05/月 |
| CloudFront | ~$1/月 (低トラフィック) |
| S3 | < $1/月 |
| Cognito | 無料枠 (50,000 MAU まで) |
| CloudWatch Logs | ~$0.50/月 |
| **合計** | **< $2/月** |

> **Note**: Gemini API の料金は Google 側で別途課金されます。

---

## リソースの削除

全ての AWS リソースを削除するには:

```bash
cd infrastructure/cdk

# 全スタックを削除
cdk destroy --all
```

> **Note**: S3 バケットは `auto_delete_objects=True` で設定されているため、
> バケット内のオブジェクトも自動的に削除されます。

---

## CI/CD パイプライン

GitHub Actions による自動 CI が設定済みです (`.github/workflows/ci.yml`):

| ジョブ | トリガー | 内容 |
|-------|---------|------|
| `backend-lint` | push / PR | Ruff lint & format check |
| `backend-test` | push / PR | pytest (eval 除外) |
| `frontend-lint` | push / PR | TypeScript 型チェック |
| `frontend-build` | push / PR | Vite ビルド確認 |
| `cdk-synth` | push / PR | CDK テンプレート生成確認 |

### 自動デプロイの追加 (オプション)

`main` ブランチへのマージ時に自動デプロイしたい場合は、
GitHub Secrets に以下を設定し、deploy ジョブを追加してください:

```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_ACCOUNT_ID
GOOGLE_API_KEY
```
