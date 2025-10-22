# Ubuntu環境へのデプロイ手順

## 📋 前提条件

- Ubuntu サーバー（クラウド環境）
- SSH接続可能
- sudo権限あり

---

## 🚀 デプロイ手順

### ステップ1: ファイルをUbuntuサーバーへ転送

**ローカル（Mac）から実行:**

```bash
# プロジェクトディレクトリに移動
cd /Users/kojima/Documents/src/radiko-recorder

# rsyncでファイルを転送（推奨）
rsync -avz --exclude='.DS_Store' --exclude='.claude' \
  ./ ubuntu@[サーバーIP]:/home/sites/radiko-recorder/

# または scpを使用
scp -r * ubuntu@[サーバーIP]:/home/sites/radiko-recorder/
```

**注意:** `[サーバーIP]` は実際のサーバーIPアドレスに置き換えてください

---

### ステップ2: Ubuntu側で必要なパッケージをインストール

**Ubuntu側で実行:**

```bash
# SSH接続
ssh ubuntu@[サーバーIP]

# パッケージリストを更新
sudo apt update

# Docker, Docker Compose, ffmpegをインストール
sudo apt install -y docker.io docker-compose ffmpeg

# Dockerサービスを起動・自動起動設定
sudo systemctl start docker
sudo systemctl enable docker

# 現在のユーザーをdockerグループに追加
sudo usermod -aG docker $USER

# グループ変更を即座に反映
newgrp docker

# インストール確認
docker --version
docker-compose --version
ffmpeg -version
```

---

### ステップ3: セットアップスクリプトを実行

**Ubuntu側で実行:**

```bash
# プロジェクトディレクトリに移動
cd /home/sites/radiko-recorder

# セットアップスクリプトに実行権限を付与
chmod +x setup.sh

# セットアップスクリプトを実行
./setup.sh
```

このスクリプトは以下を実行します:
- 必要なディレクトリを作成（work, output, backup）
- スクリプトファイルに実行権限を付与
- Docker, Docker Compose, ffmpegのインストール確認

---

### ステップ4: 環境変数を設定（オプション）

**Ubuntu側で実行:**

メールアドレスとパスワードを環境変数で設定する場合:

```bash
# .bashrcに追記
echo 'export RADIKO_EMAIL="your-email@example.com"' >> ~/.bashrc
echo 'export RADIKO_PASSWORD="your-password"' >> ~/.bashrc

# 即座に反映
source ~/.bashrc

# 確認
echo $RADIKO_EMAIL
echo $RADIKO_PASSWORD
```

**設定しない場合:** スクリプト内のデフォルト値が使用されます

---

### ステップ5: Dockerコンテナをビルド＆起動

**Ubuntu側で実行:**

```bash
cd /home/sites/radiko-recorder

# コンテナをビルド＆バックグラウンドで起動
docker-compose up -d --build

# ログを確認（起動状況チェック）
docker-compose logs -f

# Ctrl+C でログ表示を終了
```

---

### ステップ6: 動作確認

**Ubuntu側で実行:**

```bash
# プロキシサーバーのヘルスチェック
curl http://localhost:8080/health
# 期待される結果: {"status":"ok"}

# radikoデータ取得テスト（東京都の番組表）
curl http://localhost:8080/radiko/v3/program/now/JP13.xml
# XMLデータが返ってくることを確認

# Webサーバーの確認
curl http://localhost
# HTMLが返ってくることを確認

# コンテナの状態確認
docker-compose ps
# radiko-proxy と radiko-web の両方が Up になっていることを確認
```

---

### ステップ7: ファイアウォール設定（必要に応じて）

**Ubuntu側で実行:**

```bash
# UFWのステータス確認
sudo ufw status

# UFWが有効な場合、HTTP/HTTPSポートを開放
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8080/tcp

# 設定を反映
sudo ufw reload

# 確認
sudo ufw status
```

---

### ステップ8: ブラウザからアクセス

ブラウザで以下のURLにアクセス:

```
http://[サーバーIP]
```

以下が表示されればデプロイ成功:
- radiko録音管理システムのUI
- エリア選択ドロップダウン
- プロキシURL: `/api`（デフォルト設定済み）
- myradikoスクリプトパス: `/home/sites/radiko-recorder/script/myradiko`

---

## 🔧 動作テスト

### 番組表取得のテスト

1. ブラウザでアクセス
2. エリアを選択（例: 東京都）
3. 「番組表を取得」ボタンをクリック
4. 番組一覧が表示されることを確認
5. 各番組のボタン（cron/ダウンロード/at予約）をクリック
6. コマンドが生成されることを確認

### myradikoスクリプトのテスト

**Ubuntu側で実行:**

```bash
# テスト実行（例: TBSラジオの番組を録音）
/home/sites/radiko-recorder/script/myradiko \
  "テスト番組" \
  "test-rss" \
  "TBS" \
  "20251021010000" \
  "20251021013000" \
  "" \
  "" \
  ""

# 作業ディレクトリを確認
ls -la /home/sites/radiko-recorder/work/test-rss/

# 出力ディレクトリを確認
ls -la /home/sites/radiko-recorder/output/radio/test-rss/
```

---

## 🛠 よく使うコマンド

```bash
# コンテナの起動
docker-compose up -d

# コンテナの停止
docker-compose down

# コンテナの再起動
docker-compose restart

# ログの確認（リアルタイム）
docker-compose logs -f

# 特定サービスのログ
docker-compose logs proxy
docker-compose logs web

# コンテナの状態確認
docker-compose ps

# コンテナの再ビルド
docker-compose up -d --build

# ディスク使用状況の確認
du -sh /home/sites/radiko-recorder/*
```

---

## 🔒 セキュリティ強化（推奨）

### HTTPS対応（Let's Encrypt）

```bash
# certbotのインストール
sudo apt install certbot python3-certbot-nginx

# ドメインを持っている場合
sudo certbot --nginx -d your-domain.com

# 自動更新の設定
sudo systemctl enable certbot.timer
```

### Basic認証の追加

```bash
# htpasswdツールのインストール
sudo apt install apache2-utils

# パスワードファイルの作成
sudo htpasswd -c /etc/nginx/.htpasswd username

# nginx.confを編集（web/nginx.confに追加）
# location / {
#     auth_basic "Restricted Access";
#     auth_basic_user_file /etc/nginx/.htpasswd;
#     try_files $uri $uri/ /index.html;
# }

# 再ビルド
docker-compose up -d --build
```

---

## 🐛 トラブルシューティング

### ポート80が使用中

```bash
# 使用中のプロセスを確認
sudo lsof -i :80

# Apacheなどが起動している場合は停止
sudo systemctl stop apache2
sudo systemctl disable apache2
```

### コンテナが起動しない

```bash
# ログを確認
docker-compose logs

# コンテナの状態を詳しく確認
docker-compose ps -a
docker inspect radiko-proxy
docker inspect radiko-web
```

### ディレクトリのパーミッション問題

```bash
# 所有者を変更
sudo chown -R ubuntu:ubuntu /home/sites/radiko-recorder

# 権限を確認
ls -la /home/sites/radiko-recorder
```

### ffmpegが見つからない

```bash
# ffmpegのインストール
sudo apt update
sudo apt install -y ffmpeg

# パスを確認
which ffmpeg
```

---

## 📊 ディレクトリ構成（デプロイ後）

```
/home/sites/radiko-recorder/
├── docker-compose.yml
├── setup.sh
├── DEPLOY.md                       # このファイル
├── README.md
├── Claude.md
├── proxy/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── web/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── html/
│       └── index.html
├── script/
│   └── myradiko                    # 録音ラッパースクリプト
├── rec_radiko_ts-master/
│   ├── rec_radiko_ts.sh           # 実際の録音スクリプト
│   └── ...
├── work/                           # 作業ディレクトリ（録音中の一時ファイル）
├── output/                         # 最終出力ディレクトリ
│   └── radio/
│       └── [RSS名]/
│           └── *.mp3
└── backup/                         # バックアップディレクトリ
    └── Radio/
        └── *.mp3
```

---

## ✅ デプロイ完了チェックリスト

- [ ] ファイルがUbuntuサーバーに転送された
- [ ] Docker, Docker Compose, ffmpegがインストールされた
- [ ] setup.shが正常に実行された
- [ ] 必要なディレクトリ（work, output, backup）が作成された
- [ ] docker-compose up -d --buildが成功した
- [ ] curl http://localhost:8080/health が成功した
- [ ] ブラウザからアクセスできた
- [ ] 番組表が取得できた
- [ ] コマンドが生成できた
- [ ] ファイアウォール設定が完了した（必要に応じて）

---

## 📞 サポート

問題が発生した場合は、以下を確認してください:

1. ログファイル: `docker-compose logs -f`
2. コンテナの状態: `docker-compose ps`
3. ディスク容量: `df -h`
4. パーミッション: `ls -la /home/sites/radiko-recorder`

それでも解決しない場合は、エラーメッセージとログを確認してください。
