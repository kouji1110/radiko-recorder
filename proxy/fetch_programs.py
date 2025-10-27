"""
番組表を取得してDBに保存するバッチ処理
APSchedulerで30分ごとに実行
"""
import requests
import logging
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
import db
import time

logger = logging.getLogger(__name__)

# 全エリアID
ALL_AREA_IDS = [
    'JP1', 'JP2', 'JP3', 'JP4', 'JP5', 'JP6', 'JP7', 'JP8', 'JP9', 'JP10',
    'JP11', 'JP12', 'JP13', 'JP14', 'JP15', 'JP16', 'JP17', 'JP18', 'JP19', 'JP20',
    'JP21', 'JP22', 'JP23', 'JP24', 'JP25', 'JP26', 'JP27', 'JP28', 'JP29', 'JP30',
    'JP31', 'JP32', 'JP33', 'JP34', 'JP35', 'JP36', 'JP37', 'JP38', 'JP39', 'JP40',
    'JP41', 'JP42', 'JP43', 'JP44', 'JP45', 'JP46', 'JP47'
]


def parse_radiko_time(time_str: str) -> str:
    """
    radikoの時刻文字列（YYYYMMDDHHmmss）をISO形式に変換
    """
    try:
        dt = datetime.strptime(time_str, '%Y%m%d%H%M%S')
        return dt.isoformat()
    except Exception:
        return time_str


def fetch_area_programs(area_id: str, date: str) -> list:
    """
    特定エリア・日付の番組表を取得
    date: YYYYMMDD形式
    """
    programs = []

    try:
        # まず放送局一覧を取得
        now_url = f'http://radiko.jp/v3/program/now/{area_id}.xml'
        logger.info(f'Fetching stations for {area_id}...')

        now_response = requests.get(now_url, timeout=30)
        if not now_response.ok:
            logger.warning(f'Failed to fetch stations for {area_id}: {now_response.status_code}')
            return programs

        now_xml = ET.fromstring(now_response.content)
        stations = now_xml.findall('.//station')

        logger.info(f'Found {len(stations)} stations for {area_id}')

        # 各放送局の番組表を取得
        for station in stations:
            station_id = station.get('id')
            station_name_elem = station.find('name')
            station_name = station_name_elem.text if station_name_elem is not None else 'Unknown'

            try:
                station_url = f'http://radiko.jp/v3/program/station/date/{date}/{station_id}.xml'
                station_response = requests.get(station_url, timeout=30)

                if not station_response.ok:
                    continue

                station_xml = ET.fromstring(station_response.content)
                progs = station_xml.findall('.//prog')

                for prog in progs:
                    title_elem = prog.find('title')
                    desc_elem = prog.find('desc')
                    pfm_elem = prog.find('pfm')
                    info_elem = prog.find('info')
                    url_elem = prog.find('url')

                    ft_str = prog.get('ft')
                    to_str = prog.get('to')

                    if not ft_str or not to_str:
                        continue

                    programs.append({
                        'stationId': station_id,
                        'stationName': station_name,
                        'title': title_elem.text if title_elem is not None else '',
                        'ft': parse_radiko_time(ft_str),
                        'to': parse_radiko_time(to_str),
                        'desc': desc_elem.text if desc_elem is not None else '',
                        'pfm': pfm_elem.text if pfm_elem is not None else '',
                        'info': info_elem.text if info_elem is not None else '',
                        'url': url_elem.text if url_elem is not None else ''
                    })

                # レート制限対策
                time.sleep(0.1)

            except Exception as e:
                logger.warning(f'Error fetching {station_id}: {str(e)}')
                continue

    except Exception as e:
        logger.error(f'Error fetching programs for {area_id} on {date}: {str(e)}')

    return programs


def update_all_areas(days=7):
    """
    全エリアの番組表を更新

    Args:
        days: 取得する日数（デフォルト: 7）
              過去days日間 + 今日 + 未来days日間を取得
    """
    logger.info('=' * 60)
    logger.info(f'Starting program data update for all areas ({days} days range)')
    logger.info('=' * 60)

    start_time = time.time()

    # 日付リストを生成（朝5時基準）
    now = datetime.now()
    today = datetime.now()

    # 朝5時未満の場合は前日として扱う
    if today.hour < 5:
        today = today - timedelta(days=1)

    dates = []
    for i in range(-days, days + 1):  # -days日〜+days日
        date = today + timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        dates.append(date_str)

    logger.info(f'Date range: {dates[0]} to {dates[-1]} ({len(dates)} days)')

    total_programs = 0
    success_count = 0
    error_count = 0

    # 各エリアを処理
    for idx, area_id in enumerate(ALL_AREA_IDS, 1):
        logger.info(f'[{idx}/{ len(ALL_AREA_IDS)}] Processing {area_id}...')

        for date in dates:
            try:
                programs = fetch_area_programs(area_id, date)

                if programs:
                    db.save_programs(programs, area_id, date)
                    total_programs += len(programs)
                    success_count += 1
                    logger.info(f'  ✅ {area_id} {date}: {len(programs)} programs')
                else:
                    logger.warning(f'  ⚠️ {area_id} {date}: No programs found')

                # レート制限対策
                time.sleep(0.2)

            except Exception as e:
                error_count += 1
                logger.error(f'  ❌ {area_id} {date}: {str(e)}')

    elapsed_time = time.time() - start_time

    logger.info('=' * 60)
    logger.info(f'Update completed in {elapsed_time:.1f} seconds')
    logger.info(f'Total programs: {total_programs}')
    logger.info(f'Success: {success_count}, Errors: {error_count}')
    logger.info('=' * 60)

    # 古いデータを削除
    db.cleanup_old_data(days_to_keep=15)

    # 結果を返す
    return {
        'areas': len(ALL_AREA_IDS),
        'programs': total_programs,
        'success': success_count,
        'errors': error_count,
        'elapsed_time': elapsed_time
    }


if __name__ == '__main__':
    # 直接実行時
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    # DB初期化
    db.init_database()

    # 番組表更新
    update_all_areas()
