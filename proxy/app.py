from flask import Flask, Response, request, jsonify, stream_with_context, send_file
import requests
from flask_cors import CORS
import logging
import subprocess
import json
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import zipfile
import tempfile

# DBモジュールをインポート
import db
import fetch_programs

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 日本語などの非ASCII文字をそのまま出力
CORS(app, resources={r"/*": {"origins": "*"}})

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# パス設定（Docker環境用）
BASE_DIR = os.environ.get('BASE_DIR', '/app')
SCRIPT_PATH = os.path.join(BASE_DIR, 'script/myradiko')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output/radio')

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

@app.route('/execute', methods=['POST', 'OPTIONS'])
def execute_recording():
    """録音コマンドを実行してログをストリーミング"""
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

    # タイトルをサニタイズ（スペースをアンダーバーに、全角記号を半角に）
    safe_title = sanitize_filename(title)

    # デバッグ用ログ
    logger.info(f'Original title: {title}')
    logger.info(f'Sanitized title: {safe_title}')

    def generate_log():
        """ログをストリーミングで返す"""
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

        # 開始ログ
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] コマンド実行開始..."})}\n\n'
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] 元のタイトル: {title}"})}\n\n'
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] サニタイズ後: {safe_title}"})}\n\n'

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
            '',  # DIR
            ''   # MAIL
        ]

        cmd_str = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in cmd])
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] {cmd_str}"})}\n\n'

        try:
            # プロセスを起動
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding='utf-8',
                errors='replace',  # エンコードエラーを置き換え文字で処理
                bufsize=1
            )

            # 出力を逐次送信
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                    yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] {line}"})}\n\n'

            # プロセスの終了を待つ
            process.wait()

            timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            if process.returncode == 0:
                # ファイルパスを構築（サニタイズしたタイトルを使用）
                output_dir = os.path.join(OUTPUT_DIR, rss)
                filename = f'{safe_title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'
                file_path = os.path.join(output_dir, filename)

                # ファイルが存在するか確認
                if os.path.exists(file_path):
                    # 相対パスを生成（ダウンロードURL用）
                    relative_path = f'{rss}/{filename}'
                    yield f'data: {json.dumps({"type": "success", "message": f"[{timestamp}] 実行完了！", "file": relative_path})}\n\n'
                else:
                    yield f'data: {json.dumps({"type": "success", "message": f"[{timestamp}] 実行完了！（ファイルが見つかりません）"})}\n\n'
            else:
                yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] エラーが発生しました (終了コード: {process.returncode})"})}\n\n'

        except Exception as e:
            timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            yield f'data: {json.dumps({"type": "error", "message": f"[{timestamp}] エラー: {str(e)}"})}\n\n'

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

@app.route('/files', methods=['GET'])
def list_files():
    """録音済みファイル一覧を取得"""
    try:
        base_dir = OUTPUT_DIR
        files = []

        if not os.path.exists(base_dir):
            return jsonify({'files': []})

        # ディレクトリを再帰的に探索
        for root, dirs, filenames in os.walk(base_dir):
            for filename in filenames:
                if filename.endswith('.mp3'):
                    full_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(full_path, base_dir)
                    file_stat = os.stat(full_path)

                    files.append({
                        'path': relative_path,
                        'name': filename,
                        'size': file_stat.st_size,
                        'modified': file_stat.st_mtime
                    })

        # 更新日時でソート（新しい順）
        files.sort(key=lambda x: x['modified'], reverse=True)

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
    """現在のcrontabを取得してパース"""
    try:
        result = subprocess.run(['crontab', '-l'],
                              capture_output=True,
                              text=True)

        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            cron_jobs = []

            for line in lines:
                if line and not line.startswith('#'):
                    parsed = parse_cron_command(line)
                    cron_jobs.append(parsed)

            return jsonify({'cron_jobs': cron_jobs})
        else:
            return jsonify({'cron_jobs': []})

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
    """crontabに新しいジョブを追加"""
    try:
        data = request.json
        cron_command = data.get('command', '')

        if not cron_command:
            return jsonify({'error': 'Command is required'}), 400

        # 現在のcrontabを取得
        result = subprocess.run(['crontab', '-l'],
                              capture_output=True,
                              text=True)

        current_crontab = result.stdout if result.returncode == 0 else ''

        # 重複チェック
        if cron_command in current_crontab:
            return jsonify({'error': 'This cron job already exists'}), 400

        # 新しいcronジョブを追加
        new_crontab = current_crontab.rstrip('\n') + '\n' + cron_command + '\n'

        # crontabを更新
        process = subprocess.Popen(['crontab', '-'],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 text=True)

        stdout, stderr = process.communicate(input=new_crontab)

        if process.returncode == 0:
            return jsonify({'success': True, 'message': 'Cron job added successfully'})
        else:
            return jsonify({'error': stderr}), 500

    except Exception as e:
        logger.error(f'Add cron error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/cron/remove', methods=['POST'])
def remove_cron():
    """crontabからジョブを削除"""
    try:
        data = request.json
        cron_command = data.get('command', '')

        if not cron_command:
            return jsonify({'error': 'Command is required'}), 400

        # 現在のcrontabを取得
        result = subprocess.run(['crontab', '-l'],
                              capture_output=True,
                              text=True)

        if result.returncode != 0:
            return jsonify({'error': 'No crontab found'}), 404

        current_crontab = result.stdout
        lines = current_crontab.split('\n')

        # 指定されたコマンドを除外
        new_lines = [line for line in lines if line.strip() != cron_command.strip()]
        new_crontab = '\n'.join(new_lines)

        # crontabを更新
        process = subprocess.Popen(['crontab', '-'],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 text=True)

        stdout, stderr = process.communicate(input=new_crontab)

        if process.returncode == 0:
            return jsonify({'success': True, 'message': 'Cron job removed successfully'})
        else:
            return jsonify({'error': stderr}), 500

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

        if not os.path.exists(safe_path):
            return jsonify({'error': 'File not found'}), 404

        # ファイルを削除
        os.remove(safe_path)
        logger.info(f'File deleted: {safe_path}')

        return jsonify({'success': True, 'message': 'File deleted successfully'})

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

                if not os.path.exists(safe_path):
                    errors.append({'path': filepath, 'error': 'File not found'})
                    continue

                # ファイルを削除
                os.remove(safe_path)
                deleted.append(filepath)
                logger.info(f'File deleted: {safe_path}')

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
    """at予約を登録"""
    try:
        data = request.json
        script_path = data.get('script_path', SCRIPT_PATH)
        title = data.get('title', '')        # 番組名
        start_time = data.get('start_time')  # YYYYMMDDHHmm形式
        end_time = data.get('end_time')      # YYYYMMDDHHmm形式
        station_id = data.get('station_id')
        at_time = data.get('at_time')        # HH:MM YYYY-MM-DD形式

        if not all([start_time, end_time, station_id, at_time]):
            return jsonify({'error': 'Missing required parameters'}), 400

        # タイトルをサニタイズ（スペースをアンダーバーに、全角記号を半角に）
        safe_title = sanitize_filename(title)

        # cronと同じ形式のコマンドを生成（サニタイズしたタイトルを使用）
        command = f'{script_path} "{safe_title}" "{station_id}" "{station_id}" "{start_time}" "{end_time}" "" "" "" >> /tmp/myradiko_output.log 2>&1'
        at_command = f"echo '{command}' | at {at_time}"

        logger.info(f'Scheduling at job: {at_command}')

        result = subprocess.run(
            at_command,
            shell=True,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            logger.error(f'at command failed: {result.stderr}')
            return jsonify({'error': result.stderr}), 500

        logger.info(f'at job scheduled successfully: {result.stdout}')

        return jsonify({
            'success': True,
            'message': 'at予約を登録しました',
            'output': result.stdout
        })

    except Exception as e:
        logger.error(f'Schedule at error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/list', methods=['GET'])
def list_at_jobs():
    """at予約一覧を取得"""
    try:
        result = subprocess.run(
            ['atq'],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            logger.error(f'atq command failed: {result.stderr}')
            return jsonify({'jobs': []})

        jobs = []
        for line in result.stdout.strip().split('\n'):
            if line:
                # atqの出力形式: job_id date time queue user
                # 例: 1	Thu Oct 24 00:00:00 2025 a root
                parts = line.split()
                if len(parts) >= 6:
                    job_id = parts[0]
                    weekday = parts[1]
                    month = parts[2]
                    day = parts[3]
                    time = parts[4]
                    year = parts[5]

                    jobs.append({
                        'id': job_id,
                        'datetime': f'{year}/{month}/{day} {weekday} {time}',
                        'raw': line
                    })

        return jsonify({'jobs': jobs})

    except Exception as e:
        logger.error(f'List at jobs error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/cancel/<job_id>', methods=['DELETE'])
def cancel_at_job(job_id):
    """at予約をキャンセル"""
    try:
        result = subprocess.run(
            ['atrm', job_id],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            logger.error(f'atrm command failed: {result.stderr}')
            return jsonify({'error': result.stderr}), 500

        logger.info(f'at job {job_id} cancelled successfully')

        return jsonify({
            'success': True,
            'message': f'at予約 #{job_id} をキャンセルしました'
        })

    except Exception as e:
        logger.error(f'Cancel at job error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/at/detail/<job_id>', methods=['GET'])
def get_at_job_detail(job_id):
    """at予約の詳細を取得"""
    try:
        result = subprocess.run(
            ['at', '-c', job_id],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            logger.error(f'at -c command failed: {result.stderr}')
            return jsonify({'error': result.stderr}), 500

        # コマンド部分を抽出（最後の行がコマンド）
        lines = result.stdout.strip().split('\n')
        command = ''
        for line in reversed(lines):
            if line and not line.startswith('#') and 'myradiko' in line:
                command = line
                break

        return jsonify({
            'command': command,
            'full_output': result.stdout
        })

    except Exception as e:
        logger.error(f'Get at job detail error: {str(e)}')
        return jsonify({'error': str(e)}), 500

# ========================================
# 番組表DB関連API
# ========================================

@app.route('/api/programs/search', methods=['GET'])
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


@app.route('/api/programs/area/<area_id>/date/<date>', methods=['GET'])
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


@app.route('/api/programs/update/status', methods=['GET'])
def get_update_status_api():
    """番組表の更新ステータスを取得"""
    try:
        status = db.get_update_status()
        return jsonify(status)

    except Exception as e:
        logger.error(f'Get update status API error: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/programs/update/trigger', methods=['POST'])
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
