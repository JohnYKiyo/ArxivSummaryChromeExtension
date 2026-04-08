# Code Review Guidelines

このドキュメントは Claude Code Review 専用のガイドラインです。
PR レビュー時に自動的に読み込まれ、以下の観点でコードをチェックします。

---

## Always check

### 設計原則 (SOLID / KISS / DRY / YAGNI / SoC)

- **SRP (Single Responsibility Principle)**: 1 つのモジュール・クラス・関数は 1 つの責務のみを持つこと。複数の理由で変更される関数はフラグを立てる
- **OCP (Open/Closed Principle)**: 拡張に対して開き、修正に対して閉じていること。新しい振る舞いの追加に既存コードの修正が必要な設計はフラグを立てる
- **LSP (Liskov Substitution Principle)**: サブクラスは親クラスの契約を破らないこと
- **ISP (Interface Segregation Principle)**: クライアントが使わないメソッドへの依存を強制しないこと。肥大化したインターフェースはフラグを立てる
- **DIP (Dependency Inversion Principle)**: 上位モジュールは下位モジュールに依存しない。どちらも抽象に依存すること
- **KISS**: 不必要に複雑な実装はフラグを立てる。シンプルな方法で同じ結果を達成できる場合はそちらを推奨する
- **DRY**: 同一ロジックの重複が 3 箇所以上ある場合はフラグを立てる。ただし、早すぎる抽象化よりも明示的な重複の方がましな場合がある点に留意する
- **YAGNI**: 現在の要件に不要な機能・抽象・設定の追加はフラグを立てる。将来の仮想的な要件のための実装を避ける
- **SoC (Separation of Concerns)**: プレゼンテーション・ビジネスロジック・データアクセスが混在している場合はフラグを立てる

### Clean Architecture (バックエンド: Python / FastAPI)

バックエンドは以下のレイヤー構成に従うこと。依存の方向は外側から内側への一方向のみ許可する。

```
backend/src/
├── api/          # Interface Adapters - ルーティング、リクエスト/レスポンス変換
├── services/     # Use Cases - アプリケーション固有のビジネスルール
├── agents/       # Use Cases - AI エージェントのオーケストレーション
├── tools/        # Interface Adapters - 外部サービスとの通信
├── models/       # Entities - ドメインモデル、ビジネスルール (フレームワーク非依存)
└── config.py     # Frameworks & Drivers - 設定
```

- `api/` レイヤーが `services/` や `agents/` を直接インポートすることは許可するが、逆方向の依存はフラグを立てる
- `services/` や `agents/` が FastAPI の `Request`/`Response` オブジェクトに直接依存している場合はフラグを立てる
- `tools/` の外部 API クライアントがビジネスロジックを含んでいる場合はフラグを立てる
- ドメインモデル (`models/`) はフレームワークに依存しないこと（Pydantic の `BaseModel` は許可）

### Feature-based Architecture + Colocation (フロントエンド: React / TypeScript)

フロントエンドは Feature-based Architecture を採用し、機能単位でコードを凝集させる。共通UIは `shared/` に配置する。

#### ディレクトリ構成

```
frontend/web/src/
├── features/                    # 機能単位のモジュール
│   ├── paper-convert/           # 論文変換機能
│   │   ├── components/          # この機能固有の UI コンポーネント
│   │   ├── hooks/               # この機能固有のフック
│   │   ├── services/            # この機能固有の API クライアント
│   │   ├── types.ts             # この機能固有の型定義
│   │   └── index.ts             # 公開 API (re-export)
│   ├── job-status/              # ジョブ状態表示・SSE ストリーム
│   └── auth/                    # 認証
├── shared/                      # 機能横断の共通モジュール
│   ├── components/              # 共通 UI コンポーネント (Button, Layout, etc.)
│   ├── hooks/                   # 共通フック
│   ├── services/                # 共通 API クライアント・ユーティリティ
│   └── types/                   # 共通型定義
├── pages/                       # ルーティング対象のページコンポーネント
└── app/                         # アプリ初期化、プロバイダー、ルーティング設定
```

#### 依存ルール

- **feature → shared**: 許可。共通モジュールへの依存は自由
- **feature → feature**: 禁止。機能間の直接依存はフラグを立てる。共通化すべきものは `shared/` に抽出する
- **shared → feature**: 禁止。共通モジュールが特定機能に依存している場合はフラグを立てる
- **pages → features + shared**: 許可。ページは機能と共通モジュールを組み合わせる場所

#### Colocation

関連ファイルは同じディレクトリにまとめて配置する。

```
features/paper-convert/components/ConvertForm/
├── index.ts              # re-export
├── ConvertForm.tsx       # コンポーネント本体
├── ConvertForm.test.tsx  # テスト
└── useConvertForm.ts     # コンポーネント固有のフック (必要な場合のみ)
```

- コンポーネント固有のロジック・テスト・型定義がコンポーネントディレクトリの外に散らばっている場合はフラグを立てる
- 2 つ以上の機能で使うフック・コンポーネントは `shared/` に昇格させる
- feature の `index.ts` が公開 API となる。feature 内部のモジュールを直接インポート（深いパスでの参照）している場合はフラグを立てる

### セキュリティ

- API キー・シークレットがコードにハードコードされている場合はフラグを立てる
- ユーザー入力のサニタイズが欠けている場合はフラグを立てる
- CORS 設定が過度に緩い場合 (`*`) はフラグを立てる
- SQL インジェクション・XSS・SSRF の可能性がある場合はフラグを立てる
- `dangerouslySetInnerHTML` の使用はフラグを立てる

### エラーハンドリング

- 外部 API 呼び出し（arXiv API、Google Gemini）でエラーハンドリングが欠けている場合はフラグを立てる
- SSE ストリームの切断・タイムアウトに対するリカバリが考慮されていない場合はフラグを立てる
- ユーザーに内部エラーの詳細（スタックトレースなど）が漏洩する場合はフラグを立てる

### テスト

- 新しい API エンドポイントに対応するテストがない場合はフラグを立てる
- AI エージェントの新規追加・変更に eval テストが含まれていない場合はフラグを立てる
- テスト内でモックが過剰に使われ、実際の動作と乖離している場合はフラグを立てる

### パフォーマンス

- React コンポーネントで不要な再レンダリングを引き起こす実装（useEffect の依存配列の誤り、インラインオブジェクト生成など）はフラグを立てる
- N+1 クエリパターンやループ内での API 呼び出しはフラグを立てる
- 大きなバンドルサイズへの影響がある依存関係の追加はフラグを立てる

### 型安全性

- TypeScript で `any` 型を使用している場合はフラグを立てる（`unknown` を推奨）
- Python で型ヒントが欠けている公開関数はフラグを立てる
- API のリクエスト/レスポンスに Pydantic モデルまたは TypeScript の型定義がない場合はフラグを立てる

---

## Style

- ネストされた条件分岐よりも早期リターンを優先する
- マジックナンバー・マジックストリングは名前付き定数に抽出する
- Python は ruff のフォーマット・リントルールに従う
- TypeScript は ESLint flat config のルールに従う
- コンポーネントの Props は明示的に型定義する（`React.FC` の使用は避け、関数宣言 + Props 型を推奨）
- Python のインポートは標準ライブラリ → サードパーティ → ローカルの順で整理する
- 構造化ログを使用し、f-string によるログメッセージの組み立てを避ける

---

## Skip

- `node_modules/` 配下のファイル
- `*.lock` ファイルのフォーマットのみの変更
- 自動生成されたファイル（`dist/`, `build/`, `.vite/`）
- `infrastructure/cdk/cdk.out/` 配下の CloudFormation テンプレート
- `.github/workflows/` の YAML フォーマットのみの変更
