from flask import Flask, Response, request, jsonify, stream_with_context, send_file
import requests
from flask_cors import CORS
import logging
import subprocess
import json
import os
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

    def generate_log():
        """ログをストリーミングで返す"""
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

        # 開始ログ
        yield f'data: {json.dumps({"type": "log", "message": f"[{timestamp}] コマンド実行開始..."})}\n\n'

        # myradikoスクリプトのパス
        script_path = '/home/sites/radiko-recorder/script/myradiko'

        # コマンド構築
        cmd = [
            script_path,
            title,
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
                universal_newlines=True,
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
                # ファイルパスを構築
                output_dir = f'/home/sites/radiko-recorder/output/radio/{rss}'
                filename = f'{title}({start_time[:4]}.{start_time[4:6]}.{start_time[6:8]}).mp3'
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
        base_dir = '/home/sites/radiko-recorder/output/radio'
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
        base_dir = '/home/sites/radiko-recorder/output/radio'
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
        output_dir = f'/home/sites/radiko-recorder/output/radio/{rss}'
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
    """cronのログを取得"""
    try:
        logs = []

        # myradiko実行ログを確認
        myradiko_log = '/tmp/myradiko_output.log'
        if os.path.exists(myradiko_log):
            try:
                with open(myradiko_log, 'r') as f:
                    lines = f.readlines()
                    # 最新100行
                    recent_lines = lines[-100:] if len(lines) > 100 else lines
                    logs.extend([line.rstrip() for line in recent_lines if line.strip()])
            except Exception as e:
                logger.error(f'Error reading myradiko log: {str(e)}')

        # システムのcronログも確認
        system_log_files = [
            '/var/log/cron.log',
            '/var/log/syslog',
            '/var/log/messages'
        ]

        for log_file in system_log_files:
            if os.path.exists(log_file):
                try:
                    result = subprocess.run(
                        ['grep', '-i', 'cron', log_file],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        logs.extend(lines[-50:])
                except Exception as e:
                    logger.error(f'Error reading {log_file}: {str(e)}')
                    continue

        # cronジョブ一覧も表示
        try:
            result = subprocess.run(['crontab', '-l'],
                                  capture_output=True,
                                  text=True)
            if result.returncode == 0 and result.stdout.strip():
                logs.insert(0, '=== 登録されているcronジョブ ===')
                logs.insert(1, result.stdout.strip())
                logs.insert(2, '')
        except Exception as e:
            pass

        if not logs:
            logs = ['ログがありません。cronが実行されるとここにログが表示されます。']

        return jsonify({'logs': logs})

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
        base_dir = '/home/sites/radiko-recorder/output/radio'
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
