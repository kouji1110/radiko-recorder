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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
