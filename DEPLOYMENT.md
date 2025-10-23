# radiko録音管理システム デプロイ手順書

## 📋 目次

1. [初回デプロイ手順](#初回デプロイ手順)
2. [更新・リリース手順](#更新リリース手順)
3. [トラブルシューティング](#トラブルシューティング)
4. [メンテナンスコマンド](#メンテナンスコマンド)

---

## 初回デプロイ手順

### 前提条件

- Ubuntu 24.04 LTS
- Docker & Docker Compose インストール済み
- ドメイン名（例: radiko.degucha.com）
- SSH接続可能

### ステップ1: 環境確認

```bash
# OSバージョン確認
cat /etc/os-release

# Dockerがインストールされているか確認
docker --version
docker-compose --version

# 使用中のポート確認
sudo lsof -i :80
sudo lsof -i :8080

# 現在のユーザー名
whoami
```

### ステップ2: ディレクトリ作成

```bash
# プロジェクトディレクトリを作成
mkdir -p /home/sites/radiko-recorder
cd /home/sites/radiko-recorder

# 必要なサブディレクトリを作成
mkdir -p work output backup script rec_radiko_ts-master

# 権限を確認
ls -la /home/sites/radiko-recorder
```

### ステップ3: ポート番号の調整

既存のNginxやサービスとポート競合する場合は、`docker-compose.yml` のポート番号を変更：

```yaml
# docker-compose.yml
services:
  proxy:
    ports:
      - "8089:8080"  # 左側を空いているポート番号に変更

  web:
    ports:
      - "8088:80"    # 左側を空いているポート番号に変更
```

### ステップ4: ファイルのアップロード

FTPクライアント（FileZilla、Cyberduck等）またはSCPで以下をアップロード：

```
ローカル → Ubuntu環境 (/home/sites/radiko-recorder/)

├── docker-compose.yml
├── proxy/
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
├── web/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── .htpasswd
│   └── html/
│       ├── index.html
│       └── img/
│           ├── favicon.ico
│           └── radiko.png
├── script/
│   └── myradiko
└── rec_radiko_ts-master/
    └── (rec_radiko_tsスクリプト一式)
```

### ステップ5: スクリプトに実行権限を付与

```bash
# myradikoスクリプトに実行権限
chmod +x /home/sites/radiko-recorder/script/myradiko

# rec_radiko_tsスクリプトに実行権限
chmod +x /home/sites/radiko-recorder/rec_radiko_ts-master/rec_radiko_ts
```

### ステップ6: Dockerコンテナのビルド＆起動

```bash
# プロジェクトディレクトリに移動
cd /home/sites/radiko-recorder

# Dockerコンテナをビルド＆起動
docker-compose up -d --build

# コンテナの起動確認
docker-compose ps

# ログ確認（エラーがないかチェック）
docker-compose logs
```

### ステップ7: DNS設定

DNSプロバイダーのコントロールパネルで以下を追加：

- **タイプ**: A レコード
- **名前**: radiko（または任意のサブドメイン）
- **値**: サーバーのグローバルIPアドレス
- **TTL**: 3600（1時間）

**サーバーIPアドレスの確認:**
```bash
curl ifconfig.me
```

### ステップ8: Nginxリバースプロキシ設定

#### 8-1. 設定ファイルを作成

```bash
sudo nano /etc/nginx/sites-available/radiko.degucha.com
```

以下を貼り付け：

```nginx
server {
    listen 80;
    server_name radiko.degucha.com;

    # ログ設定
    access_log /var/log/nginx/radiko.degucha.com.access.log;
    error_log /var/log/nginx/radiko.degucha.com.error.log;

    # リバースプロキシ設定
    location / {
        proxy_pass http://localhost:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # タイムアウト設定（録音処理が長時間かかる可能性があるため）
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
        proxy_read_timeout 300;
        send_timeout 300;

        # WebSocket対応（SSEストリーミングログ用）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # セキュリティヘッダー
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
}
```

保存: `Ctrl + O`, `Enter`, `Ctrl + X`

#### 8-2. 設定を有効化

```bash
# シンボリックリンクを作成
sudo ln -s /etc/nginx/sites-available/radiko.degucha.com /etc/nginx/sites-enabled/radiko.degucha.com

# 設定ファイルの確認
ls -la /etc/nginx/sites-available/radiko.degucha.com
ls -la /etc/nginx/sites-enabled/ | grep radiko

# Nginx設定のテスト
sudo nginx -t

# Nginxを再起動
sudo systemctl reload nginx

# ステータス確認
sudo systemctl status nginx
```

### ステップ9: 動作確認

ブラウザで以下にアクセス：

**http://radiko.degucha.com**

Basic認証が表示される：
- **ユーザー名**: `radiko`
- **パスワード**: `radiko2025`

#### 確認項目

1. ✅ ログイン画面が表示される
2. ✅ エリア選択ができる
3. ✅ 「番組表を更新」ボタンで番組一覧が取得できる
4. ✅ 番組カードが表示される
5. ✅ 各種コマンド生成ボタンが動作する

---

## 更新・リリース手順

### ケース1: HTMLやCSSの修正のみ（フロントエンド更新）

```bash
# ローカルで修正後、コミット
git add web/html/index.html
git commit -m "Fix: ..."

# Ubuntu環境へアップロード
# FTPクライアントで web/html/index.html を
# /home/sites/radiko-recorder/web/html/ にアップロード

# Dockerコンテナを再起動（変更を反映）
cd /home/sites/radiko-recorder
docker-compose restart web

# ログ確認
docker-compose logs web

# ブラウザでキャッシュクリア後、アクセスして確認
# Ctrl + Shift + R (ハードリロード)
```

### ケース2: Pythonコード（proxy/app.py）の修正

```bash
# ローカルで修正後、コミット
git add proxy/app.py
git commit -m "Fix: ..."

# Ubuntu環境へアップロード
# FTPクライアントで proxy/app.py を
# /home/sites/radiko-recorder/proxy/ にアップロード

# Dockerコンテナを再起動
cd /home/sites/radiko-recorder
docker-compose restart proxy

# ログ確認
docker-compose logs proxy

# 動作確認
curl http://localhost:8089/health
```

### ケース3: Dockerfile やパッケージの変更

```bash
# ローカルで修正後、コミット
git add proxy/Dockerfile proxy/requirements.txt
git commit -m "Update: ..."

# Ubuntu環境へアップロード
# 変更したファイルをアップロード

# Dockerイメージを再ビルド
cd /home/sites/radiko-recorder
docker-compose down
docker-compose up -d --build

# コンテナ起動確認
docker-compose ps

# ログ確認
docker-compose logs
```

### ケース4: docker-compose.yml の変更

```bash
# ローカルで修正後、コミット
git add docker-compose.yml
git commit -m "Update: ..."

# Ubuntu環境へアップロード
# docker-compose.yml をアップロード

# コンテナを再作成
cd /home/sites/radiko-recorder
docker-compose down
docker-compose up -d

# コンテナ起動確認
docker-compose ps
```

### ケース5: Nginx設定の変更

```bash
# Ubuntu環境で直接編集
sudo nano /etc/nginx/sites-available/radiko.degucha.com

# または、ローカルで編集してアップロード後に配置

# 設定テスト
sudo nginx -t

# Nginxを再起動
sudo systemctl reload nginx

# ステータス確認
sudo systemctl status nginx
```

### リリースチェックリスト

- [ ] ローカル環境で動作確認済み
- [ ] 変更内容をGitコミット済み
- [ ] バックアップ取得（必要に応じて）
- [ ] Ubuntu環境へファイルアップロード
- [ ] Dockerコンテナ再起動 or 再ビルド
- [ ] エラーログの確認
- [ ] ブラウザで動作確認
- [ ] 主要機能のテスト

---

## トラブルシューティング

### コンテナが起動しない

```bash
# ログを確認
docker-compose logs

# 特定のコンテナのログを確認
docker-compose logs proxy
docker-compose logs web

# コンテナの状態確認
docker-compose ps

# コンテナを完全に削除して再作成
docker-compose down
docker-compose up -d --build
```

### ブラウザでアクセスできない

```bash
# ポートがリッスンされているか確認
sudo lsof -i :8088
sudo lsof -i :8089

# ローカルからアクセステスト
curl -I http://localhost:8088

# Nginx設定テスト
sudo nginx -t

# Nginxログ確認
sudo tail -f /var/log/nginx/radiko.degucha.com.error.log
sudo tail -f /var/log/nginx/radiko.degucha.com.access.log
```

### 番組表が取得できない

```bash
# proxyコンテナのログ確認
docker-compose logs proxy

# proxyコンテナ内に入って確認
docker exec -it radiko-proxy bash

# radikoへの接続テスト
curl -I https://radiko.jp

# DNSが解決できるか確認
nslookup radiko.jp
```

### 録音が実行されない

```bash
# proxyコンテナ内に入る
docker exec -it radiko-proxy bash

# cronジョブ確認
crontab -l

# atジョブ確認
atq

# スクリプトの実行権限確認
ls -la /home/sites/radiko-recorder/script/myradiko
ls -la /home/sites/radiko-recorder/rec_radiko_ts-master/rec_radiko_ts

# 手動でスクリプト実行テスト
/home/sites/radiko-recorder/script/myradiko "テスト" "TBS" "TBS" "202510231500" "202510231530"
```

### ディスク容量不足

```bash
# ディスク使用状況確認
df -h

# 録音ファイルの容量確認
du -sh /home/sites/radiko-recorder/output

# 古いファイルを削除
find /home/sites/radiko-recorder/output -type f -mtime +30 -delete

# Dockerの不要なイメージ削除
docker system prune -a
```

---

## メンテナンスコマンド

### Docker関連

```bash
# コンテナの状態確認
docker-compose ps

# ログ確認（リアルタイム）
docker-compose logs -f

# 特定のコンテナのログ
docker-compose logs -f proxy
docker-compose logs -f web

# コンテナ再起動
docker-compose restart

# 特定のコンテナを再起動
docker-compose restart proxy
docker-compose restart web

# コンテナ停止
docker-compose down

# コンテナ起動
docker-compose up -d

# コンテナ再ビルド＆起動
docker-compose up -d --build

# コンテナ内に入る
docker exec -it radiko-proxy bash
docker exec -it radiko-web sh
```

### ログ確認

```bash
# Nginxログ
sudo tail -f /var/log/nginx/radiko.degucha.com.access.log
sudo tail -f /var/log/nginx/radiko.degucha.com.error.log

# Dockerログ
docker-compose logs --tail=100 proxy
docker-compose logs --tail=100 web

# システムログ
sudo journalctl -u nginx -f
```

### バックアップ

```bash
# 録音ファイルのバックアップ
tar -czf radiko-backup-$(date +%Y%m%d).tar.gz /home/sites/radiko-recorder/output

# 設定ファイルのバックアップ
cp /etc/nginx/sites-available/radiko.degucha.com ~/radiko-nginx-backup.conf
cp /home/sites/radiko-recorder/docker-compose.yml ~/docker-compose-backup.yml

# データベース（将来的に追加する場合）
# docker exec radiko-db pg_dump -U user dbname > backup.sql
```

### ファイル管理

```bash
# 録音済みファイル一覧
ls -lah /home/sites/radiko-recorder/output

# ディスク使用量確認
du -sh /home/sites/radiko-recorder/*

# 古いファイルを検索（30日以上前）
find /home/sites/radiko-recorder/output -type f -mtime +30

# 古いファイルを削除
find /home/sites/radiko-recorder/output -type f -mtime +30 -delete
```

### セキュリティ

```bash
# Basic認証のパスワード変更
# 新しいパスワードのハッシュを生成
docker run --rm httpd:2.4-alpine htpasswd -nbB radiko "新しいパスワード"

# .htpasswdファイルを更新
sudo nano /home/sites/radiko-recorder/web/.htpasswd
# 生成されたハッシュを貼り付け

# webコンテナを再起動
docker-compose restart web
```

### パフォーマンス監視

```bash
# Dockerコンテナのリソース使用状況
docker stats

# システム全体のリソース確認
htop
# または
top

# ディスクI/O確認
iostat -x 1

# ネットワーク確認
netstat -tuln | grep 8088
```

---

## 補足情報

### 本番環境の構成

```
インターネット
    ↓
Nginx (ポート80) ← リバースプロキシ
    ↓
radiko-web コンテナ (ポート8088)
    ↓
radiko-proxy コンテナ (ポート8089)
    ↓
radiko.jp API
```

### 使用ポート

- **80**: Nginx (UbuntuのNginx)
- **8088**: radiko-web コンテナ (Docker内部でポート80)
- **8089**: radiko-proxy コンテナ (Docker内部でポート8080)

### ディレクトリ構成

```
/home/sites/radiko-recorder/
├── docker-compose.yml       # Docker構成ファイル
├── proxy/                   # Flaskアプリケーション
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
├── web/                     # Nginx + フロントエンド
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── .htpasswd
│   └── html/
│       ├── index.html
│       └── img/
├── script/                  # 録音スクリプト
│   └── myradiko
├── rec_radiko_ts-master/   # rec_radiko_tsスクリプト
├── work/                    # 作業用ディレクトリ
├── output/                  # 録音ファイル出力先
└── backup/                  # バックアップ先
```

### 重要なファイル

- `/etc/nginx/sites-available/radiko.degucha.com` - Nginx設定
- `/home/sites/radiko-recorder/docker-compose.yml` - Docker構成
- `/home/sites/radiko-recorder/web/html/index.html` - フロントエンド
- `/home/sites/radiko-recorder/proxy/app.py` - バックエンドAPI

---

## 連絡先・参考リンク

- プロジェクトリポジトリ: (GitHubのURLなど)
- radiko公式: https://radiko.jp
- Docker公式ドキュメント: https://docs.docker.com
- Nginx公式ドキュメント: https://nginx.org/en/docs/
