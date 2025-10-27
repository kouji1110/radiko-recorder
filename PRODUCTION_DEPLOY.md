# 本番環境デプロイ手順（完全版）

## 前提条件
- Ubuntu サーバー（クラウド環境）
- Docker と Docker Compose がインストール済み
- Git がインストール済み
- SSH接続可能

---

## 1. 日本語ロケールの設定（初回のみ）

```bash
# SSH接続
ssh ubuntu@<サーバーのIPアドレス>

# 日本語ロケールをインストール
sudo apt-get update
sudo apt-get install -y language-pack-ja
sudo locale-gen ja_JP.UTF-8
sudo update-locale LANG=ja_JP.UTF-8

# 確認
locale -a | grep ja
# 出力例: ja_JP.utf8

# システム再起動（ロケール設定を反映）
sudo reboot
```

再起動後、再度SSH接続してロケールを確認：
```bash
locale
# LANG=ja_JP.UTF-8 が設定されていることを確認
```

---

## 2. プロジェクトのクローン（初回のみ）

```bash
# プロジェクトディレクトリ作成
sudo mkdir -p /home/sites
cd /home/sites

# Gitリポジトリをクローン
sudo git clone <リポジトリURL> radiko-recorder
cd radiko-recorder

# 所有者を変更
sudo chown -R ubuntu:ubuntu /home/sites/radiko-recorder
```

---

## 3. 必要なディレクトリの作成と権限設定

```bash
cd /home/sites/radiko-recorder

# データディレクトリ作成
mkdir -p data
mkdir -p output/radio
mkdir -p work
mkdir -p backup/Radio

# 権限設定
chmod 755 data
chmod 755 output
chmod 755 work
chmod 755 backup

# 確認
ls -la
```

---

## 4. 環境設定ファイルの作成

```bash
# radikoアカウント情報を環境変数として設定（必要に応じて）
cat > .env <<'EOF'
RADIKO_EMAIL=your-email@example.com
RADIKO_PASSWORD=your-password
EOF

# パーミッション設定
chmod 600 .env
```

---

## 5. Docker環境の構築

```bash
# イメージのビルド
docker-compose build --no-cache

# コンテナ起動
docker-compose up -d

# 起動確認
docker-compose ps
# proxy と web が Up になっていることを確認

# ログ確認
docker-compose logs -f
# Ctrl+C で終了
```

---

## 6. データベースの初期化確認

```bash
# DBファイルが作成されているか確認
ls -la data/programs.db

# サイズが0でないことを確認
du -h data/programs.db

# DB内を確認（オプション）
docker-compose exec proxy python3 -c "
import sqlite3
conn = sqlite3.connect('/home/sites/radiko-recorder/data/programs.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM programs')
print('Programs count:', cursor.fetchone()[0])
conn.close()
"
```

---

## 7. Basic認証の設定（初回のみ）

```bash
# .htpasswdファイルが含まれているか確認
ls -la web/.htpasswd

# ユーザー名とパスワードは:
# ユーザー名: radiko
# パスワード: radiko2025
```

---

## 8. ファイアウォール設定（必要に応じて）

```bash
# UFWが有効か確認
sudo ufw status

# 80番ポートを開放
sudo ufw allow 80/tcp

# SSH（22番）も必要
sudo ufw allow 22/tcp

# 有効化
sudo ufw enable
```

---

## 9. 動作確認

ブラウザで以下にアクセス：
```
http://<サーバーのIPアドレス>
```

Basic認証のプロンプトが表示されたら：
- ユーザー名: `radiko`
- パスワード: `radiko2025`

確認項目：
- [ ] 番組検索が動作する
- [ ] 番組表更新が動作する
- [ ] ダウンロード（DL）が動作する
- [ ] at予約が動作する
- [ ] cron登録が動作する
- [ ] 録音済みファイルの再生が動作する

---

## 10. 番組表の初回更新

1. 「番組表更新」タブを開く
2. 「番組表を今すぐ更新」ボタンをクリック
3. 進捗を確認（全47都道府県のデータ取得に10〜20分程度）
4. 完了まで待つ

---

## 更新手順（2回目以降）

### A. 最新コードの取得

```bash
cd /home/sites/radiko-recorder
git pull origin master
```

### B. コンテナの再ビルド・再起動

```bash
# 停止
docker-compose down

# 再ビルド
docker-compose build --no-cache

# 起動
docker-compose up -d

# ログ確認
docker-compose logs -f proxy
docker-compose logs -f web
```

### C. 動作確認

ブラウザで各機能が正常に動作することを確認

---

## トラブルシューティング

### 1. ロケールエラーが出る場合

```bash
# エラー例:
# warning: setlocale: LC_CTYPE: cannot change locale (ja_JP.UTF-8)

# 対処法: LOCALE_SETUP.md を参照
sudo apt-get install language-pack-ja
sudo locale-gen ja_JP.UTF-8
sudo reboot
```

### 2. データベースエラーが出る場合

```bash
# 権限確認
ls -la /home/sites/radiko-recorder/data/

# 権限修正
sudo chown -R ubuntu:ubuntu /home/sites/radiko-recorder/data
chmod 755 /home/sites/radiko-recorder/data

# コンテナ再起動
docker-compose restart proxy
```

### 3. ファイルが保存できない場合

```bash
# outputディレクトリの権限確認
ls -la /home/sites/radiko-recorder/output/

# 権限修正
sudo chown -R ubuntu:ubuntu /home/sites/radiko-recorder/output
chmod -R 755 /home/sites/radiko-recorder/output

# コンテナ再起動
docker-compose restart
```

### 4. ポートが使えない場合

```bash
# 80番ポートを使用中のプロセスを確認
sudo lsof -i :80

# システムのnginxが起動している場合
sudo systemctl stop nginx
sudo systemctl disable nginx

# Dockerコンテナを再起動
docker-compose restart web
```

### 5. コンテナが起動しない場合

```bash
# ログを詳しく確認
docker-compose logs proxy
docker-compose logs web

# コンテナを完全削除して再構築
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

---

## ロールバック手順

問題が発生した場合、前のバージョンに戻す：

```bash
# コミット履歴を確認
git log --oneline -5

# 前のコミットに戻す
git checkout <前のコミットID>

# コンテナを再ビルド
docker-compose down
docker-compose up -d --build
```

---

## メンテナンスコマンド

```bash
# コンテナの状態確認
docker-compose ps

# ログ確認
docker-compose logs -f

# コンテナ再起動
docker-compose restart

# ディスク使用量確認
df -h

# データベースサイズ確認
du -h /home/sites/radiko-recorder/data/programs.db

# 録音ファイル一覧
ls -lh /home/sites/radiko-recorder/output/radio/
```

---

## セキュリティ推奨事項

1. **HTTPS化（Let's Encrypt）**
   ```bash
   sudo apt-get install certbot
   # Nginx用のcertbotプラグインを使用
   ```

2. **Basic認証のパスワード変更**
   ```bash
   cd /home/sites/radiko-recorder/web
   htpasswd -c .htpasswd radiko
   # 新しいパスワードを入力
   docker-compose restart web
   ```

3. **ファイアウォール設定**
   - 必要なポート（22, 80, 443）のみ開放
   - 不要なポートは閉じる

4. **定期バックアップ**
   ```bash
   # データベースのバックアップ
   cp /home/sites/radiko-recorder/data/programs.db ~/backup/

   # 録音ファイルのバックアップ
   rsync -av /home/sites/radiko-recorder/output/ ~/backup/output/
   ```

---

## サポート

問題が発生した場合：
1. ログを確認（`docker-compose logs`）
2. トラブルシューティングセクションを参照
3. GitHubのIssuesを確認
