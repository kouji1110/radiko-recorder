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
# Docker環境では環境変数BASE_DIRを使用、デフォルトは /app
BASE_DIR = os.environ.get('BASE_DIR', '/app')
DB_PATH = os.path.join(BASE_DIR, 'data', 'programs.db')

def init_database():
    """データベースを初期化"""
    try:
        # データディレクトリを作成
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # programsテーブル：番組データ本体（重複なし）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        # program_areasテーブル：どのエリアで聴けるかのマッピング
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS program_areas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL,
                area_id TEXT NOT NULL,
                UNIQUE(program_id, area_id),
                FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
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
            CREATE INDEX IF NOT EXISTS idx_station_start
            ON programs(station_id, start_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_program_areas_area
            ON program_areas(area_id)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_program_areas_program
            ON program_areas(program_id)
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
    """番組データを保存（新スキーマ：programs + program_areas）

    同じ番組（station_id + start_time）は1回だけprogramsに保存し、
    エリア情報はprogram_areasに保存することで重複を避ける
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 該当エリア・日付のマッピングを削除
        cursor.execute('''
            DELETE FROM program_areas
            WHERE program_id IN (
                SELECT p.id FROM programs p
                JOIN program_areas pa ON p.id = pa.program_id
                WHERE pa.area_id = ? AND p.date = ?
            )
            AND area_id = ?
        ''', (area_id, date, area_id))

        saved_count = 0
        skipped_count = 0

        for prog in programs:
            station_id = prog.get('stationId', '')
            start_time = prog.get('ft', '')

            # 番組データを挿入（既存の場合はスキップ）
            cursor.execute('''
                INSERT OR IGNORE INTO programs (
                    station_id, station_name, title,
                    start_time, end_time, description, performer,
                    info, url, date, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                station_id,
                prog.get('stationName', ''),
                prog.get('title', ''),
                start_time,
                prog.get('to', ''),
                prog.get('desc', ''),
                prog.get('pfm', ''),
                prog.get('info', ''),
                prog.get('url', ''),
                date,
                datetime.now().isoformat()
            ))

            # program_id を取得
            cursor.execute('''
                SELECT id FROM programs
                WHERE station_id = ? AND start_time = ?
            ''', (station_id, start_time))

            row = cursor.fetchone()
            if row:
                program_id = row[0]

                # エリアマッピングを追加
                cursor.execute('''
                    INSERT OR IGNORE INTO program_areas (program_id, area_id)
                    VALUES (?, ?)
                ''', (program_id, area_id))

                saved_count += 1
            else:
                skipped_count += 1

        # 更新ログを記録
        cursor.execute('''
            INSERT OR REPLACE INTO update_log (area_id, date, updated_at, status)
            VALUES (?, ?, ?, ?)
        ''', (area_id, date, datetime.now().isoformat(), 'success'))

        conn.commit()
        conn.close()

        logger.info(f'✅ Saved {saved_count} programs for {area_id} on {date} (skipped: {skipped_count})')
        return True

    except Exception as e:
        logger.error(f'❌ Save programs error: {str(e)}')
        return False


def search_programs(keyword: str, area_id: Optional[str] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None) -> List[Dict]:
    """番組を検索（新スキーマ対応）

    Args:
        keyword: 検索キーワード
        area_id: エリアID（指定時はそのエリアで聴ける全番組）
        date_from: 開始日付
        date_to: 終了日付
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 基本クエリ：programsとprogram_areasをJOIN
        if area_id:
            # エリア指定時：そのエリアで聴ける番組のみ
            query = '''
                SELECT DISTINCT
                    p.station_id, p.station_name, p.title,
                    p.start_time, p.end_time, p.description, p.performer,
                    p.info, p.url, p.date,
                    GROUP_CONCAT(DISTINCT pa.area_id) as area_ids
                FROM programs p
                JOIN program_areas pa ON p.id = pa.program_id
                WHERE (
                    p.title LIKE ? OR
                    p.performer LIKE ? OR
                    p.description LIKE ?
                )
                AND pa.area_id = ?
            '''
            params = [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', area_id]
        else:
            # 全体検索時：全番組（重複なし）
            query = '''
                SELECT DISTINCT
                    p.station_id, p.station_name, p.title,
                    p.start_time, p.end_time, p.description, p.performer,
                    p.info, p.url, p.date,
                    GROUP_CONCAT(DISTINCT pa.area_id) as area_ids
                FROM programs p
                LEFT JOIN program_areas pa ON p.id = pa.program_id
                WHERE (
                    p.title LIKE ? OR
                    p.performer LIKE ? OR
                    p.description LIKE ?
                )
            '''
            params = [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%']

        # 日付範囲フィルター
        if date_from:
            query += ' AND p.date >= ?'
            params.append(date_from)

        if date_to:
            query += ' AND p.date <= ?'
            params.append(date_to)

        query += ' GROUP BY p.id ORDER BY p.start_time ASC LIMIT 1000'

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            area_ids = row['area_ids'].split(',') if row['area_ids'] else []
            results.append({
                'areaId': area_ids[0] if area_ids else '',  # 最初のエリアIDを代表として返す
                'areaIds': area_ids,  # 全エリアIDも返す
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
    """特定エリア・日付の番組を取得（新スキーマ対応）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT
                p.station_id, p.station_name, p.title,
                p.start_time, p.end_time, p.description, p.performer,
                p.info, p.url, p.date,
                GROUP_CONCAT(DISTINCT pa.area_id) as area_ids
            FROM programs p
            JOIN program_areas pa ON p.id = pa.program_id
            WHERE pa.area_id = ? AND p.date = ?
            GROUP BY p.id
            ORDER BY p.start_time ASC
        ''', (area_id, date))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            area_ids = row['area_ids'].split(',') if row['area_ids'] else []
            results.append({
                'areaId': area_id,  # リクエストされたエリアIDを返す
                'areaIds': area_ids,  # 全エリアIDも返す
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
