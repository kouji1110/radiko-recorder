# radiko録音管理システム構築指示書

## 📋 プロジェクト概要

radikoの番組表を取得し、録音コマンドを生成するWebアプリケーションをDockerで構築する。
クラウド上のUbuntu環境で動作させることを前提とする。

## 🎯 システム要件

### 機能要件
1. radikoのXMLをプロキシ経由で取得
2. 全国47都道府県のラジオ局に対応
3. 3種類の録音コマンドを生成：
   - **cron用コマンド**: 毎週定期実行（番組終了5分後）
   - **即時ダウンロード**: 放送終了済み番組
   - **at予約コマンド**: 未来の番組予約（番組終了5分後）

### 技術スタック
- **フロントエンド**: HTML/CSS/JavaScript（バニラJS）
- **バックエンド**: Python Flask（プロキシサーバー）
- **コンテナ**: Docker + Docker Compose
- **Webサーバー**: Nginx
- **OS**: Ubuntu (クラウド環境)

## 📁 ディレクトリ構成

```
radiko-recorder/
├── docker-compose.yml
├── proxy/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── web/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── html/
│       └── index.html
└── README.md
```

## 📝 各ファイルの詳細仕様

### 1. docker-compose.yml

```yaml
version: '3.8'

services:
  proxy:
    build: ./proxy
    container_name: radiko-proxy
    ports:
      - "8080:8080"
    restart: unless-stopped
    environment:
      - FLASK_ENV=production
    networks:
      - radiko-network

  web:
    build: ./web
    container_name: radiko-web
    ports:
      - "80:80"
    depends_on:
      - proxy
    restart: unless-stopped
    networks:
      - radiko-network

networks:
  radiko-network:
    driver: bridge
```

### 2. proxy/Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
```

### 3. proxy/requirements.txt

```
Flask==3.0.0
flask-cors==4.0.0
requests==2.31.0
gunicorn==21.2.0
```

### 4. proxy/app.py

```python
from flask import Flask, Response
import requests
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/health')
def health():
    """ヘルスチェック"""
    return {'status': 'ok'}, 200

@app.route('/radiko/<path:path>')
def proxy(path):
    """radikoへのリクエストをプロキシする"""
    url = f'http://radiko.jp/{path}'
    logger.info(f'Proxying request to: {url}')
    
    try:
        resp = requests.get(
            url, 
            timeout=30,
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }
        )
        
        return Response(
            resp.content, 
            status=resp.status_code,
            content_type=resp.headers.get('content-type', 'text/xml'),
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        )
    except requests.RequestException as e:
        logger.error(f'Error proxying request: {str(e)}')
        return Response(
            f'Error: {str(e)}', 
            status=500,
            content_type='text/plain'
        )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
```

### 5. web/Dockerfile

```dockerfile
FROM nginx:alpine

# nginxの設定ファイルをコピー
COPY nginx.conf /etc/nginx/nginx.conf

# HTMLファイルをコピー
COPY html /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 6. web/nginx.conf

```nginx
events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    sendfile on;
    keepalive_timeout 65;

    server {
        listen 80;
        server_name _;

        root /usr/share/nginx/html;
        index index.html;

        location / {
            try_files $uri $uri/ /index.html;
        }

        # プロキシサーバーへのリバースプロキシ設定
        location /api/ {
            proxy_pass http://proxy:8080/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # ログ設定
        access_log /var/log/nginx/access.log;
        error_log /var/log/nginx/error.log;
    }
}
```

### 7. web/html/index.html

**既存のHTMLファイルを以下の点を修正して配置:**

1. プロキシURLのデフォルト値を変更:
```javascript
// 修正前
<input type="text" id="proxyUrl" placeholder="例: http://localhost:8080">

// 修正後
<input type="text" id="proxyUrl" value="/api" placeholder="例: /api">
```

2. myradikoスクリプトのパスを環境に合わせて変更可能にする（オプション）

### 8. README.md

```markdown
# radiko録音管理システム

## セットアップ

### 前提条件
- Docker
- Docker Compose

### インストール

1. リポジトリをクローン（または作成）
```bash
mkdir -p radiko-recorder
cd radiko-recorder
```

2. 各ファイルを配置

3. Dockerコンテナをビルド＆起動
```bash
docker-compose up -d --build
```

4. 動作確認
```bash
# プロキシサーバーのヘルスチェック
curl http://localhost:8080/health

# Webサーバーの確認
curl http://localhost
```

5. ブラウザでアクセス
```
http://[サーバーのIPアドレス]
```

## 使い方

1. エリアを選択
2. 「番組表を取得」をクリック
3. 各番組のボタンからコマンドを生成
4. コピーしてターミナルで実行

## コマンド

```bash
# コンテナの起動
docker-compose up -d

# コンテナの停止
docker-compose down

# ログの確認
docker-compose logs -f

# コンテナの再起動
docker-compose restart

# コンテナのビルドし直し
docker-compose up -d --build
```

## トラブルシューティング

### ポートが既に使用されている
```bash
# 使用中のポートを確認
sudo lsof -i :80
sudo lsof -i :8080

# 使用中のプロセスを停止
sudo kill -9 [PID]
```

### コンテナが起動しない
```bash
# ログを確認
docker-compose logs

# コンテナの状態を確認
docker-compose ps
```
```

## 🚀 構築手順

### ステップ1: 環境準備

```bash
# Dockerのインストール確認
docker --version
docker-compose --version

# インストールされていない場合
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl start docker
sudo systemctl enable docker

# 現在のユーザーをdockerグループに追加
sudo usermod -aG docker $USER
# ログアウト・ログインで反映
```

### ステップ2: プロジェクト作成

```bash
# プロジェクトディレクトリを作成
mkdir -p radiko-recorder
cd radiko-recorder

# サブディレクトリを作成
mkdir -p proxy web/html

# 各ファイルを作成（上記の内容で）
```

### ステップ3: ビルドと起動

```bash
# コンテナをビルドして起動
docker-compose up -d --build

# ログを確認
docker-compose logs -f
```

### ステップ4: 動作確認

```bash
# プロキシサーバーの確認
curl http://localhost:8080/health

# radikoデータの取得テスト
curl http://localhost:8080/radiko/v3/program/now/JP13.xml

# Webサーバーの確認
curl http://localhost
```

### ステップ5: ファイアウォール設定（必要に応じて）

```bash
# UFWを使用している場合
sudo ufw allow 80/tcp
sudo ufw allow 8080/tcp
sudo ufw reload
```

## 🔧 カスタマイズポイント

### myradikoスクリプトの設定

**パス設定（Ubuntu環境用に設定済み）:**
- ベースディレクトリ: `/home/sites/radiko-recorder`
- 作業ディレクトリ: `/home/sites/radiko-recorder/work`
- 出力ディレクトリ: `/home/sites/radiko-recorder/output/radio`
- バックアップディレクトリ: `/home/sites/radiko-recorder/backup/Radio`

**環境変数（オプション）:**
メールアドレスとパスワードを環境変数で設定可能:
```bash
export RADIKO_EMAIL='your-email@example.com'
export RADIKO_PASSWORD='your-password'
```

設定しない場合はデフォルト値が使用されます。

**index.htmlのスクリプトパス:**
デフォルト値: `/home/ubuntu/myradiko`
実際の配置場所に合わせて変更してください: `/home/sites/radiko-recorder/script/myradiko`

### ポート番号の変更

docker-compose.ymlで変更:
```yaml
ports:
  - "8080:8080"  # 左側を変更（ホスト側）
  - "80:80"      # 左側を変更（ホスト側）
```

### HTTPSの有効化（推奨）

Let's Encryptを使用する場合:
```bash
# certbotのインストール
sudo apt install certbot python3-certbot-nginx

# 証明書の取得
sudo certbot --nginx -d your-domain.com

# nginx.confに自動で設定が追加される
```

## ✅ テストチェックリスト

- [ ] Dockerコンテナが正常に起動している
- [ ] http://[サーバーIP]/にアクセスできる
- [ ] 番組表が取得できる
- [ ] cronコマンドが生成される
- [ ] ダウンロードコマンドが生成される
- [ ] at予約コマンドが生成される
- [ ] コマンドがクリップボードにコピーできる

## 📊 監視とメンテナンス

### ログの確認
```bash
# すべてのログ
docker-compose logs

# 特定のサービス
docker-compose logs proxy
docker-compose logs web

# リアルタイムでログを追跡
docker-compose logs -f
```

### リソース使用状況
```bash
# コンテナのリソース使用状況
docker stats

# ディスク使用状況
docker system df
```

### 定期メンテナンス
```bash
# 不要なイメージの削除
docker system prune -a

# コンテナの再起動
docker-compose restart
```

## 🔒 セキュリティ考慮事項

1. **外部公開する場合**:
   - HTTPSを必須にする
   - Basic認証を追加する
   - ファイアウォールを設定する

2. **認証の追加例（nginx）**:
```nginx
location / {
    auth_basic "Restricted Access";
    auth_basic_user_file /etc/nginx/.htpasswd;
    try_files $uri $uri/ /index.html;
}
```

3. **htpasswdファイルの作成**:
```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd username
```

## 📝 補足事項

- radikoのXMLは頻繁に更新されるため、キャッシュは無効化している
- プロキシサーバーはCORS制限を回避するために必要
- 本番環境ではgunicornの使用を推奨（production設定）
- ログはDocker経由で確認可能

## 🎯 期待される成果物

1. Docker Composeでワンコマンドでシステムがすべてのサービスが起動
2. ブラウザからアクセスして番組表が表示される
3. 各種コマンドが正常に生成される
4. システムが安定して動作する

## 🔐 ログイン情報

Basic認証が設定されています：

- **ユーザー名**: `radiko`
- **パスワード**: `radiko2025`

※ パスワードを変更する場合は以下のコマンドで新しいハッシュを生成してください：
```bash
docker run --rm httpd:2.4-alpine htpasswd -nbB radiko "新しいパスワード"
```

生成されたハッシュを `web/.htpasswd` ファイルに貼り付けてコンテナを再ビルドしてください。

## 🚀 本番環境デプロイ手順

### 初回デプロイ

```bash
# 1. プロジェクトディレクトリに移動
cd /home/sites/radiko-recorder

# 2. 最新コードを取得
git pull origin master

# 3. 実行権限を付与（重要！）
chmod +x script/myradiko
chmod +x rec_radiko_ts-master/*.sh

# 4. 必要なディレクトリを作成
mkdir -p output/radio data work backup

# 5. ディレクトリの権限を設定
chmod -R 755 output data work backup script rec_radiko_ts-master

# 6. コンテナをビルド＆起動
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# 7. 動作確認
docker-compose ps
docker-compose logs proxy | tail -30

# 8. コンテナ内の権限確認
docker exec radiko-proxy ls -la /app/script/myradiko
docker exec radiko-proxy ls -la /app/rec_radiko_ts-master/rec_radiko_ts.sh
```

### 更新時のデプロイ

```bash
# 1. 最新コードを取得
cd /home/sites/radiko-recorder
git pull origin master

# 2. コンテナを再起動（コードのみの変更の場合）
docker-compose restart

# または、Dockerfileやdocker-compose.ymlが変更された場合
docker-compose down
docker-compose up -d --build

# 3. ログで問題がないか確認
docker-compose logs -f
```

### トラブルシューティング

#### 録音時に「Permission denied」エラーが出る場合

```bash
# ホスト側で実行権限を付与
chmod +x script/myradiko
chmod +x rec_radiko_ts-master/*.sh

# コンテナ内でも確認
docker exec radiko-proxy chmod +x /app/script/myradiko
docker exec radiko-proxy chmod +x /app/rec_radiko_ts-master/*.sh

# コンテナを再起動
docker-compose restart
```

#### ファイル名のエンコーディングエラーが出る場合

全角文字が半角に変換されるようになっています。それでもエラーが出る場合：

```bash
# コンテナ内のロケール設定を確認
docker exec radiko-proxy locale

# 必要に応じてDockerfileにロケール設定を追加
```

#### パスが見つからないエラーが出る場合

```bash
# 環境変数を確認
docker exec radiko-proxy printenv | grep BASE_DIR

# マウントポイントを確認
docker exec radiko-proxy ls -la /app/
docker exec radiko-proxy ls -la /app/script/
docker exec radiko-proxy ls -la /app/output/
```