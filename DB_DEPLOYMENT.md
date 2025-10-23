# 番組表DB化デプロイ手順

サーバー側DBを使った全エリア検索機能のデプロイ手順です。

## 概要

**変更内容：**
- クライアント側IndexedDBからサーバー側SQLiteに変更
- 30分ごとに自動で全エリア×15日分の番組表を取得・保存
- 全エリア検索がサーバー側DB検索になり爆速化

**メリット：**
- ✅ ユーザー側の負荷ゼロ（ブラウザストレージ不使用）
- ✅ 検索が超高速（SQLiteの全文検索）
- ✅ 全ユーザーでデータ共有
- ✅ デバイス間で同期不要

---

## 1. サーバー側のデプロイ

### 1-1. 新しいファイルをアップロード

FTPクライアントで以下のファイルをアップロード：

```
radiko-recorder/proxy/
├── db.py (新規)
├── fetch_programs.py (新規)
├── app.py (更新)
└── requirements.txt (更新)
```

### 1-2. データディレクトリを作成

```bash
ssh [your-server]
mkdir -p /home/sites/radiko-recorder/data
chmod 755 /home/sites/radiko-recorder/data
```

### 1-3. Pythonパッケージをインストール

```bash
cd /home/sites/radiko-recorder/proxy
pip3 install -r requirements.txt
```

### 1-4. Dockerコンテナを再起動

```bash
cd /home/sites/radiko-recorder
docker-compose down
docker-compose up -d --build
```

### 1-5. ログを確認

```bash
docker-compose logs -f proxy
```

以下のログが表示されればOK：
```
✅ Database initialized: /home/sites/radiko-recorder/data/programs.db
✅ Scheduler started: updating programs every 30 minutes
```

---

## 2. 初回データ取得（重要）

初回起動時はDBが空なので、手動で番組表を取得します。

### 方法1: APIで即時更新をトリガー（推奨）

```bash
curl -X POST http://localhost:8089/api/programs/update/trigger
```

### 方法2: Dockerコンテナ内で直接実行

```bash
docker exec -it radiko-proxy bash
cd /app
python3 fetch_programs.py
exit
```

**所要時間：約10〜20分**
- 47エリア × 15日分 × 各エリア5〜10局 = 数千件の番組データ取得

### 進捗確認

別ターミナルでログを監視：
```bash
docker-compose logs -f proxy
```

以下のようなログが流れます：
```
[1/47] Processing JP1...
  ✅ JP1 20250116: 120 programs
[2/47] Processing JP2...
  ✅ JP2 20250116: 95 programs
...
Update completed in 850.3 seconds
Total programs: 45230
```

---

## 3. 動作確認

### 3-1. DB初期化確認

```bash
curl http://localhost:8089/api/programs/update/status
```

レスポンス例：
```json
{
  "total_updates": 705,
  "recent_updates": [
    {"area_id": "JP27", "date": "20250123", "updated_at": "2025-01-23T12:34:56", "status": "success"},
    ...
  ]
}
```

### 3-2. 検索API確認

```bash
curl "http://localhost:8089/api/programs/search?keyword=ニュース"
```

レスポンス例：
```json
{
  "success": true,
  "count": 324,
  "programs": [
    {
      "areaId": "JP13",
      "stationId": "TBS",
      "stationName": "TBSラジオ",
      "title": "ニュース",
      "ft": "2025-01-23T12:00:00",
      "to": "2025-01-23T12:15:00",
      ...
    },
    ...
  ]
}
```

---

## 4. フロントエンド変更（次のステップ）

`web/html/index.html`の全エリア検索機能を変更：

**変更前（現在）:**
```javascript
// 全エリアを並列でfetchしてフィルタリング
for (const areaId of allAreaIds) {
    // radikoAPIを直接叩く
}
```

**変更後:**
```javascript
// サーバーのDB検索APIを呼ぶだけ
const response = await fetch(`/api/programs/search?keyword=${keyword}`);
const data = await response.json();
displayPrograms(data.programs);
```

---

## 5. 自動更新について

### スケジュール

- **自動更新**: 30分ごと
- **対象期間**: 今日の7日前 〜 今日の7日後（計15日間）
- **データ保持**: 15日より古いデータは自動削除

### 手動更新

いつでも即時更新可能：
```bash
curl -X POST http://radiko.degucha.com/api/programs/update/trigger
```

---

## 6. トラブルシューティング

### Q1. DBファイルが作成されない

**確認:**
```bash
ls -la /home/sites/radiko-recorder/data/
```

**対処:**
```bash
chmod 755 /home/sites/radiko-recorder/data
docker-compose restart proxy
```

### Q2. 番組データが取得できない

**ログ確認:**
```bash
docker-compose logs proxy | grep "Error fetching"
```

**対処:** radiko.jpへのネットワーク接続を確認

### Q3. 検索が遅い

**DB再構築:**
```bash
docker exec -it radiko-proxy bash
rm /home/sites/radiko-recorder/data/programs.db
python3 -c "import db; db.init_database()"
python3 fetch_programs.py
```

### Q4. スケジューラーが動いていない

**確認:**
```bash
docker-compose logs proxy | grep "Scheduler"
```

**対処:**
```bash
docker-compose restart proxy
```

---

## 7. パフォーマンス

### 全エリア検索の比較

|  | 変更前 | 変更後 |
|---|---|---|
| **初回検索** | 数分（ブラウザで全エリア並列fetch） | **0.1秒**（DB検索） |
| **2回目以降** | 数十秒（ブラウザキャッシュ） | **0.1秒**（DB検索） |
| **ユーザー負荷** | 高い（CPU/メモリ/通信） | **なし** |
| **容量** | ブラウザストレージ数百MB | サーバー側のみ |

### データベースサイズ

- **想定サイズ**: 50〜100MB（15日分）
- **ディスク使用量**: `/home/sites/radiko-recorder/data/`

確認コマンド：
```bash
du -h /home/sites/radiko-recorder/data/programs.db
```

---

## 8. 次回以降の更新

HTMLのみの更新（フロントエンド変更）：
```bash
# FTPでweb/html/index.htmlをアップロード
# 再起動不要（即座に反映）
```

Pythonコードの更新（バックエンド変更）：
```bash
# FTPでproxy/配下のファイルをアップロード
docker-compose restart proxy
```

---

## 9. ロールバック手順

問題が発生した場合：

```bash
# 旧バージョンに戻す
cd /home/sites/radiko-recorder
git checkout [前のコミットハッシュ] proxy/app.py
docker-compose restart proxy

# DBを削除（IndexedDBに戻す場合）
rm /home/sites/radiko-recorder/data/programs.db
```

---

## まとめ

この変更により：
1. ✅ 全エリア検索が爆速化（数分 → 0.1秒）
2. ✅ ユーザー側の負荷がゼロに
3. ✅ ブラウザストレージ容量問題が解消
4. ✅ サーバーで一元管理

質問があれば気軽に聞いてください！
