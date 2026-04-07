# AWS デプロイガイド

arXiv Translator を AWS 上に本番デプロイするための手順です。

---

## アーキテクチャ

```
                Internet
                   │
           ┌───────┴───────┐
           │               │
    ┌──────▼──────┐  ┌─────▼──────┐
    │ CloudFront  │  │    ALB     │
    │  (HTTPS)    │  │ (idle=300s)│
    │             │  │            │
    │ Frontend    │  │  /api/*    │
    └──────┬──────┘  └─────┬──────┘
           │               │
    ┌──────▼──────┐  ┌─────▼──────────────┐
    │ S3 Bucket   │  │  ECS Fargate       │
    │ (Static)    │  │  (FastAPI + ADK)   │
    └─────────────┘  │                    │
                     │  Private Subnet    │
                     └─────┬──────┬───────┘
                           │      │
                     ┌─────▼──┐ ┌─▼───────────┐
                     │Cognito │ │ S3 Bucket    │
                     │User    │ │ (ZIP Output) │
                     │Pool    │ │ TTL: 1 hour  │
                     └────────┘ └──────────────┘

VPC (2 AZs)
┌──────────────────────────────────────────┐
│  Public Subnets      Private Subnets     │
│  ├── ALB             ├── ECS Tasks       │
│  └── NAT Gateway     └── (Gemini API →)  │
└──────────────────────────────────────────┘
```

### 設計判断

| 項目 | 選択 | 理由 |
|------|------|------|
| API Gateway | **不採用** | SSE の最大接続時間が 29 秒に制限されるため |
| ALB | **採用** | idle timeout を 300 秒に設定し、論文処理中の SSE 接続を維持 |
| ECS Fargate | **採用** | SSE は長時間の持続的接続が必要。Lambda の 15 分制限・ストリーミング非対応のため不適 |
| NAT Gateway | **1 つのみ** | コスト最適化。本番 HA 構成にする場合は AZ ごとに 1 つに増設 |

---

## 前提条件

### ツール

```bash
# AWS CLI v2
aws --version        # 2.x 以上

# AWS CDK v2
cdk --version        # 2.x 以上 (npm install -g aws-cdk)

# Docker
docker --version     # 24+ 以上

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

- `ec2:*` (VPC, Subnet, Security Group)
- `ecs:*` (Cluster, Service, Task)
- `ecr:*` (Repository, Image Push)
- `elasticloadbalancing:*` (ALB, Target Group)
- `s3:*` (Bucket, Object)
- `cloudfront:*` (Distribution, Invalidation)
- `cognito-idp:*` (User Pool)
- `logs:*` (CloudWatch Logs)
- `iam:*` (Role, Policy for ECS Task)
- `cloudformation:*` (CDK がスタック管理に使用)

> **Tip**: 初回は `AdministratorAccess` で試し、本番では最小権限ポリシーに絞ってください。

---

## デプロイ手順

### Step 1: Google API Key の準備

[Google AI Studio](https://aistudio.google.com/apikey) で API キーを取得します。

後のステップで AWS Secrets Manager に保存するか、ECS タスク定義に直接設定します。

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
# => ArxivTranslatorNetwork
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

# 全体デプロイ (CDK + Docker + Frontend)
./infrastructure/deploy.sh
```

`deploy.sh` は以下を順に実行します:

1. **CDK Bootstrap** — 初回のみ、S3 バケットなどを準備
2. **CDK Deploy** — 4 つのスタックを順次デプロイ
3. **Docker Build & Push** — Backend イメージをビルドし ECR にプッシュ
4. **Frontend Build & Upload** — Vite ビルド → S3 にアップロード → CloudFront キャッシュ無効化

### 部分デプロイ

```bash
# Backend のみ更新 (CDK + Docker image push + ECS 再デプロイ)
./infrastructure/deploy.sh --backend

# Frontend のみ更新 (S3 アップロード + CloudFront 無効化)
./infrastructure/deploy.sh --frontend

# CDK スタックのみ (インフラ変更時)
./infrastructure/deploy.sh --cdk-only
```

### Step 5: Google API Key の設定

デプロイ完了後、ECS タスクに `GOOGLE_API_KEY` を設定します。

#### 方法 A: AWS Secrets Manager (推奨)

```bash
# シークレットを作成
aws secretsmanager create-secret \
    --name arxiv-translator/google-api-key \
    --secret-string "your-actual-google-api-key" \
    --region ap-northeast-1

# ECS タスク定義を更新して Secrets Manager から参照
# (CDK の backend_stack.py で secrets 設定を追加)
```

#### 方法 B: ECS コンソールから直接設定

1. AWS コンソール → ECS → クラスター `arxiv-translator`
2. サービス `arxiv-translator-backend` → タスク定義を更新
3. コンテナ環境変数に `GOOGLE_API_KEY` を追加
4. サービスを更新して新しいタスク定義をデプロイ

### Step 6: Cognito の設定

CDK デプロイにより Cognito User Pool が自動作成されます。

```bash
# デプロイ出力から Cognito の情報を取得
cat infrastructure/cdk-outputs.json | python3 -m json.tool
```

出力例:
```json
{
  "ArxivTranslatorAuth": {
    "UserPoolId": "ap-northeast-1_XXXXXXXXX",
    "UserPoolClientId": "xxxxxxxxxxxxxxxxxxxxxxxxxx",
    "UserPoolDomain": "arxiv-translator-xxxxxxxx"
  }
}
```

#### Frontend の環境変数を設定

`frontend/web/.env.production` を作成:

```bash
VITE_API_BASE_URL=https://<alb-dns-name>
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
# ALB の DNS 名を取得
ALB_URL=$(cat infrastructure/cdk-outputs.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in data.get('ArxivTranslatorBackend', {}).items():
    if 'ALB' in k or 'LoadBalancer' in k:
        print(v)
        break
")

# ヘルスチェック
curl http://${ALB_URL}/api/v1/health
# => {"status":"healthy","version":"0.1.0"}
```

### Frontend の確認

```bash
# CloudFront の URL を取得
CF_URL=$(cat infrastructure/cdk-outputs.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in data.get('ArxivTranslatorStorage', {}).items():
    if 'CloudFront' in k and 'Domain' in k:
        print(v)
        break
")

echo "Frontend URL: https://${CF_URL}"
```

ブラウザで `https://<CloudFront Domain>` にアクセスして Web UI が表示されることを確認。

### テスト変換

```bash
# API に直接リクエスト
curl -X POST http://${ALB_URL}/api/v1/convert \
  -H "Content-Type: application/json" \
  -d '{"arxiv_url": "https://arxiv.org/abs/2301.00001"}'
```

---

## 運用

### ログの確認

```bash
# ECS タスクのログ (CloudWatch Logs)
aws logs tail /ecs/arxiv-translator-backend --follow --region ap-northeast-1

# 直近 1 時間のエラーログ
aws logs filter-log-events \
    --log-group-name /ecs/arxiv-translator-backend \
    --start-time $(date -d '1 hour ago' +%s000) \
    --filter-pattern "ERROR" \
    --region ap-northeast-1
```

### スケーリング

```bash
# Fargate タスク数を変更
aws ecs update-service \
    --cluster arxiv-translator \
    --service arxiv-translator-backend \
    --desired-count 2 \
    --region ap-northeast-1
```

### Backend の更新

コード変更後:

```bash
# Docker イメージを再ビルド → ECR にプッシュ → ECS 再デプロイ
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

最小構成 (Fargate タスク 1 台、低トラフィック) の月額概算:

| サービス | 概算コスト |
|---------|-----------|
| NAT Gateway | ~$35/月 + データ転送量 |
| ECS Fargate (0.5 vCPU, 1 GB) | ~$15/月 |
| ALB | ~$18/月 + LCU |
| CloudFront | ~$1/月 (低トラフィック) |
| S3 | < $1/月 |
| Cognito | 無料枠 (50,000 MAU まで) |
| ECR | < $1/月 |
| CloudWatch Logs | ~$1/月 |
| **合計** | **~$70-80/月** |

### コスト削減のヒント

- **未使用時は Fargate タスクを 0 に**: `aws ecs update-service --desired-count 0 ...`
- **NAT Gateway の代替**: NAT Instance (t3.nano) で ~$5/月 に削減可能
- **Fargate Spot**: 非クリティカルな用途なら最大 70% OFF

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

手動確認が必要な項目:
- CloudWatch Logs のロググループ (保持期間後に自動削除)
- ECR リポジトリ内のイメージ (ライフサイクルポリシーで管理)

---

## CI/CD パイプライン

GitHub Actions による自動 CI が設定済みです (`.github/workflows/ci.yml`):

| ジョブ | トリガー | 内容 |
|-------|---------|------|
| `backend-lint` | push / PR | Ruff lint & format check |
| `backend-test` | push / PR | pytest (eval 除外) |
| `backend-docker` | push / PR | Docker ビルド確認 |
| `frontend-lint` | push / PR | TypeScript 型チェック |
| `frontend-build` | push / PR | Vite ビルド確認 |
| `frontend-docker` | push / PR | Docker ビルド確認 |
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
