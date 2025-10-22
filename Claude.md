# radiko録音管理システム - プロジェクト情報

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
