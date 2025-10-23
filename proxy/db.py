"""
番組表キャッシュ用のデータベースモジュール
"""
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Optional
import os

logger = logging.getLogger(__name__)

# DBファイルのパス
DB_PATH = '/home/sites/radiko-recorder/data/programs.db'

def init_database():
    """データベースを初期化"""
    try:
        # データディレクトリを作成
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # programsテーブル作成
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_id TEXT NOT NULL,
                station_id TEXT NOT NULL,
                station_name TEXT,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                description TEXT,
                performer TEXT,
                info TEXT,
                url TEXT,
                date TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(station_id, start_time)
            )
        ''')

        # インデックス作成
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_search
            ON programs(title, performer, description)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_date
            ON programs(date)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_area
            ON programs(area_id)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_station_start
            ON programs(station_id, start_time)
        ''')

        # メタデータテーブル（最終更新時刻を記録）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS update_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_id TEXT NOT NULL,
                date TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT,
                UNIQUE(area_id, date)
            )
        ''')

        conn.commit()
        conn.close()

        logger.info(f'✅ Database initialized: {DB_PATH}')
        return True

    except Exception as e:
        logger.error(f'❌ Database initialization error: {str(e)}')
        return False


def save_programs(programs: List[Dict], area_id: str, date: str):
    """番組データを保存（一括UPSERT）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 既存データを削除（該当エリア・日付）
        cursor.execute('''
            DELETE FROM programs
            WHERE area_id = ? AND date = ?
        ''', (area_id, date))

        # 新しいデータを一括挿入
        insert_data = []
        for prog in programs:
            insert_data.append((
                area_id,
                prog.get('stationId', ''),
                prog.get('stationName', ''),
                prog.get('title', ''),
                prog.get('ft', ''),  # ISO format string
                prog.get('to', ''),  # ISO format string
                prog.get('desc', ''),
                prog.get('pfm', ''),
                prog.get('info', ''),
                prog.get('url', ''),
                date,
                datetime.now().isoformat()
            ))

        cursor.executemany('''
            INSERT INTO programs (
                area_id, station_id, station_name, title,
                start_time, end_time, description, performer,
                info, url, date, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', insert_data)

        # 更新ログを記録
        cursor.execute('''
            INSERT OR REPLACE INTO update_log (area_id, date, updated_at, status)
            VALUES (?, ?, ?, ?)
        ''', (area_id, date, datetime.now().isoformat(), 'success'))

        conn.commit()
        conn.close()

        logger.info(f'✅ Saved {len(programs)} programs for {area_id} on {date}')
        return True

    except Exception as e:
        logger.error(f'❌ Save programs error: {str(e)}')
        return False


def search_programs(keyword: str, area_id: Optional[str] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None) -> List[Dict]:
    """番組を検索"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 基本クエリ
        query = '''
            SELECT
                area_id, station_id, station_name, title,
                start_time, end_time, description, performer,
                info, url, date
            FROM programs
            WHERE (
                title LIKE ? OR
                performer LIKE ? OR
                description LIKE ?
            )
        '''

        params = [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%']

        # エリアフィルター
        if area_id:
            query += ' AND area_id = ?'
            params.append(area_id)

        # 日付範囲フィルター
        if date_from:
            query += ' AND date >= ?'
            params.append(date_from)

        if date_to:
            query += ' AND date <= ?'
            params.append(date_to)

        query += ' ORDER BY start_time ASC LIMIT 1000'

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                'areaId': row['area_id'],
                'stationId': row['station_id'],
                'stationName': row['station_name'],
                'title': row['title'],
                'ft': row['start_time'],
                'to': row['end_time'],
                'desc': row['description'],
                'pfm': row['performer'],
                'info': row['info'],
                'url': row['url'],
                'date': row['date']
            })

        conn.close()

        logger.info(f'🔍 Search "{keyword}": found {len(results)} programs')
        return results

    except Exception as e:
        logger.error(f'❌ Search programs error: {str(e)}')
        return []


def get_programs_by_area_date(area_id: str, date: str) -> List[Dict]:
    """特定エリア・日付の番組を取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                area_id, station_id, station_name, title,
                start_time, end_time, description, performer,
                info, url, date
            FROM programs
            WHERE area_id = ? AND date = ?
            ORDER BY start_time ASC
        ''', (area_id, date))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                'areaId': row['area_id'],
                'stationId': row['station_id'],
                'stationName': row['station_name'],
                'title': row['title'],
                'ft': row['start_time'],
                'to': row['end_time'],
                'desc': row['description'],
                'pfm': row['performer'],
                'info': row['info'],
                'url': row['url'],
                'date': row['date']
            })

        conn.close()

        return results

    except Exception as e:
        logger.error(f'❌ Get programs error: {str(e)}')
        return []


def get_update_status() -> Dict:
    """更新ステータスを取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                area_id, date, updated_at, status
            FROM update_log
            ORDER BY updated_at DESC
            LIMIT 100
        ''')

        rows = cursor.fetchall()

        status = {
            'total_updates': len(rows),
            'recent_updates': []
        }

        for row in rows:
            status['recent_updates'].append({
                'area_id': row['area_id'],
                'date': row['date'],
                'updated_at': row['updated_at'],
                'status': row['status']
            })

        conn.close()

        return status

    except Exception as e:
        logger.error(f'❌ Get update status error: {str(e)}')
        return {'total_updates': 0, 'recent_updates': []}


def cleanup_old_data(days_to_keep: int = 15):
    """古いデータを削除"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 指定日数より古いデータを削除
        cursor.execute('''
            DELETE FROM programs
            WHERE date < date('now', ? || ' days')
        ''', (f'-{days_to_keep}',))

        deleted_programs = cursor.rowcount

        cursor.execute('''
            DELETE FROM update_log
            WHERE date < date('now', ? || ' days')
        ''', (f'-{days_to_keep}',))

        deleted_logs = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(f'🗑️ Cleaned up: {deleted_programs} programs, {deleted_logs} logs')
        return deleted_programs

    except Exception as e:
        logger.error(f'❌ Cleanup error: {str(e)}')
        return 0


if __name__ == '__main__':
    # テスト用
    logging.basicConfig(level=logging.INFO)
    init_database()
