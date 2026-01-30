import os
import time
import random
import requests
import re
import tomllib
import tomli_w  # 用于将进度写回配置文件喵～
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse, urlencode, unquote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= 加载配置文件 =================
CONFIG_FILE = "config.toml"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"(>_<) 找不到配置文件 {CONFIG_FILE} 喵！")
        return None
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)

def save_config(config_data):
    """将更新后的配置（主要是进度）保存回文件喵～"""
    try:
        with open(CONFIG_FILE, "wb") as f:
            tomli_w.dump(config_data, f)
        return True
    except Exception as e:
        print(f"(`д′) 报错进度失败: {e}")
        return False

CONF = load_config()
if not CONF:
    exit(1)

def get_site_dirname(base_url: str) -> str:
    """从 base_url 生成一个稳定、可做目录名的站点标识。"""
    parsed = urlparse(base_url)

    hostname = parsed.hostname or parsed.netloc or parsed.path or "site"
    dirname = hostname

    if parsed.hostname and parsed.port:
        dirname = f"{parsed.hostname}_{parsed.port}"

    path_part = parsed.path.strip("/")
    if path_part:
        dirname = f"{dirname}_{path_part}"

    dirname = re.sub(r'[\\\\/:*?"<>|]+', "_", dirname).strip()
    return dirname or "site"

def get_download_dir(download_root: str, base_url: str) -> str:
    """每个站点单独落盘到 downloads/<site>/..."""
    return os.path.join(download_root, get_site_dirname(base_url))

# 配置映射
COOKIE_STR = CONF['auth']['cookie_str']
BASE_URL = CONF['site']['base_url']
START_URL = CONF['site']['start_url']
DOWNLOAD_ROOT_DIR = CONF['download']['save_dir']
DOWNLOAD_DIR = get_download_dir(DOWNLOAD_ROOT_DIR, BASE_URL)

MAX_DOWNLOAD_COUNT = CONF['download']['max_count']
START_PAGE = CONF['download']['start_page']
DOWNLOAD_DELAY_RANGE = (CONF['download']['delay_min'], CONF['download']['delay_max'])

MAX_RETRIES = CONF['network']['max_retries']
BACKOFF_FACTOR = CONF['network']['backoff_factor']
TIMEOUT = CONF['network']['timeout']

HEADERS = {
    'User-Agent': CONF['network']['user_agent'],
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': BASE_URL,
}
# ===========================================

def get_session():
    """创建一个带有重试机制的 Session 喵～"""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

def get_page_url(base_url, page_num):
    """动态构造带 page 参数的 URL 喵～"""
    u = urlparse(base_url)
    query = parse_qs(u.query)
    query['page'] = [str(page_num)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(u._replace(query=new_query))

def download_file(session, url, current_count):
    """执行单个文件的下载喵～

    返回值:
      - "downloaded": 成功下载
      - "duplicate": 目标文件已存在，跳过
      - "failed": 下载失败
    """
    try:
        torrent_id = parse_qs(urlparse(url).query).get('id', ['unknown'])[0]
        temp_filename = f"torrent_{torrent_id}.torrent"

        res = session.get(url, stream=True, timeout=TIMEOUT)
        if res.status_code == 429:
            print(f"(#￣～￣#) 触发了服务器限流(429)！深层休眠 60 秒喵...")
            time.sleep(60)
            return "failed"

        res.raise_for_status()

        # --- 针对低分享率确认页的特殊处理喵 ---
        content_type = res.headers.get('Content-Type', '').lower()
        if 'text/html' in content_type or 'downloadnotice.php' in res.url:
            print(f"(⊙﹏⊙) 检测到下载提示页: {res.url}，尝试自动确认并下载种子 (ID: {torrent_id})...")

            # 构造正确的提交地址喵～
            notice_url = urljoin(BASE_URL, "downloadnotice.php")

            post_data = {
                'id': torrent_id,
                'type': 'ratio',
                'letmedown': 'ratio',
                'submit': '下载种子文件'
            }
            # 这里的 Referer 最好带上喵，有的站会校验
            notice_headers = {
                'Referer': res.url,
                'Origin': BASE_URL.rstrip('/')
            }
            res = session.post(notice_url, data=post_data, headers=notice_headers, stream=True, timeout=TIMEOUT)
            res.raise_for_status()

            if 'text/html' in res.headers.get('Content-Type', '').lower():
                print(f"(`д′) 自动确认失败了喵，返回的还是网页，请手动检查账号状态...")
                # 打印出现在的 URL 方便调试
                print(f"最终 URL: {res.url}")
                return "failed"
        # -----------------------------------

        save_filename = temp_filename
        if 'Content-Disposition' in res.headers:
            cd = res.headers['Content-Disposition']
            # 优先尝试获取标准的 filename* (UTF-8) 喵～
            match_utf8 = re.search(r"filename\*=UTF-8''([^;]+)", cd)
            if match_utf8:
                save_filename = unquote(match_utf8.group(1))
            else:
                match = re.search(r'filename="?([^";]+)"?', cd)
                if match:
                    filename_raw = match.group(1)
                    # 针对部分站点乱码的黑魔法解码喵～
                    try:
                        save_filename = filename_raw.encode('latin1').decode('utf-8')
                    except Exception:
                        save_filename = filename_raw

        save_filename = "".join([c for c in save_filename if c not in r'/:*?"<>|']).strip()
        save_path = os.path.join(DOWNLOAD_DIR, save_filename)

        # 检查是否是坏文件 (HTML)
        is_already_html = False
        if os.path.exists(save_path):
            with open(save_path, 'rb') as f:
                head = f.read(100)
                if b'<!DOCTYPE' in head or b'<html' in head:
                    is_already_html = True

        if os.path.exists(save_path) and not is_already_html:
            print(f"╮(╯_╰)╭ 已存在且有效，跳过: {save_filename}")
            return "duplicate"

        with open(save_path, 'wb') as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"(๑ˉ∀ˉ๑) [进度 {current_count}] 下载成功: {save_filename}")
        return "downloaded"

    except Exception as e:
        print(f"(`д′) 下载失败 {url}: {e}")
        return "failed"

def download_torrents():
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
        print(f"(*^▽^*) 创建了下载目录: {DOWNLOAD_DIR}")

    session = get_session()
    if COOKIE_STR:
        session.cookies.update({c.split('=')[0].strip(): c.split('=')[1].strip() for c in COOKIE_STR.split(';') if '=' in c})
    else:
        print("(>_<) 错误：请先在 config.toml 配置 Cookie 喵！")
        return

    total_attempted = 0
    downloaded_count = 0
    duplicate_count = 0
    failed_count = 0
    current_page = START_PAGE

    print(f"o(*￣︶￣*)o 进度恢复成功！从第 {current_page} 页开始下载喵～")

    try:
        while True:
            if MAX_DOWNLOAD_COUNT > 0 and total_attempted >= MAX_DOWNLOAD_COUNT:
                print(f"ヽ(✿ﾟ▽ﾟ)诺 已达到最大下载量 {MAX_DOWNLOAD_COUNT} 喵！")
                break

            target_url = get_page_url(START_URL, current_page)
            print(f"(..•˘_˘•..) 正在扫描第 {current_page} 页...")

            resp = session.get(target_url, timeout=TIMEOUT)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, 'html.parser')
            links = soup.find_all('a', href=True)
            page_links = list(dict.fromkeys([urljoin(BASE_URL, a['href']) for a in links if 'download.php' in a['href']]))

            if not page_links:
                print(f"╮(╯_╰)╭ 在第 {current_page} 页没找到种子，任务可能完成了喵～")
                break

            for url in page_links:
                if MAX_DOWNLOAD_COUNT > 0 and total_attempted >= MAX_DOWNLOAD_COUNT:
                    break
                total_attempted += 1
                result = download_file(session, url, total_attempted)
                if result == "downloaded":
                    downloaded_count += 1
                elif result == "duplicate":
                    duplicate_count += 1
                else:
                    failed_count += 1

                if result != "failed":
                    time.sleep(random.uniform(*DOWNLOAD_DELAY_RANGE))
                else:
                    time.sleep(5)

            current_page += 1
            CONF['download']['start_page'] = current_page
            save_config(CONF)

            wait_between_pages = random.uniform(5, 10)
            print(f"(*^▽^*) 第 {current_page-1} 页处理完毕，休息 {wait_between_pages:.1f} 秒...")
            time.sleep(wait_between_pages)

    except KeyboardInterrupt:
        print("\n(*/ω\\*) 收到主人中断指令，正在安全退出喵...")
    except Exception as e:
        print(f"(°ー°〃) 运行出错: {e}")

    print(f"ヽ(✿ﾟ▽ﾟ)ノ 本次任务结束，共处理 {total_attempted} 个种子喵！")
    print(f"(≧▽≦) 统计：成功下载 {downloaded_count}，重复跳过 {duplicate_count}，下载失败 {failed_count} 喵！")

if __name__ == "__main__":
    download_torrents()
