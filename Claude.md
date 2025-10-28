アセンか# radiko録音管理システム - プロジェクト情報

## プロジェクト概要
radikoの番組表を取得し、録音コマンドを生成するWebアプリケーション。Docker環境で構築し、Ubuntu（クラウド環境）での動作を想定。

## 技術スタック
- **フロントエンド**: HTML/CSS/JavaScript（バニラJS）
- **バックエンド**: Python Flask（プロキシサーバー）
- **Webサーバー**: Nginx
- **コンテナ**: Docker + Docker Compose
- **対象OS**: Ubuntu（クラウド環境）

## ディレクトリ構成
```
radiko-recorder/
├── docker-compose.yml       # Docker Compose設定
├── proxy/                   # Pythonプロキシサーバー
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── web/                     # Nginx Webサーバー
│   ├── Dockerfile
│   ├── nginx.conf
│   └── html/
│       └── index.html
├── README.md               # プロジェクト説明
└── Claude.md              # このファイル（プロジェクト記憶用）
```

## 主要機能
1. **radikoプロキシ**: CORS制限を回避するためのFlaskプロキシサーバー
2. **番組表取得**: 全国47都道府県のラジオ局に対応
3. **録音コマンド生成**:
   - cronコマンド: 毎週定期実行（番組終了5分後）
   - ダウンロードコマンド: 即時実行（放送終了済み番組）
   - at予約コマンド: 未来の番組予約（番組終了5分後）

## 重要な設定値

### プロキシURL
- デフォルト値: `/api`
- Nginx経由でリバースプロキシされる
- `/api/` → `http://proxy:8080/`

### myradikoスクリプトパス
- Ubuntu環境用デフォルト: `/home/ubuntu/myradiko`
- ユーザーの環境に合わせて変更可能

### ポート設定
- Nginx (web): 80番ポート
- Flask (proxy): 8080番ポート

## デプロイ先情報
- 想定デプロイ先: Ubuntu（クラウド環境）
- 推奨デプロイパス: `/home/sites/radiko-recorder`
- 注意: ローカルMac環境では構築のみ、実行はUbuntu上で行う

## セキュリティ考慮事項
1. 外部公開時はHTTPSを推奨（Let's Encrypt使用）
2. Basic認証の追加を推奨
3. ファイアウォール設定（UFW等）

## メンテナンス
- ログ確認: `docker-compose logs -f`
- コンテナ状態: `docker-compose ps`
- 再起動: `docker-compose restart`
- 再ビルド: `docker-compose up -d --build`

## 作成日
2025-10-21

## 備考
- radikoのXMLキャッシュは無効化設定済み
- CORSは完全に許可（flask-cors使用）
- プロキシサーバーはproduction設定

## TODO: 予約情報の永続化（優先度: 高）

### 現在の問題
- 予約情報（cron/at）がDockerコンテナ内のcrontabに保存されている
- `docker-compose down` するとコンテナ削除により予約情報が全て消える
- リリースのたびに予約が消えてしまう

### 解決策: DBに保存 + APSchedulerで実行
1. **データベーステーブル追加**
   ```sql
   CREATE TABLE cron_jobs (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       minute TEXT,
       hour TEXT,
       day_of_month TEXT,
       month TEXT,
       day_of_week TEXT,
       command TEXT,
       title TEXT,
       station TEXT,
       start_time TEXT,
       end_time TEXT,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );

   CREATE TABLE at_jobs (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       job_id TEXT UNIQUE,
       schedule_time TEXT,
       command TEXT,
       title TEXT,
       station TEXT,
       start_time TEXT,
       end_time TEXT,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );
   ```

2. **APSchedulerで予約管理**
   - アプリ起動時にDBから全予約を読み込んでschedulerに登録
   - `/cron/add` → DBに保存 + schedulerに登録
   - `/cron/remove` → DBから削除 + schedulerから削除
   - `/cron/list` → DBから取得
   - 同様に `/at/*` も実装

3. **メリット**
   - `docker-compose down` しても予約情報が保持される
   - コンテナ再起動時に自動復元
   - cronデーモン不要（Pythonのみで完結）
   - 予約の編集・管理が容易

4. **実装ファイル**
   - `proxy/db.py` に予約関連のDB操作関数を追加
   - `proxy/app.py` にAPScheduler設定を追加
   - 既存の `/cron/*`, `/at/*` エンドポイントを書き換え
