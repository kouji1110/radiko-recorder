#!/bin/bash
# radiko録音管理システム - Ubuntu環境セットアップスクリプト

set -e

echo "=========================================="
echo "radiko録音管理システム セットアップ"
echo "=========================================="
echo ""

# 基本ディレクトリの設定
BASE_DIR="/home/sites/radiko-recorder"

# 必要なディレクトリを作成
echo "[1/4] ディレクトリを作成しています..."
mkdir -p "${BASE_DIR}/work"
mkdir -p "${BASE_DIR}/output/radio"
mkdir -p "${BASE_DIR}/backup/Radio"

echo "✓ 作業ディレクトリ: ${BASE_DIR}/work"
echo "✓ 出力ディレクトリ: ${BASE_DIR}/output/radio"
echo "✓ バックアップディレクトリ: ${BASE_DIR}/backup/Radio"
echo ""

# myradikoスクリプトに実行権限を付与
echo "[2/4] スクリプトに実行権限を付与しています..."
chmod +x "${BASE_DIR}/script/myradiko"
chmod +x "${BASE_DIR}/rec_radiko_ts-master/rec_radiko_ts.sh"
echo "✓ 実行権限を付与しました"
echo ""

# Dockerのインストール確認
echo "[3/4] Dockerのインストールを確認しています..."
if command -v docker &> /dev/null; then
    echo "✓ Docker: $(docker --version)"
else
    echo "⚠ Dockerがインストールされていません"
    echo "  以下のコマンドでインストールしてください:"
    echo "  sudo apt update && sudo apt install -y docker.io docker-compose"
    exit 1
fi

if command -v docker-compose &> /dev/null; then
    echo "✓ Docker Compose: $(docker-compose --version)"
else
    echo "⚠ Docker Composeがインストールされていません"
    echo "  以下のコマンドでインストールしてください:"
    echo "  sudo apt update && sudo apt install -y docker-compose"
    exit 1
fi
echo ""

# ffmpegのインストール確認
echo "[4/4] ffmpegのインストールを確認しています..."
if command -v ffmpeg &> /dev/null; then
    echo "✓ ffmpeg: $(ffmpeg -version | head -n 1)"
else
    echo "⚠ ffmpegがインストールされていません"
    echo "  以下のコマンドでインストールしてください:"
    echo "  sudo apt update && sudo apt install -y ffmpeg"
    exit 1
fi
echo ""

echo "=========================================="
echo "セットアップが完了しました！"
echo "=========================================="
echo ""
echo "次のステップ:"
echo "1. 環境変数を設定（オプション）:"
echo "   export RADIKO_EMAIL='your-email@example.com'"
echo "   export RADIKO_PASSWORD='your-password'"
echo ""
echo "2. Dockerコンテナを起動:"
echo "   cd ${BASE_DIR}"
echo "   docker-compose up -d --build"
echo ""
echo "3. ブラウザでアクセス:"
echo "   http://[サーバーIP]"
echo ""
