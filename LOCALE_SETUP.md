# ロケール設定ガイド

## エラーメッセージ
```
warning: setlocale: LC_CTYPE: cannot change locale (ja_JP.UTF-8): No such file or directory
エラー: 'utf-8' codec can't decode byte 0x81 in position 61: invalid start byte
```

## 原因
Ubuntu環境に日本語ロケール（ja_JP.UTF-8）がインストールされていない

## 解決方法

### 1. 日本語ロケールのインストール

```bash
# SSH接続
ssh ubuntu@<サーバーのIPアドレス>

# 日本語ロケールをインストール
sudo apt-get update
sudo apt-get install language-pack-ja
sudo locale-gen ja_JP.UTF-8
sudo update-locale LANG=ja_JP.UTF-8

# 確認
locale -a | grep ja
# ja_JP.utf8 が表示されればOK
```

### 2. システムの再起動（推奨）

```bash
sudo reboot
```

再起動後、再度SSH接続してロケールを確認：

```bash
locale
# LANG=ja_JP.UTF-8
# LC_CTYPE="ja_JP.UTF-8"
# などが表示されればOK
```

### 3. Dockerコンテナの再起動

```bash
cd /home/sites/radiko-recorder
docker-compose down
docker-compose up -d
```

### 4. 動作確認

番組を録音してエラーが出ないことを確認

---

## 代替案（日本語ロケールをインストールしない場合）

myradikoスクリプトは自動的に英語UTF-8ロケールにフォールバックするように修正済みです。

ただし、番組タイトルに日本語が含まれる場合、一部の文字が正しく処理されない可能性があります。

**推奨**: 日本語番組を扱う場合は、日本語ロケールのインストールを強く推奨します。

---

## トラブルシューティング

### ロケールが見つからない場合

```bash
# インストール済みロケールを確認
locale -a

# 日本語が無い場合
sudo apt-get install locales
sudo dpkg-reconfigure locales
# メニューで「ja_JP.UTF-8」を選択してスペースキーでチェック
# OKを選択
```

### Dockerコンテナ内のロケール

```bash
# コンテナ内でロケールを確認
docker-compose exec proxy locale -a

# 日本語ロケールがない場合はDockerfileに追加
# proxy/Dockerfile に以下を追加:
# RUN apt-get update && apt-get install -y locales && \
#     locale-gen ja_JP.UTF-8
```

---

## 参考情報

- Ubuntu ロケール設定: https://help.ubuntu.com/community/Locale
- Docker ロケール設定: https://docs.docker.com/samples/library/ubuntu/
