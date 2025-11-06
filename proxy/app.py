from flask import Flask, Response, request, jsonify, stream_with_context, send_file, session, redirect, url_for
import requests
from flask_cors import CORS
import logging
import subprocess
import json
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import zipfile
import tempfile
import time
import select
from functools import wraps
import threading

# DBモジュールをインポート
import db
import fetch_programs

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 日本語などの非ASCII文字をそのまま出力
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'radiko-recorder-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # セッション有効期限30日
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# パス設定（Docker環境用）
BASE_DIR = os.environ.get('BASE_DIR', '/app')
SCRIPT_PATH = os.path.join(BASE_DIR, 'script/myradiko')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output/radio')

# APSchedulerの初期化
scheduler = BackgroundScheduler(daemon=True, timezone='Asia/Tokyo')
scheduler.start()

# アプリ終了時にスケジューラーを停止
atexit.register(lambda: scheduler.shutdown())

logger.info('✅ APScheduler initialized')

# ========================================
# 認証設定
# ========================================

# パスワード設定（環境変数から取得、デフォルトは'radiko2025'）
AUTH_PASSWORD = os.environ.get('AUTH_PASSWORD', 'radiko2025')

def login_required(f):
    """ログインが必要なエンドポイント用のデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Unauthorized', 'message': '認証が必要です'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/auth/login', methods=['POST'])
def login():
    """ログインエンドポイント"""
    data = request.get_json()
    password = data.get('password', '')

    if password == AUTH_PASSWORD:
        session['logged_in'] = True
        session.permanent = True  # 30日間有効
        logger.info('✅ Login successful')
        return jsonify({'success': True, 'message': 'ログインしました'})
    else:
        logger.warning('❌ Login failed: incorrect password')
        return jsonify({'success': False, 'message': 'パスワードが間違っています'}), 401

@app.route('/auth/logout', methods=['POST'])
def logout():
    """ログアウトエンドポイント"""
    session.pop('logged_in', None)
    logger.info('👋 Logout successful')
    return jsonify({'success': True, 'message': 'ログアウトしました'})

@app.route('/auth/check', methods=['GET'])
def check_auth():
    """認証状態確認エンドポイント"""
    is_logged_in = session.get('logged_in', False)
    return jsonify({'logged_in': is_logged_in})


# ========================================
# 録音実行関数
# ========================================

def convert_cron_dow_to_apscheduler(cron_dow):
    """
    cron形式の曜日（0=日曜, 1=月曜, ..., 6=土曜）を
    APScheduler形式（mon,tue,wed,thu,fri,sat,sun）に変換
    """
    # cronの数値表記をAPSchedulerの文字列表記に変換
    dow_map = {
        '0': 'sun',
        '1': 'mon',
        '2': 'tue',
        '3': 'wed',
        '4': 'thu',
        '5': 'fri',
        '6': 'sat',
        '*': '*'
    }

    # カンマ区切りの場合も対応
    if ',' in cron_dow:
        parts = cron_dow.split(',')
        return ','.join([dow_map.get(p.strip(), p.strip()) for p in parts])

    return dow_map.get(cron_dow, cron_dow)


def execute_recording(command: str, job_id=None, job_type='cron', metadata=None):
    """録音を実行する関数"""
    try:
        logger.info(f'🎙️ Recording started (type={job_type}, job_id={job_id})')
        logger.info(f'📝 Command: {command}')
        logger.info(f'📋 Metadata received: {metadata}')
        logger.info(f'📋 Metadata type: {type(metadata)}, bool: {bool(metadata)}')

        # コマンドからフォルダIDパラメータを抽出（第7引数）
        # 形式: myradiko "title" "rss" "station" "start" "end" "" "folder_id" "" >> ...
        virtual_folder_id = None
        try:
            import re
            # 7番目のクォート内の文字列を探す
            pattern = r'"([^"]*)"'
            matches = re.findall(pattern, command)
            if len(matches) >= 7:
                folder_id_str = matches[6]  # 7番目の引数（0-indexed）
                if folder_id_str and folder_id_str != '':
                    try:
                        virtual_folder_id = int(folder_id_str)
                        logger.info(f'📁 Extracted virtual_folder_id from command: {virtual_folder_id}')
                    except (ValueError, TypeError):
                        logger.warning(f'⚠️ Invalid folder ID in command: {folder_id_str}')
        except Exception as e:
            logger.warning(f'⚠️ Failed to extract folder from command: {str(e)}')

        # コマンドを実行
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=7200  # 2時間タイムアウト
        )

        if result.returncode == 0:
            logger.info(f'✅ Recording completed successfully')
            if result.stdout:
                logger.info(f'📤 Output: {result.stdout[:500]}')

            # 録音成功時、DBに登録
            if metadata:
                try:
                    # 生成されたファイルを探す
                    title = metadata.get('title', '')
                    rss = metadata.get('rss', '')
                    start_time = metadata.get('start_time', '')
                    station = metadata.get('station', '')

                    # start_timeが4桁（HHMM）の場合、今日の日付を前置
                    if len(start_time) == 4:
                        from datetime import datetime
                        today_date = datetime.now().strftime('%Y%m%d')
                        start_time = today_date + start_time
                        logger.info(f'📅 Expanded start_time from HHMM to YYYYMMDDHHMM: {start_time}')

                    # ファイル名を生成
                    filename = f'{title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'

                    # myradikoは常にOUTPUT_DIR/rss/に保存する（実際のファイルパス）
                    actual_output_dir = os.path.join(OUTPUT_DIR, rss)
                    actual_file_path = os.path.join(actual_output_dir, filename)

                    # file_pathは実際のパス（仮想フォルダを含まない）
                    relative_path = f'{rss}/{filename}'

                    # virtual_folder_idは外側のスコープから取得済み

                    # ファイル存在確認は実際のパスで行う
                    file_path = actual_file_path

                    if os.path.exists(file_path):
                        file_stat = os.stat(file_path)
                        file_metadata = extract_metadata_from_filename(filename, relative_path)

                        # 番組表から番組IDを検索
                        program_id = None
                        if rss and start_time:
                            program_id = db.find_program_by_info(rss, start_time)
                            if program_id:
                                logger.info(f'📋 Found program ID: {program_id}')

                        # DBに登録
                        db.register_recorded_file(
                            file_path=relative_path,
                            file_name=filename,
                            program_id=program_id,
                            program_title=file_metadata['program_title'],
                            station_id=file_metadata['station_id'],
                            station_name=station,
                            broadcast_date=file_metadata['broadcast_date'],
                            start_time=start_time,
                            end_time=metadata.get('end_time'),
                            file_size=file_stat.st_size,
                            duration=None,
                            file_modified=datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                            virtual_folder_id=virtual_folder_id
                        )
                        logger.info(f'📝 Recorded file registered in DB: {relative_path}')

                        # メタデータとアートワークを埋め込む
                        embed_metadata_after_recording(file_path, title, station)
                except Exception as e:
                    logger.error(f'❌ Failed to register file in DB: {str(e)}')
                    import traceback
                    logger.error(f'❌ Traceback: {traceback.format_exc()}')
        else:
            logger.error(f'❌ Recording failed with return code: {result.returncode}')
            if result.stderr:
                logger.error(f'📤 Error output: {result.stderr[:500]}')

        # at予約の場合はDBから削除
        if job_id and job_type == 'at':
            db.delete_at_job(job_id)
            logger.info(f'🗑️ At job removed from DB: {job_id}')

    except subprocess.TimeoutExpired:
        logger.error(f'❌ Recording timeout (2 hours exceeded)')
    except Exception as e:
        logger.error(f'❌ Recording error: {str(e)}')


def restore_jobs_from_db():
    """DBから予約を復元してスケジューラーに登録"""
    try:
        logger.info('🔄 Restoring jobs from database...')

        # cron予約を復元
        cron_jobs = db.get_all_cron_jobs()
        logger.info(f"📋 Found {len(cron_jobs)} cron jobs in database")

        for job in cron_jobs:
            try:
                # cron形式の曜日をAPScheduler形式に変換
                apscheduler_dow = convert_cron_dow_to_apscheduler(job['day_of_week'])

                # metadataを構築
                metadata = {
                    'title': job.get('title', ''),
                    'rss': job.get('station', ''),
                    'station': job.get('station', ''),
                    'start_time': job.get('start_time', ''),
                    'end_time': job.get('end_time', '')
                }

                scheduler.add_job(
                    func=execute_recording,
                    trigger='cron',
                    minute=job['minute'],
                    hour=job['hour'],
                    day=job['day_of_month'],
                    month=job['month'],
                    day_of_week=apscheduler_dow,
                    args=[job['command'], job['id'], 'cron', metadata],
                    id=f"cron_{job['id']}",
                    replace_existing=True
                )
                logger.info(f"✅ Cron job restored: {job['title']} (ID: {job['id']}) - Schedule: {job['minute']}:{job['hour']} on {job['day_of_week']} -> {apscheduler_dow}")
            except Exception as e:
                logger.error(f"❌ Failed to restore cron job {job['id']}: {str(e)}")

        # at予約を復元
        at_jobs = db.get_all_at_jobs()
        logger.info(f"📋 Found {len(at_jobs)} at jobs in database")

        for job in at_jobs:
            try:
                # schedule_timeをdatetimeに変換
                run_date = datetime.fromisoformat(job['schedule_time'])

                # 過去の予約はスキップ
                if run_date < datetime.now():
                    logger.warning(f"⚠️ Skipping past at job: {job['title']} (scheduled: {job['schedule_time']})")
                    db.delete_at_job(job['id'])
                    continue

                # metadataを構築
                metadata = {
                    'title': job.get('title', ''),
                    'rss': job.get('station', ''),
                    'station': job.get('station', ''),
                    'start_time': job.get('start_time', ''),
                    'end_time': job.get('end_time', '')
                }

                scheduler.add_job(
                    func=execute_recording,
                    trigger='date',
                    run_date=run_date,
                    args=[job['command'], job['id'], 'at', metadata],
                    id=f"at_{job['id']}",
                    replace_existing=True
                )
                logger.info(f"✅ At job restored: {job['title']} (ID: {job['id']}, scheduled: {job['schedule_time']})")
            except Exception as e:
                logger.error(f"❌ Failed to restore at job {job['id']}: {str(e)}")

        logger.info(f'✅ Job restoration completed: {len(cron_jobs)} cron, {len(at_jobs)} at')

    except Exception as e:
        logger.error(f'❌ Job restoration error: {str(e)}')


# DBから予約を復元
restore_jobs_from_db()


# ファイル名サニタイズ関数
def sanitize_filename(title):
    """番組名をファイル名として安全な形式に変換

    - 半角スペース・全角スペースをアンダーバーに
    - 全角文字を半角に変換
    - 全角数字・英字を半角に
    """
    if not title:
        return title

    # スペースをアンダーバーに
    title = title.replace(' ', '_')
    title = title.replace('　', '_')

    # 全角英数字を半角に
    full_to_half = str.maketrans(
        'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９',
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    )
    title = title.translate(full_to_half)

    # 全角括弧・記号を半角に
    replacements = {
        '（': '(',
        '）': ')',
        '「': '[',
        '」': ']',
        '：': ':',
        '！': '!',
        '？': '?',
        '［': '[',
        '］': ']',
        '【': '[',
        '】': ']'
    }

    for old, new in replacements.items():
        title = title.replace(old, new)

    return title

def embed_artwork_to_mp3(file_path, artwork_data, mime_type, title=None, artist=None):
    """MP3ファイルにアートワークとメタデータを埋め込む"""
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC, TIT2, TPE1

        # MP3ファイルを読み込み
        audio = MP3(file_path, ID3=ID3)

        # ID3タグが存在しない場合は追加
        if audio.tags is None:
            audio.add_tags()

        # アートワークを埋め込み
        if artwork_data:
            # 既存のアートワークを削除
            audio.tags.delall('APIC')

            # MIMEタイプをmutagenの形式に変換
            mime_map = {
                'image/jpeg': 'image/jpeg',
                'image/jpg': 'image/jpeg',
                'image/png': 'image/png',
                'image/gif': 'image/gif',
                'image/webp': 'image/webp'
            }
            mutagen_mime = mime_map.get(mime_type, 'image/jpeg')

            # アートワークを追加
            audio.tags.add(
                APIC(
                    encoding=3,  # UTF-8
                    mime=mutagen_mime,
                    type=3,  # Cover (front)
                    desc='Cover',
                    data=artwork_data
                )
            )

        # タイトルを埋め込み
        if title:
            audio.tags.delall('TIT2')
            audio.tags.add(TIT2(encoding=3, text=title))

        # アーティスト名を埋め込み
        if artist:
            audio.tags.delall('TPE1')
            audio.tags.add(TPE1(encoding=3, text=artist))

        # 保存
        audio.save()
        return True

    except Exception as e:
        logger.error(f'Failed to embed artwork to {file_path}: {str(e)}')
        return False


def embed_metadata_after_recording(file_path: str, title: str, station: str):
    """録音完了後にメタデータとアートワークを埋め込む"""
    try:
        if not os.path.exists(file_path):
            logger.warning(f'File not found for metadata embedding: {file_path}')
            return False

        # アートワークをDBから取得
        artwork_data = db.get_artwork(title)

        if artwork_data:
            # アートワークが登録されている場合、埋め込む
            logger.info(f'Embedding artwork for: {title}')
            result = embed_artwork_to_mp3(
                file_path,
                artwork_data['image_data'],
                artwork_data['mime_type'],
                title=title,
                artist=station
            )
            if result:
                logger.info(f'✅ Metadata embedded successfully: {file_path}')
            else:
                logger.warning(f'⚠️ Failed to embed metadata: {file_path}')
            return result
        else:
            # アートワークがない場合、タイトルとアーティストのみ埋め込む
            logger.info(f'No artwork found, embedding title/artist only: {title}')
            result = embed_artwork_to_mp3(
                file_path,
                None,  # アートワークなし
                None,
                title=title,
                artist=station
            )
            if result:
                logger.info(f'✅ Title/Artist embedded successfully: {file_path}')
            else:
                logger.warning(f'⚠️ Failed to embed title/artist: {file_path}')
            return result

    except Exception as e:
        logger.error(f'❌ Error embedding metadata: {str(e)}')
        return False


# DB初期化
db.init_database()

# スケジューラー設定（深夜3時に実行）
scheduler = BackgroundScheduler(daemon=True, timezone='Asia/Tokyo')
scheduler.add_job(
    func=fetch_programs.update_all_areas,
    trigger='cron',
    hour=3,  # 毎日3:00AMに実行
    minute=0,
    id='update_programs',
    name='Update radiko programs daily at 3:00 AM',
    replace_existing=True
)
scheduler.start()

# アプリ終了時にスケジューラーをシャットダウン
atexit.register(lambda: scheduler.shutdown())

logger.info('✅ Scheduler started: updating programs daily at 3:00 AM JST')

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


def monitor_and_register_recording(process, title, rss, station, start_time, end_time, virtual_folder_id, safe_title):
    """
    バックグラウンドで録音プロセスの完了を監視し、DB登録を行う

    この関数は別スレッドで実行されるため、ブラウザが切断されても
    プロセス完了とDB登録を保証する
    """
    try:
        logger.info(f'🔍 [Background] Monitoring recording process for: {title}')

        # プロセスの完了を待つ
        process.wait()

        logger.info(f'📝 [Background] Process completed with return code: {process.returncode}')

        # ファイル名を生成
        filename = f'{safe_title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'

        # myradikoは常にOUTPUT_DIR/rss/に保存する
        actual_output_dir = os.path.join(OUTPUT_DIR, rss)
        actual_file_path = os.path.join(actual_output_dir, filename)
        relative_path = f'{rss}/{filename}'

        # ISO形式の時刻を準備
        iso_start_time = f'{start_time[:4]}-{start_time[4:6]}-{start_time[6:8]}T{start_time[8:10]}:{start_time[10:12]}:00' if start_time else None
        iso_end_time = f'{end_time[:4]}-{end_time[4:6]}-{end_time[6:8]}T{end_time[8:10]}:{end_time[10:12]}:00' if end_time else None
        broadcast_date = f'{start_time[:4]}-{start_time[4:6]}-{start_time[6:8]}' if start_time else None

        # ファイルの存在確認
        if not os.path.exists(actual_file_path):
            logger.warning(f'⚠️ [Background] File not found after recording: {actual_file_path}')
            return

        logger.info(f'✅ [Background] File exists: {actual_file_path}')

        # ファイル統計情報を取得
        file_stat = os.stat(actual_file_path)

        # 番組表から番組IDを検索
        program_id = None
        if rss and iso_start_time:
            program_id = db.find_program_by_info(rss, iso_start_time)
            if program_id:
                logger.info(f'📋 [Background] Found program ID: {program_id}')

        # DBに登録
        db.register_recorded_file(
            file_path=relative_path,
            file_name=filename,
            program_id=program_id,
            program_title=title,
            station_id=rss,
            station_name=station,
            broadcast_date=broadcast_date,
            start_time=iso_start_time,
            end_time=iso_end_time,
            file_size=file_stat.st_size,
            duration=None,
            file_modified=datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
            virtual_folder_id=virtual_folder_id
        )
        logger.info(f'✅ [Background] File registered in DB: {relative_path}')

        # メタデータとアートワークを埋め込む
        embed_metadata_after_recording(actual_file_path, title, station)
        logger.info(f'✅ [Background] Metadata embedded: {relative_path}')

    except Exception as e:
        logger.error(f'❌ [Background] Error in monitor_and_register_recording: {str(e)}')
        import traceback
        logger.error(f'❌ [Background] Traceback: {traceback.format_exc()}')


@app.route('/execute', methods=['POST', 'OPTIONS'])
def execute_recording_http():
    """録音コマンドを実行してログをストリーミング（HTTPエンドポイント）"""
    # OPTIONSリクエスト（CORS preflight）への対応
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    data = request.json
    title = data.get('title', '')
    rss = data.get('rss', '')
    station = data.get('station', '')
    start_time = data.get('start_time', '')
    end_time = data.get('end_time', '')
    folder_id_str = data.get('folder', '')

    # フォルダIDを整数に変換（空文字列はNone）
    virtual_folder_id = None
    if folder_id_str and folder_id_str != '':
        try:
            virtual_folder_id = int(folder_id_str)
        except (ValueError, TypeError):
            logger.warning(f'⚠️ Invalid folder ID: {folder_id_str}')

    # タイトルをサニタイズ（スペースをアンダーバーに、全角記号を半角に）
    safe_title = sanitize_filename(title)

    # デバッグ用ログ
    logger.info(f'Original title: {title}')
    logger.info(f'Sanitized title: {safe_title}')
    logger.info(f'📁 Received virtual_folder_id: {virtual_folder_id}')

    def generate_log():
        """ログをストリーミングで返す"""
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

        # 開始ログ
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] コマンド実行開始..."}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] 元のタイトル: {title}"}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] サニタイズ後: {safe_title}"}, ensure_ascii=False)}\n\n'

        # myradikoスクリプトのパス
        script_path = SCRIPT_PATH

        # コマンド構築（サニタイズしたタイトルを使用）
        cmd = [
            script_path,
            safe_title,
            rss,
            station,
            start_time,
            end_time,
            '',  # SKIP
            '',  # DIR（使用しない）
            ''   # MAIL
        ]

        cmd_str = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in cmd])
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] {cmd_str}"}, ensure_ascii=False)}\n\n'

        try:
            # プロセスを起動（バッファなしで即座に出力）
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding='utf-8',
                errors='replace',  # エンコードエラーを置き換え文字で処理
                bufsize=0,  # バッファなし
                universal_newlines=True
            )

            # バックグラウンドスレッドでDB登録を監視
            # ブラウザが切断されても、このスレッドは独立して実行される
            monitor_thread = threading.Thread(
                target=monitor_and_register_recording,
                args=(process, title, rss, station, start_time, end_time, virtual_folder_id, safe_title),
                daemon=False  # アプリ終了時も完了を待つ
            )
            monitor_thread.start()
            logger.info(f'🚀 [Main] Background monitoring thread started for: {title}')

            # 出力を逐次送信
            last_output_time = time.time()
            error_403_detected = False

            while True:
                # プロセスが終了したかチェック
                if process.poll() is not None:
                    # 残りの出力を読み取る
                    remaining = process.stdout.read()
                    if remaining:
                        for line in remaining.splitlines():
                            line = line.rstrip()
                            if line:
                                timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                # JSON安全な文字列を生成
                                message = f"[{timestamp}] {line}"
                                yield f'data: {json.dumps({"type": "log", "message": message}, ensure_ascii=False)}\n\n'
                                if '403 Forbidden' in line:
                                    error_403_detected = True
                    break

                # 出力を読み取る（ノンブロッキング）
                line = process.stdout.readline()
                if line:
                    last_output_time = time.time()
                    line = line.rstrip()
                    if line:
                        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                        # JSON安全な文字列を生成
                        message = f"[{timestamp}] {line}"
                        yield f'data: {json.dumps({"type": "log", "message": message}, ensure_ascii=False)}\n\n'
                        if '403 Forbidden' in line:
                            error_403_detected = True
                else:
                    # 出力がない場合は少し待つ
                    time.sleep(0.1)

                    # 30秒間出力がない場合、ハートビートを送信
                    if time.time() - last_output_time > 30:
                        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] 処理中..."}, ensure_ascii=False)}\n\n'
                        last_output_time = time.time()

            timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

            # ファイル名を先に生成
            filename = f'{safe_title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'

            # myradikoは常にOUTPUT_DIR/rss/に保存する（実際のファイルパス）
            actual_output_dir = os.path.join(OUTPUT_DIR, rss)
            actual_file_path = os.path.join(actual_output_dir, filename)

            # file_pathは実際のパス（仮想フォルダを含まない）
            relative_path = f'{rss}/{filename}'

            # virtual_folder_idは外側のスコープから取得済み

            # ファイル存在確認は実際のパスで行う
            file_path = actual_file_path

            # ISO形式の時刻を事前に準備
            iso_start_time = f'{start_time[:4]}-{start_time[4:6]}-{start_time[6:8]}T{start_time[8:10]}:{start_time[10:12]}:00' if start_time else None
            iso_end_time = f'{end_time[:4]}-{end_time[4:6]}-{end_time[6:8]}T{end_time[8:10]}:{end_time[10:12]}:00' if end_time else None
            broadcast_date = f'{start_time[:4]}-{start_time[4:6]}-{start_time[6:8]}' if start_time else None

            # ファイルの存在確認
            file_exists = os.path.exists(file_path)

            if process.returncode == 0:
                # 録音成功
                if file_exists:
                    # DB登録とメタデータ埋め込みはバックグラウンドスレッドで処理される
                    yield f'data: {json.dumps({"type": "success", "message": f"[{timestamp}] 録音完了！ DB登録処理中...", "file": relative_path}, ensure_ascii=False)}\n\n'
                    yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] バックグラウンドでDB登録とメタデータ埋め込みを実行中..."}, ensure_ascii=False)}\n\n'
                else:
                    logger.error(f'❌ Command succeeded but file not found: {file_path}')
                    yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] エラー: コマンドは成功しましたが、ファイルが見つかりません"}, ensure_ascii=False)}\n\n'
                    yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] 期待されたファイル: {filename}"}, ensure_ascii=False)}\n\n'
            else:
                # コマンドが失敗した場合
                logger.error(f'❌ Recording command failed with returncode: {process.returncode}')
                yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] 録音失敗 (終了コード: {process.returncode})"}, ensure_ascii=False)}\n\n'

                # 403エラーの場合は追加説明
                if error_403_detected:
                    yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] ⚠️ 403 Forbiddenエラー: radikoのタイムシフト期間外（7日以上前）の可能性があります"}, ensure_ascii=False)}\n\n'

                # それでもファイルが存在する場合（部分的に成功）
                if file_exists:
                    logger.warning(f'⚠️ File exists despite error: {file_path}')
                    yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] ⚠️ エラーがありましたが、ファイルは作成されています"}, ensure_ascii=False)}\n\n'
                    yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] バックグラウンドでDB登録を試行中..."}, ensure_ascii=False)}\n\n'

        except Exception as e:
            timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] エラー: {str(e)}"}, ensure_ascii=False)}\n\n'

    return Response(
        stream_with_context(generate_log()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/download/<path:filepath>')
def download_file(filepath):
    """録音ファイルをダウンロード"""
    try:
        # セキュリティ: パストラバーサル対策
        base_dir = OUTPUT_DIR
        safe_path = os.path.normpath(os.path.join(base_dir, filepath))

        if not safe_path.startswith(base_dir):
            return Response('Invalid file path', status=400)

        if not os.path.exists(safe_path):
            return Response('File not found', status=404)

        return send_file(
            safe_path,
            as_attachment=True,
            download_name=os.path.basename(safe_path)
        )
    except Exception as e:
        logger.error(f'Download error: {str(e)}')
        return Response(f'Error: {str(e)}', status=500)

@app.route('/edit-audio', methods=['POST', 'OPTIONS'])
def edit_audio():
    """音声ファイルのカット編集"""
    # OPTIONSリクエスト（CORS preflight）への対応
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    try:
        data = request.json
        file_path = data.get('file_path', '')
        start_time = data.get('start_time', 0)
        end_time = data.get('end_time', 0)
        mode = data.get('mode', 'remove')  # 'remove' or 'extract'

        if not file_path:
            return jsonify({'error': 'File path is required'}), 400

        # セキュリティ: パストラバーサル対策
        base_dir = OUTPUT_DIR
        safe_path = os.path.normpath(os.path.join(base_dir, file_path))

        if not safe_path.startswith(base_dir):
            return jsonify({'error': 'Invalid file path'}), 400

        if not os.path.exists(safe_path):
            return jsonify({'error': 'File not found'}), 404

        # ファイル名と拡張子を分離
        file_dir = os.path.dirname(safe_path)
        file_name = os.path.basename(safe_path)
        name_without_ext, ext = os.path.splitext(file_name)

        # 出力ファイル名を生成
        if mode == 'remove':
            output_filename = f'{name_without_ext}_cut{ext}'
        else:  # extract
            output_filename = f'{name_without_ext}_extract{ext}'

        output_path = os.path.join(file_dir, output_filename)

        # ffmpegコマンドを構築
        if mode == 'remove':
            # 範囲を削除: 開始前の部分と終了後の部分を結合
            # 一時ファイルを作成
            temp1 = os.path.join(file_dir, f'temp1_{name_without_ext}{ext}')
            temp2 = os.path.join(file_dir, f'temp2_{name_without_ext}{ext}')
            concat_file = os.path.join(file_dir, f'concat_{name_without_ext}.txt')

            try:
                # 開始前の部分を抽出
                cmd1 = [
                    'ffmpeg', '-y', '-i', safe_path,
                    '-t', str(start_time),
                    '-c', 'copy',
                    temp1
                ]

                # 終了後の部分を抽出
                cmd2 = [
                    'ffmpeg', '-y', '-i', safe_path,
                    '-ss', str(end_time),
                    '-c', 'copy',
                    temp2
                ]

                # 実行
                subprocess.run(cmd1, check=True, capture_output=True)
                subprocess.run(cmd2, check=True, capture_output=True)

                # concatファイルを作成
                with open(concat_file, 'w') as f:
                    f.write(f"file '{temp1}'\n")
                    f.write(f"file '{temp2}'\n")

                # 結合
                cmd3 = [
                    'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                    '-i', concat_file,
                    '-c', 'copy',
                    output_path
                ]
                subprocess.run(cmd3, check=True, capture_output=True)

                # 一時ファイルを削除
                os.remove(temp1)
                os.remove(temp2)
                os.remove(concat_file)

            except Exception as e:
                # エラー時は一時ファイルをクリーンアップ
                for temp_file in [temp1, temp2, concat_file]:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                raise e

        else:  # extract
            # 範囲を抽出
            cmd = [
                'ffmpeg', '-y', '-i', safe_path,
                '-ss', str(start_time),
                '-to', str(end_time),
                '-c', 'copy',
                output_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        # 相対パスを返す
        relative_path = os.path.relpath(output_path, base_dir)

        logger.info(f'Audio edit completed: {output_filename}')

        return jsonify({
            'success': True,
            'output_file': output_filename,
            'output_path': relative_path
        })

    except subprocess.CalledProcessError as e:
        logger.error(f'FFmpeg error: {e.stderr.decode() if e.stderr else str(e)}')
        return jsonify({'error': 'Audio editing failed'}), 500
    except Exception as e:
        logger.error(f'Edit audio error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/rename-file', methods=['POST', 'OPTIONS'])
def rename_file():
    """ファイルをリネーム"""
    # OPTIONSリクエスト（CORS preflight）への対応
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    try:
        data = request.json
        file_path = data.get('file_path', '')
        new_name = data.get('new_name', '')

        if not file_path or not new_name:
            return jsonify({'error': 'File path and new name are required'}), 400

        # セキュリティ: パストラバーサル対策
        base_dir = OUTPUT_DIR
        safe_path = os.path.normpath(os.path.join(base_dir, file_path))

        if not safe_path.startswith(base_dir):
            return jsonify({'error': 'Invalid file path'}), 400

        if not os.path.exists(safe_path):
            return jsonify({'error': 'File not found'}), 404

        # 新しいファイル名のパスを構築
        file_dir = os.path.dirname(safe_path)
        new_path = os.path.join(file_dir, new_name)

        # 既に同じ名前のファイルが存在するかチェック
        if os.path.exists(new_path):
            return jsonify({'error': 'A file with that name already exists'}), 400

        # リネーム実行
        os.rename(safe_path, new_path)

        # 相対パスを返す
        relative_path = os.path.relpath(new_path, base_dir)

        logger.info(f'File renamed: {file_path} -> {new_name}')

        return jsonify({
            'success': True,
            'new_name': new_name,
            'new_path': relative_path
        })

    except Exception as e:
        logger.error(f'Rename file error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/files', methods=['GET'])
def list_files():
    """録音済みファイル一覧を取得（ルートフォルダのみ、virtual_folder_id=NULL）"""
    try:
        # DBからルートフォルダのファイルを取得（virtual_folder_id=NULL）
        files = db.get_files_in_folder(folder_id=None, limit=1000, offset=0)
        return jsonify({'files': files})
    except Exception as e:
        logger.error(f'List files error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/check-file', methods=['POST'])
def check_file_exists():
    """ファイルの存在チェック"""
    try:
        data = request.json
        title = data.get('title', '')
        rss = data.get('rss', '')
        start_time = data.get('start_time', '')

        # ファイルパスを構築
        output_dir = os.path.join(OUTPUT_DIR, rss)
        filename = f'{title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'
        file_path = os.path.join(output_dir, filename)

        exists = os.path.exists(file_path)
        relative_path = f'{rss}/{filename}' if exists else None

        return jsonify({
            'exists': exists,
            'path': relative_path,
            'filename': filename
        })
    except Exception as e:
        logger.error(f'Check file error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/cron/list', methods=['GET'])
def list_cron():
    """DBからcron予約を取得"""
    try:
        jobs = db.get_all_cron_jobs()

        # フロントエンド用にフォーマット
        cron_jobs = []
        for job in jobs:
            cron_jobs.append({
                'id': job['id'],
                'raw': f"{job['minute']} {job['hour']} {job['day_of_month']} {job['month']} {job['day_of_week']} {job['command']}",
                'minute': job['minute'],
                'hour': job['hour'],
                'dayOfWeek': job['day_of_week'],
                'command': job['command'],
                'title': job['title'],
                'station': job['station'],
                'startTime': job['start_time'],
                'endTime': job['end_time'],
                'virtual_folder_id': job.get('virtual_folder_id')
            })

        return jsonify({'cron_jobs': cron_jobs})

    except Exception as e:
        logger.error(f'List cron error: {str(e)}')
        return jsonify({'error': str(e)}), 500

def parse_cron_command(cron_line):
    """cronコマンドをパースして番組情報を抽出"""
    import re

    parts = cron_line.split(None, 5)  # 最初の5つのフィールド（cron式）とコマンド部分を分離

    if len(parts) < 6:
        return {
            'raw': cron_line,
            'minute': '',
            'hour': '',
            'dayOfWeek': '',
            'command': cron_line,
            'title': '',
            'station': '',
            'startTime': '',
            'endTime': ''
        }

    minute = parts[0]
    hour = parts[1]
    day_of_month = parts[2]
    month = parts[3]
    day_of_week = parts[4]
    command_part = parts[5]

    # myradikoコマンドのパターンマッチング
    # 新形式: /path/to/myradiko "番組名" "RSS" "放送局" "`date...`HHMM" "`date...`HHMM" "" "" ""
    # 引数1=タイトル, 引数2=RSS, 引数3=放送局, 引数4=開始時刻, 引数5=終了時刻
    pattern = r'([^\s]+)\s+"([^"]*)"\s+"([^"]*)"\s+"([^"]*)"\s+"[^"]*(\d{4})".*?"[^"]*(\d{4})"'
    match = re.search(pattern, command_part)

    title = ''
    station = ''
    start_time = ''
    end_time = ''

    if match:
        title = match.group(2)        # 引数1: タイトル
        # 引数2はRSSなので、引数3の放送局IDを使用
        station = match.group(4)      # 引数3: 放送局ID
        start_time = match.group(5)   # HHMM形式
        end_time = match.group(6)     # HHMM形式

    return {
        'raw': cron_line,
        'minute': minute,
        'hour': hour,
        'dayOfWeek': day_of_week,
        'command': command_part,
        'title': title,
        'station': station,
        'startTime': start_time,
        'endTime': end_time
    }

@app.route('/cron/add', methods=['POST'])
def add_cron():
    """DBに新しいcron予約を追加してスケジューラーに登録"""
    try:
        data = request.json
        cron_command = data.get('command', '')

        if not cron_command:
            return jsonify({'error': 'Command is required'}), 400

        # cronコマンドをパース
        parsed = parse_cron_command(cron_command)

        # コマンドからフォルダIDを抽出（第7引数）
        virtual_folder_id = None
        try:
            import re
            pattern = r'"([^"]*)"'
            matches = re.findall(pattern, parsed['command'])
            if len(matches) >= 7:
                folder_id_str = matches[6]  # 7番目の引数（0-indexed）
                if folder_id_str and folder_id_str != '':
                    try:
                        virtual_folder_id = int(folder_id_str)
                        logger.info(f'📁 Extracted folder_id from cron command: {virtual_folder_id}')
                    except (ValueError, TypeError):
                        logger.warning(f'⚠️ Invalid folder ID in cron command: {folder_id_str}')
        except Exception as e:
            logger.warning(f'⚠️ Failed to extract folder from cron command: {str(e)}')

        # DBに保存
        job_id = db.save_cron_job(
            minute=parsed['minute'],
            hour=parsed['hour'],
            day_of_month='*',
            month='*',
            day_of_week=parsed['dayOfWeek'],
            command=parsed['command'],
            title=parsed['title'],
            station=parsed['station'],
            start_time=parsed['startTime'],
            end_time=parsed['endTime'],
            virtual_folder_id=virtual_folder_id
        )

        if not job_id:
            return jsonify({'error': 'Failed to save cron job'}), 500

        # スケジューラーに登録
        try:
            # cron形式の曜日をAPScheduler形式に変換
            apscheduler_dow = convert_cron_dow_to_apscheduler(parsed['dayOfWeek'])

            logger.info(f"📝 Adding cron job to scheduler - ID: {job_id}")
            logger.info(f"📝 Schedule: minute={parsed['minute']}, hour={parsed['hour']}, dow={parsed['dayOfWeek']} -> {apscheduler_dow}")
            logger.info(f"📝 Command: {parsed['command'][:100]}")

            # メタデータを構築
            metadata = {
                'title': parsed['title'],
                'rss': parsed['station'],
                'station': parsed['station'],
                'start_time': parsed['startTime'],
                'end_time': parsed['endTime']
            }

            scheduler.add_job(
                func=execute_recording,
                trigger='cron',
                minute=parsed['minute'],
                hour=parsed['hour'],
                day='*',
                month='*',
                day_of_week=apscheduler_dow,
                args=[parsed['command'], job_id, 'cron', metadata],
                id=f"cron_{job_id}",
                replace_existing=True
            )
            logger.info(f"✅ Cron job added to scheduler successfully: cron_{job_id}")

            # スケジューラーの状態をログ
            all_jobs = scheduler.get_jobs()
            logger.info(f"📊 Total scheduled jobs: {len(all_jobs)}")

        except Exception as e:
            logger.error(f"❌ Failed to add job to scheduler: {str(e)}")
            logger.error(f"❌ Job details - minute:{parsed['minute']}, hour:{parsed['hour']}, dow:{parsed['dayOfWeek']}, cmd:{parsed['command'][:50]}")
            # DBからも削除
            db.delete_cron_job(job_id)
            return jsonify({'error': f'Failed to schedule job: {str(e)}'}), 500

        return jsonify({'success': True, 'message': 'Cron job added successfully', 'job_id': job_id})

    except Exception as e:
        logger.error(f'Add cron error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/cron/remove', methods=['POST'])
def remove_cron():
    """DBからcron予約を削除してスケジューラーからも削除"""
    try:
        data = request.json
        job_id = data.get('id')

        if not job_id:
            return jsonify({'error': 'Job ID is required'}), 400

        # DBから該当するジョブを検索
        jobs = db.get_all_cron_jobs()
        job_to_delete = None

        for job in jobs:
            if job['id'] == job_id:
                job_to_delete = job
                break

        if not job_to_delete:
            return jsonify({'error': 'Cron job not found'}), 404

        # スケジューラーから削除
        try:
            scheduler.remove_job(f"cron_{job_id}")
            logger.info(f"✅ Cron job removed from scheduler: {job_id}")
        except Exception as e:
            logger.warning(f"⚠️ Job not found in scheduler (may be already removed): {str(e)}")

        # DBから削除
        if db.delete_cron_job(job_id):
            return jsonify({'success': True, 'message': 'Cron job removed successfully'})
        else:
            return jsonify({'error': 'Failed to delete cron job from database'}), 500

    except Exception as e:
        logger.error(f'Remove cron error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/cron/logs', methods=['GET'])
def get_cron_logs():
    """cronのログを取得（実行サマリーのみ）"""
    try:
        summary_logs = []

        # myradiko実行ログを確認してサマリーを作成
        myradiko_log = '/tmp/myradiko_output.log'
        if os.path.exists(myradiko_log):
            try:
                with open(myradiko_log, 'r') as f:
                    content = f.read()

                # ログをセクションごとに分割（localeエラー以降がひとつの実行）
                sections = content.split('warning: setlocale:')

                # 最新10件の実行ログを解析
                recent_sections = sections[-11:-1] if len(sections) > 11 else sections[1:]

                for section in reversed(recent_sections):
                    # 成功/失敗を判定
                    if 'size=' in section and 'time=' in section:
                        # ファイルサイズと時間が記録されていれば成功
                        # ファイル名を抽出
                        lines = section.split('\n')
                        filename = '不明'
                        for line in lines:
                            if '.mp3' in line or '.m4a' in line:
                                # Output行からファイル名を抽出
                                if 'Output #0' in line or 'to ' in line:
                                    parts = line.split("'")
                                    if len(parts) >= 2:
                                        filename = parts[1].replace('.m4a', '.mp3')
                                        break

                        # サイズを抽出
                        size_match = section.split('size=')[-1].split()[0] if 'size=' in section else '不明'

                        summary_logs.append(f'✅ 成功: {filename} ({size_match})')
                    elif 'Error' in section or 'failed' in section.lower():
                        # エラーメッセージを抽出
                        error_lines = [l for l in section.split('\n') if 'Error' in l or 'failed' in l.lower()]
                        error_msg = error_lines[0] if error_lines else 'エラー発生'
                        summary_logs.append(f'❌ 失敗: {error_msg[:80]}')

            except Exception as e:
                logger.error(f'Error reading myradiko log: {str(e)}')

        # cronジョブ一覧も表示
        try:
            result = subprocess.run(['crontab', '-l'],
                                  capture_output=True,
                                  text=True)
            if result.returncode == 0 and result.stdout.strip():
                summary_logs.insert(0, '=== 登録されているcronジョブ ===')
                summary_logs.insert(1, result.stdout.strip())
                summary_logs.insert(2, '')
                summary_logs.insert(3, '=== 最近の実行結果 ===')
        except Exception as e:
            pass

        if not summary_logs:
            summary_logs = ['ログがありません。cronが実行されるとここに実行結果が表示されます。']

        return jsonify({'logs': summary_logs})

    except Exception as e:
        logger.error(f'Get cron logs error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/file/delete', methods=['POST'])
def delete_file():
    """録音ファイルを削除"""
    try:
        data = request.json
        filepath = data.get('path', '')

        if not filepath:
            return jsonify({'error': 'File path is required'}), 400

        # セキュリティ: パストラバーサル対策
        base_dir = OUTPUT_DIR
        safe_path = os.path.normpath(os.path.join(base_dir, filepath))

        if not safe_path.startswith(base_dir):
            return jsonify({'error': 'Invalid file path'}), 400

        # ファイルが存在する場合は物理削除
        file_existed = os.path.exists(safe_path)
        if file_existed:
            os.remove(safe_path)
            logger.info(f'File deleted: {safe_path}')
        else:
            logger.warning(f'File not found (will delete DB record only): {safe_path}')

        # DBからも削除（ファイルが存在しなくてもDBレコードは削除）
        db.delete_recorded_file(filepath)
        logger.info(f'File deleted from DB: {filepath}')

        message = 'File deleted successfully' if file_existed else 'DB record deleted (file not found)'
        return jsonify({'success': True, 'message': message, 'file_existed': file_existed})

    except Exception as e:
        logger.error(f'Delete file error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/files/delete-multiple', methods=['POST'])
def delete_multiple_files():
    """複数の録音ファイルを一括削除"""
    try:
        data = request.json
        filepaths = data.get('paths', [])

        if not filepaths or not isinstance(filepaths, list):
            return jsonify({'error': 'File paths array is required'}), 400

        base_dir = OUTPUT_DIR
        deleted = []
        errors = []

        for filepath in filepaths:
            try:
                # セキュリティ: パストラバーサル対策
                safe_path = os.path.normpath(os.path.join(base_dir, filepath))

                if not safe_path.startswith(base_dir):
                    errors.append({'path': filepath, 'error': 'Invalid file path'})
                    continue

                # ファイルが存在する場合は物理削除
                file_existed = os.path.exists(safe_path)
                if file_existed:
                    os.remove(safe_path)
                    logger.info(f'File deleted: {safe_path}')
                else:
                    logger.warning(f'File not found (will delete DB record only): {safe_path}')

                # DBからも削除（ファイルが存在しなくてもDBレコードは削除）
                db.delete_recorded_file(filepath)
                logger.info(f'File deleted from DB: {filepath}')

                deleted.append(filepath)

            except Exception as e:
                errors.append({'path': filepath, 'error': str(e)})
                logger.error(f'Failed to delete {filepath}: {str(e)}')

        return jsonify({
            'success': True,
            'deleted': deleted,
            'errors': errors,
            'message': f'{len(deleted)} files deleted successfully'
        })

    except Exception as e:
        logger.error(f'Delete multiple files error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/files/download-zip', methods=['POST'])
def download_zip():
    """複数のファイルをZIP形式でダウンロード"""
    try:
        data = request.json
        filepaths = data.get('paths', [])

        if not filepaths or not isinstance(filepaths, list):
            return jsonify({'error': 'File paths array is required'}), 400

        base_dir = OUTPUT_DIR

        # 一時ZIPファイルを作成
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_zip_path = temp_zip.name
        temp_zip.close()

        try:
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                added_files = 0
                for filepath in filepaths:
                    try:
                        # セキュリティ: パストラバーサル対策
                        safe_path = os.path.normpath(os.path.join(base_dir, filepath))

                        if not safe_path.startswith(base_dir):
                            logger.warning(f'Invalid file path: {filepath}')
                            continue

                        if not os.path.exists(safe_path):
                            logger.warning(f'File not found: {filepath}')
                            continue

                        # ZIPにファイルを追加（元のファイル名を保持）
                        arcname = os.path.basename(safe_path)
                        zipf.write(safe_path, arcname=arcname)
                        added_files += 1
                        logger.info(f'Added to ZIP: {arcname}')

                    except Exception as e:
                        logger.error(f'Failed to add {filepath} to ZIP: {str(e)}')

            if added_files == 0:
                os.unlink(temp_zip_path)
                return jsonify({'error': 'No valid files found'}), 404

            # ZIPファイルを送信
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            zip_filename = f'radiko_recordings_{timestamp}.zip'

            response = send_file(
                temp_zip_path,
                mimetype='application/zip',
                as_attachment=True,
                download_name=zip_filename
            )

            # 送信後にファイルを削除する
            @response.call_on_close
            def cleanup():
                try:
                    if os.path.exists(temp_zip_path):
                        os.unlink(temp_zip_path)
                        logger.info(f'Temp ZIP file deleted: {temp_zip_path}')
                except Exception as e:
                    logger.error(f'Failed to delete temp ZIP: {str(e)}')

            return response

        except Exception as e:
            # エラー時もZIPファイルを削除
            if os.path.exists(temp_zip_path):
                os.unlink(temp_zip_path)
            raise e

    except Exception as e:
        logger.error(f'Download ZIP error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/stream/<path:filepath>')
def stream_file(filepath):
    """音声ファイルをストリーミング配信"""
    try:
        # セキュリティ: パストラバーサル対策
        base_dir = OUTPUT_DIR
        safe_path = os.path.normpath(os.path.join(base_dir, filepath))

        if not safe_path.startswith(base_dir):
            return jsonify({'error': 'Invalid file path'}), 400

        if not os.path.exists(safe_path):
            return jsonify({'error': 'File not found'}), 404

        # ファイルの拡張子を確認
        ext = os.path.splitext(safe_path)[1].lower()

        # MIMEタイプを設定
        mime_types = {
            '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.wav': 'audio/wav'
        }

        mime_type = mime_types.get(ext, 'application/octet-stream')

        # ストリーミング配信（Range Request対応）
        return send_file(
            safe_path,
            mimetype=mime_type,
            as_attachment=False,
            conditional=True  # Range Request対応
        )

    except Exception as e:
        logger.error(f'Stream file error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/schedule-at', methods=['POST'])
def schedule_at():
    """DBにat予約を保存してスケジューラーに登録"""
    try:
        data = request.json
        script_path = data.get('script_path', SCRIPT_PATH)
        title = data.get('title', '')        # 番組名
        start_time = data.get('start_time')  # YYYYMMDDHHmm形式
        end_time = data.get('end_time')      # YYYYMMDDHHmm形式
        station_id = data.get('station_id')
        at_time = data.get('at_time')        # HH:MM YYYY-MM-DD形式
        folder = data.get('folder', '')      # 保存先フォルダ

        if not all([start_time, end_time, station_id, at_time]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # タイトルをサニタイズ（スペースをアンダーバーに、全角記号を半角に）
        safe_title = sanitize_filename(title)

        # cronと同じ形式のコマンドを生成（サニタイズしたタイトルを使用）
        command = f'{script_path} "{safe_title}" "{station_id}" "{station_id}" "{start_time}" "{end_time}" "" "{folder}" "" >> /tmp/myradiko_output.log 2>&1'

        # at_timeをdatetimeに変換 (HH:MM YYYY-MM-DD -> datetime)
        schedule_time_str = f"{at_time.split()[1]} {at_time.split()[0]}"  # YYYY-MM-DD HH:MM
        schedule_time = datetime.strptime(schedule_time_str, '%Y-%m-%d %H:%M')

        # 過去の時刻チェック
        now = datetime.now()
        if schedule_time < now:
            return jsonify({'error': 'Cannot schedule in the past'}), 400

        # DBに保存（job_idは自動生成）
        job_id = db.save_at_job(
            job_id=None,  # Auto-generate
            schedule_time=schedule_time.strftime('%Y-%m-%d %H:%M:%S'),
            command=command,
            title=title,
            station=station_id,
            start_time=start_time,
            end_time=end_time
        )

        if not job_id:
            return jsonify({'error': 'Failed to save at job to database'}), 500

        # metadataを構築
        metadata = {
            'title': title,
            'rss': station_id,
            'station': station_id,
            'start_time': start_time,
            'end_time': end_time
        }

        # スケジューラーに登録
        scheduler.add_job(
            func=execute_recording,
            trigger='date',
            run_date=schedule_time,
            args=[command, job_id, 'at', metadata],
            id=f"at_{job_id}",
            replace_existing=True
        )

        logger.info(f'✅ At job scheduled: {job_id} at {schedule_time}')

        return jsonify({
            'success': True,
            'message': 'at予約を登録しました',
            'job_id': job_id,
            'schedule_time': schedule_time.strftime('%Y-%m-%d %H:%M:%S')
        })

    except Exception as e:
        logger.error(f'Schedule at error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/list', methods=['GET'])
def list_at_jobs():
    """DBからat予約一覧を取得"""
    try:
        jobs_data = db.get_all_at_jobs()

        jobs = []
        for job in jobs_data:
            # schedule_timeをフォーマット (YYYY-MM-DD HH:MM:SS -> より読みやすい形式)
            try:
                schedule_dt = datetime.strptime(job['schedule_time'], '%Y-%m-%d %H:%M:%S')
                formatted_datetime = schedule_dt.strftime('%Y/%m/%d %a %H:%M')
            except:
                formatted_datetime = job['schedule_time']

            jobs.append({
                'id': str(job['id']),
                'datetime': formatted_datetime,
                'title': job.get('title', ''),
                'station': job.get('station', ''),
                'schedule_time': job['schedule_time']
            })

        return jsonify({'jobs': jobs})

    except Exception as e:
        logger.error(f'List at jobs error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/cancel/<job_id>', methods=['DELETE'])
def cancel_at_job(job_id):
    """DBからat予約を削除してスケジューラーからも削除"""
    try:
        # スケジューラーから削除
        try:
            scheduler.remove_job(f"at_{job_id}")
            logger.info(f"✅ At job removed from scheduler: {job_id}")
        except Exception as e:
            logger.warning(f"⚠️ Job not found in scheduler (may be already executed or removed): {str(e)}")

        # DBから削除
        if db.delete_at_job(int(job_id)):
            return jsonify({
                'success': True,
                'message': f'at予約 #{job_id} をキャンセルしました'
            })
        else:
            return jsonify({'error': 'Failed to delete at job from database'}), 500

    except Exception as e:
        logger.error(f'Cancel at job error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/detail/<job_id>', methods=['GET'])
def get_at_job_detail(job_id):
    """DBからat予約の詳細を取得"""
    try:
        jobs = db.get_all_at_jobs()

        job_detail = None
        for job in jobs:
            if str(job['id']) == str(job_id):
                job_detail = job
                break

        if not job_detail:
            return jsonify({'error': 'Job not found'}), 404

        return jsonify({
            'command': job_detail['command'],
            'title': job_detail.get('title', ''),
            'station': job_detail.get('station', ''),
            'start_time': job_detail.get('start_time', ''),
            'end_time': job_detail.get('end_time', ''),
            'schedule_time': job_detail['schedule_time']
        })

    except Exception as e:
        logger.error(f'Get at job detail error: {str(e)}')
        return jsonify({'error': str(e)}), 500

# ========================================
# 番組表DB関連API
# ========================================

@app.route('/programs/search', methods=['GET'])
def search_programs_api():
    """番組を検索"""
    try:
        keyword = request.args.get('keyword', '')
        area_id = request.args.get('area_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        logger.info(f'🔍 Search API called with keyword="{keyword}", area_id={area_id}, date_from={date_from}, date_to={date_to}')

        if not keyword:
            return jsonify({'error': 'keyword parameter is required'}), 400

        results = db.search_programs(keyword, area_id, date_from, date_to)
        logger.info(f'🔍 Search API returning {len(results)} results')

        return jsonify({
            'success': True,
            'count': len(results),
            'programs': results
        })

    except Exception as e:
        logger.error(f'Search API error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/programs/area/<area_id>/date/<date>', methods=['GET'])
def get_area_programs_api(area_id, date):
    """特定エリア・日付の番組を取得（DBになければradiko APIから取得）"""
    try:
        # 強制更新フラグ
        force_refresh = request.args.get('force', 'false').lower() == 'true'

        programs = db.get_programs_by_area_date(area_id, date)

        # 強制更新 または DBにデータがない場合、radiko APIから取得してDBに保存
        if force_refresh or len(programs) == 0:
            if force_refresh:
                logger.info(f'🔄 Force refresh for {area_id}/{date}, fetching from radiko API...')
            else:
                logger.info(f'📥 No data in DB for {area_id}/{date}, fetching from radiko API...')

            # radiko APIから取得
            fetched_programs = fetch_programs.fetch_area_programs(area_id, date)

            if fetched_programs:
                # DBに保存（既存データは削除される）
                db.save_programs(fetched_programs, area_id, date)
                logger.info(f'✅ Fetched and saved {len(fetched_programs)} programs for {area_id}/{date}')

                # 保存したデータを再取得してフォーマット
                programs = db.get_programs_by_area_date(area_id, date)
            else:
                logger.warning(f'⚠️ No programs found from radiko API for {area_id}/{date}')

        return jsonify({
            'success': True,
            'area_id': area_id,
            'date': date,
            'count': len(programs),
            'programs': programs
        })

    except Exception as e:
        logger.error(f'Get area programs API error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/programs/update/status', methods=['GET'])
def get_update_status_api():
    """番組表の更新ステータスを取得"""
    try:
        status = db.get_update_status()
        return jsonify(status)

    except Exception as e:
        logger.error(f'Get update status API error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/programs/update/trigger', methods=['POST'])
def trigger_update_api():
    """番組表の即時更新をトリガー"""
    try:
        logger.info('Manual update triggered via API')

        # バックグラウンドで実行（リクエストをブロックしない）
        scheduler.add_job(
            func=fetch_programs.update_all_areas,
            trigger='date',  # 即座に実行
            id='manual_update',
            name='Manual update',
            replace_existing=True
        )

        return jsonify({
            'success': True,
            'message': 'Update started in background'
        })

    except Exception as e:
        logger.error(f'Trigger update API error: {str(e)}')
        return jsonify({'error': str(e)}), 500


# ==================== 管理メニューAPI ====================

@app.route('/admin/update-programs-stream', methods=['GET'])
def admin_update_programs_stream():
    """管理画面からの番組表一括更新（SSEストリーミング）"""
    def generate():
        try:
            days = int(request.args.get('days', 3))
            logger.info(f'Admin: manual program update for {days} days (streaming)')

            # 進捗情報をストリーミング送信
            yield f"data: {json.dumps({'type': 'start', 'message': f'{days}日分の番組表更新を開始します...'})}\n\n"

            # fetch_programs.pyのALL_AREA_IDSを取得
            from fetch_programs import ALL_AREA_IDS
            from datetime import datetime, timedelta
            import time

            # 日付リストを生成（今日から指定日数分）
            today = datetime.now()
            if today.hour < 5:
                today = today - timedelta(days=1)

            dates = []
            for i in range(days):
                date = today + timedelta(days=i)
                date_str = date.strftime('%Y%m%d')
                dates.append(date_str)

            total_tasks = len(ALL_AREA_IDS) * len(dates)
            completed = 0
            total_programs = 0
            success_count = 0
            error_count = 0
            warning_count = 0

            yield f"data: {json.dumps({'type': 'info', 'message': f'全{len(ALL_AREA_IDS)}エリア × {len(dates)}日 = {total_tasks}件の処理'})}\n\n"

            # 各エリアを処理
            for idx, area_id in enumerate(ALL_AREA_IDS, 1):
                yield f"data: {json.dumps({'type': 'progress', 'area': area_id, 'current': idx, 'total': len(ALL_AREA_IDS)})}\n\n"

                for date in dates:
                    try:
                        programs = fetch_programs.fetch_area_programs(area_id, date)

                        if programs:
                            db.save_programs(programs, area_id, date)
                            total_programs += len(programs)
                            success_count += 1
                            yield f"data: {json.dumps({'type': 'success', 'area': area_id, 'date': date, 'programs': len(programs)})}\n\n"
                        else:
                            warning_count += 1
                            yield f"data: {json.dumps({'type': 'warning', 'area': area_id, 'date': date, 'message': 'No programs found'})}\n\n"

                        completed += 1
                        progress_percent = int((completed / total_tasks) * 100)
                        yield f"data: {json.dumps({'type': 'percent', 'percent': progress_percent, 'completed': completed, 'total': total_tasks, 'success': success_count, 'error': error_count, 'warning': warning_count})}\n\n"

                        time.sleep(0.2)  # レート制限対策

                    except Exception as e:
                        error_count += 1
                        error_msg = str(e)
                        # タイムアウトエラーをわかりやすく
                        if 'timed out' in error_msg or 'timeout' in error_msg.lower():
                            error_msg = 'タイムアウト (radikoサーバー応答なし)'
                        yield f"data: {json.dumps({'type': 'error', 'area': area_id, 'date': date, 'message': error_msg})}\n\n"
                        completed += 1
                        progress_percent = int((completed / total_tasks) * 100)
                        yield f"data: {json.dumps({'type': 'percent', 'percent': progress_percent, 'completed': completed, 'total': total_tasks, 'success': success_count, 'error': error_count, 'warning': warning_count})}\n\n"

            # 古いデータを削除
            yield f"data: {json.dumps({'type': 'info', 'message': '古いデータを削除中...'})}\n\n"
            db.cleanup_old_data(days_to_keep=15)

            # 完了
            yield f"data: {json.dumps({'type': 'complete', 'total_programs': total_programs, 'success': success_count, 'error': error_count, 'warning': warning_count, 'message': '更新が完了しました'})}\n\n"

        except Exception as e:
            logger.error(f'Admin update programs stream error: {str(e)}')
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/admin/logs/<log_type>')
def admin_view_logs(log_type):
    """ログファイルを表示"""
    try:
        log_content = ''

        if log_type == 'myradiko':
            # myradiko実行ログ
            log_path = '/tmp/myradiko_output.log'
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # 最新500行のみ
                    log_content = ''.join(lines[-500:])
            else:
                log_content = 'ログファイルが見つかりません'

        elif log_type == 'docker':
            # Flaskアプリのログ（このコンテナ内では取得不可）
            log_content = 'Dockerログはホスト側で `docker-compose logs proxy` コマンドを実行して確認してください。\n\n'
            log_content += 'コンテナ内からホストのDockerコマンドは実行できません。'

        elif log_type == 'nginx':
            # Nginxログ（このコンテナ内では取得不可）
            log_content = 'Nginxログはホスト側で `docker-compose logs web` コマンドを実行して確認してください。\n\n'
            log_content += 'または、docker exec コマンドでwebコンテナに入り、/var/log/nginx/以下のログを確認してください。'

        else:
            return jsonify({'error': 'Invalid log type'}), 400

        return jsonify({
            'success': True,
            'log': log_content
        })

    except Exception as e:
        logger.error(f'Admin view logs error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/execute-manual', methods=['POST'])
def admin_execute_manual():
    """手動でmyradikoコマンドを実行"""
    try:
        data = request.json
        script_path = data.get('script_path', SCRIPT_PATH)
        title = data.get('title', '')
        station_id = data.get('station_id')
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        if not all([station_id, start_time, end_time]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # タイトルをサニタイズ（スペースをアンダーバーに、全角記号を半角に）
        safe_title = sanitize_filename(title)

        # コマンドを構築（サニタイズしたタイトルを使用）
        command = f'{script_path} "{safe_title}" "{station_id}" "{station_id}" "{start_time}" "{end_time}" "" "" "" >> /tmp/myradiko_output.log 2>&1'

        logger.info(f'Admin: executing manual command: {command}')

        # コマンドを実行（同期実行）
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',  # エンコードエラーを置き換え文字で処理
            timeout=600  # 10分タイムアウト
        )

        output = result.stdout + result.stderr

        if result.returncode != 0:
            logger.error(f'Command failed: {output}')
            return jsonify({
                'success': False,
                'error': 'コマンド実行に失敗しました',
                'output': output
            }), 500

        logger.info(f'Command executed successfully: {output}')

        return jsonify({
            'success': True,
            'message': 'コマンドを実行しました',
            'output': output
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'コマンドがタイムアウトしました（10分以上）'}), 500
    except Exception as e:
        logger.error(f'Admin execute manual error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/cleanup', methods=['POST'])
def admin_cleanup():
    """古いDBデータを削除"""
    try:
        deleted = db.cleanup_old_data(days_to_keep=15)

        return jsonify({
            'success': True,
            'message': '古いデータを削除しました',
            'deleted': deleted
        })

    except Exception as e:
        logger.error(f'Admin cleanup error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/disk-space')
def admin_disk_space():
    """ディスク容量を確認"""
    try:
        # dfコマンドでディスク容量を取得
        result = subprocess.run(
            ['df', '-h', BASE_DIR],
            capture_output=True,
            text=True,
            timeout=5
        )

        return jsonify({
            'success': True,
            'info': result.stdout
        })

    except Exception as e:
        logger.error(f'Admin disk space error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/db-status')
def admin_db_status():
    """DB統計情報を取得"""
    try:
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        cursor = conn.cursor()

        # 総番組数
        cursor.execute('SELECT COUNT(*) FROM programs')
        total_programs = cursor.fetchone()[0]

        # 更新履歴数
        cursor.execute('SELECT COUNT(*) FROM update_log')
        total_updates = cursor.fetchone()[0]

        # DBファイルサイズ
        db_size = os.path.getsize(db.DB_PATH)
        db_size_mb = round(db_size / 1024 / 1024, 2)

        conn.close()

        return jsonify({
            'success': True,
            'total_programs': total_programs,
            'total_updates': total_updates,
            'db_size': f'{db_size_mb} MB'
        })

    except Exception as e:
        logger.error(f'Admin DB status error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/admin/cleanup-orphaned-records', methods=['POST'])
def cleanup_orphaned_records():
    """物理ファイルが存在しないDBレコードを削除"""
    try:
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        cursor = conn.cursor()

        # 全ての録音ファイルレコードを取得
        cursor.execute('SELECT id, file_path FROM recorded_files')
        all_records = cursor.fetchall()

        orphaned = []
        cleaned = []

        for record_id, file_path in all_records:
            if not file_path:
                continue

            full_path = os.path.join(OUTPUT_DIR, file_path)

            # ファイルが存在しない場合
            if not os.path.exists(full_path):
                orphaned.append({
                    'id': record_id,
                    'path': file_path
                })

                # DBから削除
                cursor.execute('DELETE FROM recorded_files WHERE id = ?', (record_id,))
                cleaned.append(file_path)
                logger.info(f'Orphaned record cleaned: {file_path}')

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'orphaned_count': len(orphaned),
            'cleaned': cleaned,
            'message': f'{len(cleaned)} orphaned records cleaned'
        })

    except Exception as e:
        logger.error(f'Cleanup orphaned records error: {str(e)}')
        return jsonify({'error': str(e)}), 500


# ========================================
# アートワーク管理API
# ========================================

@app.route('/artwork/upload', methods=['POST'])
def upload_artwork():
    """アートワークをアップロード"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        if 'title' not in request.form:
            return jsonify({'error': 'No title provided'}), 400

        file = request.files['file']
        title = request.form['title']
        artist = request.form.get('artist', '')  # アーティスト名（オプション）

        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # ファイルタイプチェック
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp']
        mime_type = file.content_type

        if mime_type not in allowed_types:
            return jsonify({'error': f'Invalid file type: {mime_type}'}), 400

        # ファイルデータを読み込み
        image_data = file.read()

        # DBに保存
        success = db.save_artwork(title, image_data, mime_type)

        if not success:
            return jsonify({'error': 'Failed to save artwork'}), 500

        # 該当する番組タイトルのMP3ファイルを検索してアートワークを埋め込む
        embedded_count = 0
        failed_count = 0

        # 番組タイトルから抽出した名前でファイルを検索
        program_title_pattern = title.replace('_', ' ')  # アンダーバーをスペースに戻す

        for root, dirs, files in os.walk(OUTPUT_DIR):
            for filename in files:
                if filename.endswith('.mp3'):
                    # ファイル名から番組名を抽出
                    name_without_ext = filename.replace('.mp3', '')
                    # 日付部分を削除
                    import re
                    file_program_name = re.sub(r'\(\d{4}\.\d{2}\.\d{2}\)$', '', name_without_ext).strip()

                    # 番組タイトルとマッチするか確認
                    if file_program_name == title or file_program_name == program_title_pattern:
                        file_path = os.path.join(root, filename)
                        logger.info(f'Embedding artwork to: {file_path}')

                        if embed_artwork_to_mp3(file_path, image_data, mime_type, title=title, artist=artist if artist else None):
                            embedded_count += 1
                        else:
                            failed_count += 1

        logger.info(f'Artwork embedded: {embedded_count} files, failed: {failed_count} files')

        return jsonify({
            'success': True,
            'message': f'Artwork uploaded for: {title}',
            'embedded': embedded_count,
            'failed': failed_count
        })

    except Exception as e:
        logger.error(f'Upload artwork error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/artwork/<path:title>', methods=['GET'])
def get_artwork(title):
    """タイトルに対応するアートワークを取得"""
    try:
        artwork = db.get_artwork(title)

        if artwork:
            from io import BytesIO
            return send_file(
                BytesIO(artwork['image_data']),
                mimetype=artwork['mime_type'],
                as_attachment=False
            )
        else:
            # アートワークが登録されていない場合はデフォルト(__DEFAULT__)を返す
            default_artwork = db.get_artwork('__DEFAULT__')
            if default_artwork:
                from io import BytesIO
                return send_file(
                    BytesIO(default_artwork['image_data']),
                    mimetype=default_artwork['mime_type'],
                    as_attachment=False
                )
            else:
                # __DEFAULT__も存在しない場合（起動直後など）はファイルから返す
                return send_file('img/jacket.png', mimetype='image/png')

    except Exception as e:
        logger.error(f'Get artwork error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/artwork/list', methods=['GET'])
def list_artworks():
    """登録されているアートワーク一覧を取得"""
    try:
        artworks = db.list_artworks()
        return jsonify({'success': True, 'artworks': artworks})

    except Exception as e:
        logger.error(f'List artworks error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/artwork/delete', methods=['POST'])
def delete_artwork():
    """アートワークを削除"""
    try:
        data = request.json
        title = data.get('title')

        if not title:
            return jsonify({'error': 'No title provided'}), 400

        success = db.delete_artwork(title)

        if success:
            return jsonify({'success': True, 'message': f'Artwork deleted for: {title}'})
        else:
            return jsonify({'error': 'Artwork not found'}), 404

    except Exception as e:
        logger.error(f'Delete artwork error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/batch-update-metadata', methods=['POST'])
def batch_update_metadata():
    """すべての録音ファイルのメタデータを一括更新"""
    try:
        # DBから全ての録音ファイルを取得
        import sqlite3
        conn = sqlite3.connect(db.DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT file_path, program_title, station_name
            FROM recorded_files
            WHERE file_path IS NOT NULL
        ''')

        files = cursor.fetchall()
        conn.close()

        processed = 0
        success_count = 0
        failed_count = 0
        skipped_count = 0
        results = []

        for file_path, program_title, station_name in files:
            processed += 1
            full_path = os.path.join(OUTPUT_DIR, file_path)

            # ファイルが存在しない場合はスキップ
            if not os.path.exists(full_path):
                skipped_count += 1
                results.append({
                    'file': file_path,
                    'success': False,
                    'message': 'File not found'
                })
                continue

            # MP3ファイルのみ処理
            if not full_path.endswith('.mp3'):
                skipped_count += 1
                results.append({
                    'file': file_path,
                    'success': False,
                    'message': 'Not an MP3 file'
                })
                continue

            # メタデータを埋め込む
            result = embed_metadata_after_recording(
                full_path,
                program_title or '',
                station_name or ''
            )

            if result:
                success_count += 1
                results.append({
                    'file': file_path,
                    'success': True,
                    'message': 'Metadata updated'
                })
            else:
                failed_count += 1
                results.append({
                    'file': file_path,
                    'success': False,
                    'message': 'Failed to update metadata'
                })

        logger.info(f'Batch metadata update: processed={processed}, success={success_count}, failed={failed_count}, skipped={skipped_count}')

        return jsonify({
            'success': True,
            'processed': processed,
            'success_count': success_count,
            'failed_count': failed_count,
            'skipped_count': skipped_count,
            'results': results
        })

    except Exception as e:
        logger.error(f'Batch update metadata error: {str(e)}')
        return jsonify({'error': str(e)}), 500


def extract_metadata_from_filename(filename, filepath):
    """ファイル名からメタデータを抽出

    想定フォーマット: 番組名(YYYY.MM.DD).mp3 または 番組名_局_説明(YYYY.MM.DD).mp3
    filepath例: JOAK-FM/番組名(2025.10.29).mp3
    """
    import re
    from datetime import datetime as dt

    # 局IDをファイルパスから抽出
    station_id = None
    if '/' in filepath:
        station_id = filepath.split('/')[0]

    # 拡張子を除去
    name_without_ext = filename.replace('.mp3', '').replace('.m4a', '').replace('.aac', '')

    # 放送日を抽出: (YYYY.MM.DD) または (YYYY-MM-DD) または _YYYY-MM-DD
    date_pattern = r'[\(\_](\d{4})[\.\-](\d{2})[\.\-](\d{2})[\)\_]?'
    date_match = re.search(date_pattern, name_without_ext)

    broadcast_date = None
    if date_match:
        year, month, day = date_match.groups()
        broadcast_date = f'{year}-{month}-{day}'
        # 日付部分を除去して番組タイトルを抽出
        program_title = re.sub(date_pattern, '', name_without_ext).strip('_- ')
    else:
        program_title = name_without_ext

    return {
        'program_title': program_title,
        'station_id': station_id,
        'broadcast_date': broadcast_date
    }


@app.route('/files/scan', methods=['POST'])
def scan_and_register_files():
    """既存の録音ファイルをスキャンしてDBに登録"""
    from datetime import datetime

    try:
        base_dir = OUTPUT_DIR
        registered = 0
        updated = 0
        errors = []

        if not os.path.exists(base_dir):
            return jsonify({'error': 'Output directory not found'}), 404

        # ファイルシステムをスキャン
        for root, dirs, filenames in os.walk(base_dir):
            for filename in filenames:
                if filename.endswith(('.mp3', '.m4a', '.aac')):
                    try:
                        full_path = os.path.join(root, filename)
                        relative_path = os.path.relpath(full_path, base_dir)
                        file_stat = os.stat(full_path)

                        # ファイル名からメタデータを抽出
                        metadata = extract_metadata_from_filename(filename, relative_path)

                        # DBに登録
                        file_id = db.register_recorded_file(
                            file_path=relative_path,
                            file_name=filename,
                            program_title=metadata['program_title'],
                            station_id=metadata['station_id'],
                            station_name=None,  # 後で追加可能
                            broadcast_date=metadata['broadcast_date'],
                            start_time=None,
                            end_time=None,
                            file_size=file_stat.st_size,
                            duration=None,  # 後で追加可能
                            file_modified=datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                        )

                        if file_id:
                            # 既存レコードの更新か新規登録かを判定
                            existing = db.get_recorded_file_by_path(relative_path)
                            if existing and existing['id'] != file_id:
                                updated += 1
                            else:
                                registered += 1

                    except Exception as e:
                        errors.append({'file': filename, 'error': str(e)})
                        logger.error(f'Failed to register file {filename}: {str(e)}')

        return jsonify({
            'success': True,
            'registered': registered,
            'updated': updated,
            'total': registered + updated,
            'errors': errors
        })

    except Exception as e:
        logger.error(f'Scan files error: {str(e)}')
        return jsonify({'error': str(e)}), 500


# ========================================
# 仮想フォルダ管理API
# ========================================

@app.route('/folders', methods=['GET'])
def list_folders():
    """仮想フォルダ一覧を取得"""
    try:
        folders = db.get_all_virtual_folders()
        return jsonify({'success': True, 'folders': folders})
    except Exception as e:
        logger.error(f'List folders error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/folders', methods=['POST'])
def create_folder():
    """仮想フォルダを作成"""
    try:
        data = request.json
        name = data.get('name')
        if not name:
            return jsonify({'error': 'フォルダ名が必要です'}), 400

        parent_id = data.get('parent_id')
        color = data.get('color')
        icon = data.get('icon')

        folder_id = db.create_virtual_folder(name, parent_id, color, icon)
        return jsonify({'success': True, 'folder_id': folder_id})
    except Exception as e:
        logger.error(f'Create folder error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/folders/<int:folder_id>', methods=['PUT'])
def update_folder(folder_id):
    """仮想フォルダを更新"""
    try:
        data = request.json
        success = db.update_virtual_folder(
            folder_id,
            name=data.get('name'),
            color=data.get('color'),
            icon=data.get('icon'),
            parent_id=data.get('parent_id')
        )
        return jsonify({'success': success})
    except Exception as e:
        logger.error(f'Update folder error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/folders/<int:folder_id>', methods=['DELETE'])
def delete_folder(folder_id):
    """仮想フォルダを削除"""
    try:
        success = db.delete_virtual_folder(folder_id)
        return jsonify({'success': success})
    except Exception as e:
        logger.error(f'Delete folder error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/files/move', methods=['POST'])
def move_file():
    """ファイルを仮想フォルダに移動"""
    try:
        data = request.json
        file_path = data.get('file_path')
        folder_id = data.get('folder_id')

        if not file_path:
            return jsonify({'error': 'ファイルパスが必要です'}), 400

        success = db.move_file_to_folder(file_path, folder_id)
        return jsonify({'success': success})
    except Exception as e:
        logger.error(f'Move file error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/folders/<int:folder_id>/files', methods=['GET'])
def get_folder_files(folder_id):
    """仮想フォルダ内のファイルを取得"""
    try:
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 1000, type=int)
        offset = (page - 1) * limit

        files = db.get_files_in_folder(folder_id, limit, offset)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        logger.error(f'Get folder files error: {str(e)}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # 開発環境用（本番ではgunicornを使用）
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
