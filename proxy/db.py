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

        # cron予約テーブル（定期録音）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cron_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                hour TEXT NOT NULL,
                day_of_month TEXT NOT NULL,
                month TEXT NOT NULL,
                day_of_week TEXT NOT NULL,
                command TEXT NOT NULL,
                title TEXT,
                station TEXT,
                start_time TEXT,
                end_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # at予約テーブル（1回限りの録音）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS at_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                schedule_time TEXT NOT NULL,
                command TEXT NOT NULL,
                title TEXT,
                station TEXT,
                start_time TEXT,
                end_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # アートワークテーブル（番組タイトルごとのアートワーク）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS artworks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                image_data BLOB NOT NULL,
                mime_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 録音ファイル管理テーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recorded_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                file_name TEXT NOT NULL,
                program_id INTEGER,
                program_title TEXT,
                station_id TEXT,
                station_name TEXT,
                broadcast_date TEXT,
                start_time TEXT,
                end_time TEXT,
                file_size INTEGER,
                duration REAL,
                file_modified TIMESTAMP,
                virtual_folder_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE SET NULL,
                FOREIGN KEY (virtual_folder_id) REFERENCES virtual_folders(id) ON DELETE SET NULL
            )
        ''')

        # 仮想フォルダテーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS virtual_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                color TEXT,
                icon TEXT,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES virtual_folders(id) ON DELETE CASCADE
            )
        ''')

        # 録音ファイル用インデックス
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_recorded_files_program_title
            ON recorded_files(program_title)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_recorded_files_broadcast_date
            ON recorded_files(broadcast_date)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_recorded_files_station
            ON recorded_files(station_id)
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

        # 前後7日間の番組のみ（過去7日〜未来7日）
        query += ' AND p.start_time >= datetime("now", "-7 days") AND p.start_time <= datetime("now", "+7 days")'

        query += ' GROUP BY p.id ORDER BY p.start_time DESC LIMIT 1000'

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


# ========================================
# 予約管理関連の関数
# ========================================

def save_cron_job(minute: str, hour: str, day_of_month: str, month: str, day_of_week: str,
                  command: str, title: str = '', station: str = '', start_time: str = '', end_time: str = ''):
    """cron予約をDBに保存"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO cron_jobs (minute, hour, day_of_month, month, day_of_week, command, title, station, start_time, end_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (minute, hour, day_of_month, month, day_of_week, command, title, station, start_time, end_time))

        job_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f'✅ Cron job saved: {job_id}')
        return job_id

    except Exception as e:
        logger.error(f'❌ Save cron job error: {str(e)}')
        return None


def get_all_cron_jobs():
    """全てのcron予約を取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM cron_jobs ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()

        jobs = []
        for row in rows:
            jobs.append({
                'id': row['id'],
                'minute': row['minute'],
                'hour': row['hour'],
                'day_of_month': row['day_of_month'],
                'month': row['month'],
                'day_of_week': row['day_of_week'],
                'command': row['command'],
                'title': row['title'],
                'station': row['station'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'created_at': row['created_at']
            })

        return jobs

    except Exception as e:
        logger.error(f'❌ Get cron jobs error: {str(e)}')
        return []


def delete_cron_job(job_id: int):
    """cron予約を削除"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('DELETE FROM cron_jobs WHERE id = ?', (job_id,))
        conn.commit()
        conn.close()

        logger.info(f'✅ Cron job deleted: {job_id}')
        return True

    except Exception as e:
        logger.error(f'❌ Delete cron job error: {str(e)}')
        return False


def save_at_job(job_id: str, schedule_time: str, command: str, title: str = '',
                station: str = '', start_time: str = '', end_time: str = ''):
    """at予約をDBに保存（job_idがNoneの場合は自動生成されたIDを返す）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if job_id is None:
            # job_idを指定しない場合は自動生成
            cursor.execute('''
                INSERT INTO at_jobs (schedule_time, command, title, station, start_time, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (schedule_time, command, title, station, start_time, end_time))
            generated_id = cursor.lastrowid
        else:
            # job_idを指定する場合
            cursor.execute('''
                INSERT INTO at_jobs (job_id, schedule_time, command, title, station, start_time, end_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (job_id, schedule_time, command, title, station, start_time, end_time))
            generated_id = job_id

        conn.commit()
        conn.close()

        logger.info(f'✅ At job saved: {generated_id}')
        return generated_id

    except Exception as e:
        logger.error(f'❌ Save at job error: {str(e)}')
        return None


def get_all_at_jobs():
    """全てのat予約を取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM at_jobs ORDER BY schedule_time ASC')
        rows = cursor.fetchall()
        conn.close()

        jobs = []
        for row in rows:
            jobs.append({
                'id': row['id'],
                'job_id': row['job_id'],
                'schedule_time': row['schedule_time'],
                'command': row['command'],
                'title': row['title'],
                'station': row['station'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'created_at': row['created_at']
            })

        return jobs

    except Exception as e:
        logger.error(f'❌ Get at jobs error: {str(e)}')
        return []


def delete_at_job(job_id):
    """at予約を削除（idまたはjob_idで削除）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # idカラムで削除（主キー）
        cursor.execute('DELETE FROM at_jobs WHERE id = ?', (job_id,))

        if cursor.rowcount == 0:
            # idで見つからない場合はjob_idで試行
            cursor.execute('DELETE FROM at_jobs WHERE job_id = ?', (job_id,))

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ At job deleted: {job_id}')
            return True
        else:
            logger.warning(f'⚠️ At job not found: {job_id}')
            return False

    except Exception as e:
        logger.error(f'❌ Delete at job error: {str(e)}')
        return False


def save_artwork(title: str, image_data: bytes, mime_type: str):
    """アートワークを保存（同じタイトルの場合は更新）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO artworks (title, image_data, mime_type, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(title) DO UPDATE SET
                image_data = excluded.image_data,
                mime_type = excluded.mime_type,
                updated_at = CURRENT_TIMESTAMP
        ''', (title, image_data, mime_type))

        conn.commit()
        conn.close()

        logger.info(f'✅ Artwork saved: {title}')
        return True

    except Exception as e:
        logger.error(f'❌ Save artwork error: {str(e)}')
        return False


def get_artwork(title: str):
    """タイトルに対応するアートワークを取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            'SELECT image_data, mime_type FROM artworks WHERE title = ?',
            (title,)
        )

        result = cursor.fetchone()
        conn.close()

        if result:
            return {
                'image_data': result[0],
                'mime_type': result[1]
            }
        else:
            return None

    except Exception as e:
        logger.error(f'❌ Get artwork error: {str(e)}')
        return None


def list_artworks():
    """登録されているアートワーク一覧を取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, title, mime_type, created_at, updated_at
            FROM artworks
            ORDER BY updated_at DESC
        ''')

        rows = cursor.fetchall()
        conn.close()

        artworks = []
        for row in rows:
            artworks.append({
                'id': row[0],
                'title': row[1],
                'mime_type': row[2],
                'created_at': row[3],
                'updated_at': row[4]
            })

        return artworks

    except Exception as e:
        logger.error(f'❌ List artworks error: {str(e)}')
        return []


def delete_artwork(title: str):
    """アートワークを削除"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('DELETE FROM artworks WHERE title = ?', (title,))

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ Artwork deleted: {title}')
            return True
        else:
            logger.warning(f'⚠️ Artwork not found: {title}')
            return False

    except Exception as e:
        logger.error(f'❌ Delete artwork error: {str(e)}')
        return False


# ========================================
# 録音ファイル管理関連の関数
# ========================================

def register_recorded_file(file_path: str, file_name: str, program_id: int = None,
                          program_title: str = None, station_id: str = None, station_name: str = None,
                          broadcast_date: str = None, start_time: str = None, end_time: str = None,
                          file_size: int = None, duration: float = None, file_modified: str = None):
    """録音ファイルをDBに登録（既存の場合は更新）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO recorded_files (
                file_path, file_name, program_id, program_title, station_id, station_name,
                broadcast_date, start_time, end_time, file_size, duration, file_modified, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(file_path) DO UPDATE SET
                file_name = excluded.file_name,
                program_id = excluded.program_id,
                program_title = excluded.program_title,
                station_id = excluded.station_id,
                station_name = excluded.station_name,
                broadcast_date = excluded.broadcast_date,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                file_size = excluded.file_size,
                duration = excluded.duration,
                file_modified = excluded.file_modified,
                updated_at = CURRENT_TIMESTAMP
        ''', (file_path, file_name, program_id, program_title, station_id, station_name,
              broadcast_date, start_time, end_time, file_size, duration, file_modified))

        file_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f'✅ Recorded file registered: {file_path}')
        return file_id

    except Exception as e:
        logger.error(f'❌ Register recorded file error: {str(e)}')
        return None


def get_all_recorded_files(limit: int = 1000, offset: int = 0):
    """全ての録音ファイルを取得（番組情報も含む）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                rf.*,
                p.title as program_db_title,
                p.description as program_description,
                p.performer as program_performer,
                p.info as program_db_info,
                p.url as program_url
            FROM recorded_files rf
            LEFT JOIN programs p ON rf.program_id = p.id
            ORDER BY rf.file_modified DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))

        rows = cursor.fetchall()
        conn.close()

        files = []
        for row in rows:
            file_data = {
                'id': row['id'],
                'file_path': row['file_path'],
                'file_name': row['file_name'],
                'program_id': row['program_id'],
                'program_title': row['program_title'],
                'station_id': row['station_id'],
                'station_name': row['station_name'],
                'broadcast_date': row['broadcast_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'file_size': row['file_size'],
                'duration': row['duration'],
                'file_modified': row['file_modified'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }

            # 番組表データがあれば追加
            if row['program_id']:
                file_data['program_info'] = {
                    'program_db_title': row['program_db_title'],
                    'program_description': row['program_description'],
                    'program_performer': row['program_performer'],
                    'program_info': row['program_db_info'],
                    'program_url': row['program_url']
                }

            files.append(file_data)

        return files

    except Exception as e:
        logger.error(f'❌ Get recorded files error: {str(e)}')
        return []


def search_recorded_files(keyword: str = None, station_id: str = None,
                         broadcast_date_from: str = None, broadcast_date_to: str = None):
    """録音ファイルを検索"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = 'SELECT * FROM recorded_files WHERE 1=1'
        params = []

        if keyword:
            query += ' AND (program_title LIKE ? OR file_name LIKE ?)'
            params.extend([f'%{keyword}%', f'%{keyword}%'])

        if station_id:
            query += ' AND station_id = ?'
            params.append(station_id)

        if broadcast_date_from:
            query += ' AND broadcast_date >= ?'
            params.append(broadcast_date_from)

        if broadcast_date_to:
            query += ' AND broadcast_date <= ?'
            params.append(broadcast_date_to)

        query += ' ORDER BY file_modified DESC LIMIT 1000'

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        files = []
        for row in rows:
            files.append({
                'id': row['id'],
                'file_path': row['file_path'],
                'file_name': row['file_name'],
                'program_title': row['program_title'],
                'station_id': row['station_id'],
                'station_name': row['station_name'],
                'broadcast_date': row['broadcast_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'file_size': row['file_size'],
                'duration': row['duration'],
                'file_modified': row['file_modified'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })

        return files

    except Exception as e:
        logger.error(f'❌ Search recorded files error: {str(e)}')
        return []


def delete_recorded_file(file_path: str):
    """録音ファイルをDBから削除"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('DELETE FROM recorded_files WHERE file_path = ?', (file_path,))

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ Recorded file deleted from DB: {file_path}')
            return True
        else:
            logger.warning(f'⚠️ Recorded file not found in DB: {file_path}')
            return False

    except Exception as e:
        logger.error(f'❌ Delete recorded file error: {str(e)}')
        return False


def get_recorded_file_by_path(file_path: str):
    """ファイルパスで録音ファイル情報を取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM recorded_files WHERE file_path = ?', (file_path,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                'id': row['id'],
                'file_path': row['file_path'],
                'file_name': row['file_name'],
                'program_title': row['program_title'],
                'station_id': row['station_id'],
                'station_name': row['station_name'],
                'broadcast_date': row['broadcast_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'file_size': row['file_size'],
                'duration': row['duration'],
                'file_modified': row['file_modified'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }
        else:
            return None

    except Exception as e:
        logger.error(f'❌ Get recorded file error: {str(e)}')
        return None


def find_program_by_info(station_id: str, start_time: str):
    """局IDと開始時刻から番組を検索"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id FROM programs
            WHERE station_id = ? AND start_time = ?
            LIMIT 1
        ''', (station_id, start_time))

        row = cursor.fetchone()
        conn.close()

        if row:
            return row['id']
        else:
            return None

    except Exception as e:
        logger.error(f'❌ Find program error: {str(e)}')
        return None


# ========================================
# 仮想フォルダ管理
# ========================================

def create_virtual_folder(name: str, parent_id: int = None, color: str = None, icon: str = None):
    """仮想フォルダを作成"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO virtual_folders (name, parent_id, color, icon)
            VALUES (?, ?, ?, ?)
        ''', (name, parent_id, color, icon))

        folder_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f'✅ Virtual folder created: {name} (ID: {folder_id})')
        return folder_id

    except Exception as e:
        logger.error(f'❌ Create virtual folder error: {str(e)}')
        return None


def get_all_virtual_folders():
    """全ての仮想フォルダを取得"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM virtual_folders
            ORDER BY sort_order ASC, name ASC
        ''')

        rows = cursor.fetchall()
        conn.close()

        folders = []
        for row in rows:
            folders.append({
                'id': row['id'],
                'name': row['name'],
                'parent_id': row['parent_id'],
                'color': row['color'],
                'icon': row['icon'],
                'sort_order': row['sort_order'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })

        return folders

    except Exception as e:
        logger.error(f'❌ Get virtual folders error: {str(e)}')
        return []


def update_virtual_folder(folder_id: int, name: str = None, color: str = None, icon: str = None, parent_id: int = None):
    """仮想フォルダを更新"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        updates = []
        params = []

        if name is not None:
            updates.append('name = ?')
            params.append(name)
        if color is not None:
            updates.append('color = ?')
            params.append(color)
        if icon is not None:
            updates.append('icon = ?')
            params.append(icon)
        if parent_id is not None:
            updates.append('parent_id = ?')
            params.append(parent_id)

        if not updates:
            return False

        updates.append('updated_at = CURRENT_TIMESTAMP')
        params.append(folder_id)

        query = f"UPDATE virtual_folders SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ Virtual folder updated: ID {folder_id}')
            return True
        else:
            return False

    except Exception as e:
        logger.error(f'❌ Update virtual folder error: {str(e)}')
        return False


def delete_virtual_folder(folder_id: int):
    """仮想フォルダを削除（フォルダ内のファイルはルートに移動）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # フォルダ内のファイルをルート（NULL）に移動
        cursor.execute('''
            UPDATE recorded_files
            SET virtual_folder_id = NULL
            WHERE virtual_folder_id = ?
        ''', (folder_id,))

        # フォルダを削除
        cursor.execute('DELETE FROM virtual_folders WHERE id = ?', (folder_id,))

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ Virtual folder deleted: ID {folder_id}')
            return True
        else:
            return False

    except Exception as e:
        logger.error(f'❌ Delete virtual folder error: {str(e)}')
        return False


def move_file_to_folder(file_path: str, folder_id: int = None):
    """ファイルを仮想フォルダに移動（folder_id=Noneでルートに移動）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE recorded_files
            SET virtual_folder_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE file_path = ?
        ''', (folder_id, file_path))

        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows > 0:
            logger.info(f'✅ File moved to folder: {file_path} -> Folder ID {folder_id}')
            return True
        else:
            logger.warning(f'⚠️ File not found: {file_path}')
            return False

    except Exception as e:
        logger.error(f'❌ Move file to folder error: {str(e)}')
        return False


def get_files_in_folder(folder_id: int = None, limit: int = 1000, offset: int = 0):
    """仮想フォルダ内のファイルを取得（folder_id=Noneでルートのファイル）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if folder_id is None:
            # ルートのファイル（フォルダに属していないファイル）
            cursor.execute('''
                SELECT
                    rf.*,
                    p.title as program_db_title,
                    p.description as program_description,
                    p.performer as program_performer,
                    p.info as program_db_info,
                    p.url as program_url
                FROM recorded_files rf
                LEFT JOIN programs p ON rf.program_id = p.id
                WHERE rf.virtual_folder_id IS NULL
                ORDER BY rf.file_modified DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        else:
            # 指定されたフォルダ内のファイル
            cursor.execute('''
                SELECT
                    rf.*,
                    p.title as program_db_title,
                    p.description as program_description,
                    p.performer as program_performer,
                    p.info as program_db_info,
                    p.url as program_url
                FROM recorded_files rf
                LEFT JOIN programs p ON rf.program_id = p.id
                WHERE rf.virtual_folder_id = ?
                ORDER BY rf.file_modified DESC
                LIMIT ? OFFSET ?
            ''', (folder_id, limit, offset))

        rows = cursor.fetchall()
        conn.close()

        files = []
        for row in rows:
            # file_modifiedをUnixタイムスタンプに変換（文字列の場合）
            modified_value = row['file_modified']
            if isinstance(modified_value, str):
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(modified_value.replace('Z', '+00:00'))
                    modified_value = dt.timestamp()
                except:
                    modified_value = 0

            # フロントエンドが期待する形式に合わせる（path, name, size, modifiedを使用）
            file_data = {
                'id': row['id'],
                'path': row['file_path'],  # フロントエンドは 'path' を期待
                'name': row['file_name'],  # フロントエンドは 'name' を期待
                'size': row['file_size'],  # フロントエンドは 'size' を期待
                'modified': modified_value,  # Unixタイムスタンプ
                'program_id': row['program_id'],
                'program_title': row['program_title'],
                'station_id': row['station_id'],
                'station_name': row['station_name'],
                'broadcast_date': row['broadcast_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'duration': row['duration'],
                'virtual_folder_id': row['virtual_folder_id'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }

            # 番組表データがあれば追加
            if row['program_id']:
                file_data['program_info'] = {
                    'program_db_title': row['program_db_title'],
                    'program_description': row['program_description'],
                    'program_performer': row['program_performer'],
                    'program_info': row['program_db_info'],
                    'program_url': row['program_url']
                }

            files.append(file_data)

        return files

    except Exception as e:
        logger.error(f'❌ Get files in folder error: {str(e)}')
        return []


if __name__ == '__main__':
    # テスト用
    logging.basicConfig(level=logging.INFO)
    init_database()
