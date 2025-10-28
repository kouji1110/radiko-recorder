# radiko録音管理システム - デプロイ手順

## 前提条件

- Ubuntu環境（クラウドサーバー等）
- Docker & Docker Composeがインストール済み
- ポート8088（Web）と8089（API）が利用可能

## デプロイ手順

### 1. リポジトリをクローン

```bash
cd /home/sites
git clone <repository-url> radiko-recorder
cd radiko-recorder
```

### 2. myradikoスクリプトを配置

```bash
# myradikoスクリプトを配置（パスは環境に合わせて変更）
# デフォルト: /home/ubuntu/myradiko
```

### 3. コンテナをビルド＆起動

```bash
docker-compose up -d --build
```

### 4. 起動確認

```bash
# コンテナ状態確認
docker-compose ps

# ログ確認
docker-compose logs -f proxy

# ヘルスチェック
curl http://localhost:8089/health
```

### 5. アクセス

```
Web UI: http://<server-ip>:8088
```

**Basic認証**:
- ユーザー名: `radiko`
- パスワード: `radiko2025`

## 更新手順

```bash
cd /home/sites/radiko-recorder

# 最新コードを取得
git pull

# コンテナ再ビルド＆再起動
docker-compose down
docker-compose up -d --build

# 確認
docker-compose logs -f proxy
```

## データバックアップ

重要なデータは`data/`ディレクトリに保存されます：

```bash
# バックアップ
tar czf radiko-backup-$(date +%Y%m%d).tar.gz data/

# リストア
tar xzf radiko-backup-YYYYMMDD.tar.gz
```

## トラブルシューティング

### コンテナが起動しない

```bash
docker-compose logs proxy
docker-compose logs web
```

### データベースエラー

```bash
# DBを初期化（予約データは削除されます）
rm data/programs.db
docker-compose restart proxy
```

### ポート競合

```bash
# ポート使用状況確認
lsof -i :8088
lsof -i :8089
```

## その他

- 番組表は毎日03:00に自動更新されます
- 録音ファイルは`output/radio/`に保存されます
- ログは`docker-compose logs`で確認できます
- 予約データはコンテナ再起動後も保持されます
