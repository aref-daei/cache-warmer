#!/usr/bin/env python3
"""
warm_cache_selenium.py

هدف: باز کردن صفحات داخلی یک وبسایت با Selenium تا بک‌اند (مثلاً Redis) آن صفحه‌ها را پیش‌کش کند.
نکته: این اسکریپت صفحه‌ها را ذخیره نمی‌کند. فقط باز می‌کند و اجازه می‌دهد JS اجرا شود.

نیازمندی‌ها:
    pip install selenium webdriver-manager beautifulsoup4 tqdm

اجرای نمونه:
    python warm_cache_selenium.py https://example.com --workers 2 --delay 1.0 --max-pages 500 --headless

پارامترهای مهم:
    --workers    تعداد مرورگرهای همزمان (هرکدام یک thread/instance)
    --delay      تاخیر پایه بین باز کردن صفحات (ثانیه)
    --max-pages  حداکثر تعداد صفحه‌ای که باز می‌شود (پیش‌گیرنده از پیمایش بی‌نهایت)
    --headless   اجرای مرورگر در حالت headless
    --scroll     اسکرول صفحه برای trigger کردن lazy-load (True/False)
    --user-agent سفارشی کردن UA (اختیاری)
    --proxies    فایل متنی با لیست پراکسی‌ها (اختیاری؛ فقط برای توزیع بار)
"""

import argparse
import threading
import queue
import time
import random
from urllib.parse import urlparse, urljoin, urldefrag

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
from tqdm import tqdm

# --------- Helpers ----------
def same_domain(base, other):
    return urlparse(base).netloc == urlparse(other).netloc

def normalize_link(base, link):
    try:
        joined = urljoin(base, link)
        clean, _ = urldefrag(joined)
        return clean
    except Exception:
        return None

def load_proxies(path):
    if not path:
        return []
    out = []
    with open(path, 'r', encoding='utf-8') as f:
        for l in f:
            s = l.strip()
            if s:
                out.append(s)
    return out

# --------- Worker (each worker has one browser instance) ----------
def worker_thread(root_url, q: queue.Queue, seen: set, seen_lock: threading.Lock,
                  stats: dict, stats_lock: threading.Lock, pbar, args, proxy=None):
    # prepare Chrome options
    opts = ChromeOptions()
    if args.headless:
        opts.add_argument("--headless=new")  # newer headless mode
        opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--window-size=1366,768")
    if args.user_agent:
        opts.add_argument(f"--user-agent={args.user_agent}")
    if proxy:
        # proxy should be like http://host:port or host:port
        opts.add_argument(f'--proxy-server={proxy}')

    # instantiate driver
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=opts)

    try:
        wait = WebDriverWait(driver, args.page_load_timeout)
        while True:
            try:
                url = q.get(timeout=3)
            except Exception:
                # queue empty-ish: exit if no more work
                return

            with seen_lock:
                if url in seen:
                    q.task_done()
                    continue
                seen.add(url)

            # politeness delay
            if args.random_delay:
                time.sleep(args.delay * random.uniform(0.6, 1.6))
            else:
                time.sleep(args.delay)

            try:
                # navigate
                driver.get(url)
                # wait for document ready
                try:
                    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
                except Exception:
                    pass

                # optional small extra wait for JS that fetches in background
                if args.js_wait > 0:
                    time.sleep(args.js_wait)

                # optional scroll to bottom to trigger lazy-load (image/text)
                if args.scroll:
                    try:
                        scroll_height = driver.execute_script("return document.body.scrollHeight")
                        viewport = driver.execute_script("return window.innerHeight")
                        cur = 0
                        while cur < scroll_height:
                            cur += viewport // 2
                            driver.execute_script(f"window.scrollTo(0, {cur});")
                            time.sleep(0.2)
                            scroll_height = driver.execute_script("return document.body.scrollHeight")
                        # back to top
                        driver.execute_script("window.scrollTo(0, 0);")
                        # give a short time for any triggered requests
                        time.sleep(0.3)
                    except Exception:
                        pass

                # read page source for link extraction (not saved)
                try:
                    html = driver.page_source
                except Exception:
                    html = ""

                # extract internal links and enqueue
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    for a in soup.find_all('a', href=True):
                        href = a['href'].strip()
                        n = normalize_link(url, href)
                        if not n:
                            continue
                        if same_domain(root_url, n):
                            with seen_lock:
                                if n not in seen and q.qsize() + stats['crawled'] < args.max_pages:
                                    q.put(n)

                # update stats
                with stats_lock:
                    stats['crawled'] += 1
                    pbar.update(1)
                    pbar.set_postfix({'last': url, 'seen': len(seen)})
            except Exception as e:
                with stats_lock:
                    stats['failed'] += 1
                    pbar.set_postfix({'failed': stats['failed']})
            finally:
                q.task_done()

            # stop if reached max
            with stats_lock:
                if stats['crawled'] >= args.max_pages:
                    return

    finally:
        try:
            driver.quit()
        except Exception:
            pass

# --------- Main crawling coordinator ----------
def run_warm_crawl(root_url, workers=2, delay=1.0, random_delay=True, max_pages=1000,
                   headless=True, scroll=True, page_load_timeout=30, js_wait=0.5,
                   user_agent=None, proxies=None):
    q = queue.Queue()
    q.put(root_url)
    seen = set()
    seen_lock = threading.Lock()
    stats = {'crawled': 0, 'failed': 0}
    stats_lock = threading.Lock()

    pbar = tqdm(total=0, unit='page', desc='Warm cache')

    threads = []
    proxies = proxies or []
    for i in range(workers):
        proxy = random.choice(proxies) if proxies else None
        t = threading.Thread(
            target=worker_thread,
            args=(root_url, q, seen, seen_lock, stats, stats_lock, pbar,
                  argparse.Namespace(delay=delay, random_delay=random_delay, max_pages=max_pages,
                                     headless=headless, scroll=scroll, page_load_timeout=page_load_timeout,
                                     js_wait=js_wait, user_agent=user_agent),
                  proxy)
        )
        t.daemon = True
        t.start()
        threads.append(t)

    try:
        # wait until queue empties or max_pages reached
        while True:
            with stats_lock:
                total_est = stats['crawled'] + q.qsize()
                if total_est > pbar.total:
                    pbar.total = min(total_est, max_pages)
                if stats['crawled'] >= max_pages:
                    break
            # if queue empty and threads idle -> break
            if q.empty():
                # small grace time for threads to discover emptiness
                time.sleep(1.0)
                if q.empty():
                    break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Interrupted by user — shutting down...")
    finally:
        # wait for threads to finish
        for t in threads:
            t.join(timeout=1.0)
        pbar.close()
        print(f"Done. crawled={stats['crawled']}, failed={stats['failed']}, discovered={len(seen)}")

# --------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Warm site cache using Selenium (open pages so backend caches them).")
    ap.add_argument('root', help='Root URL to start from, e.g. https://example.com')
    ap.add_argument('--workers', '-w', type=int, default=2, help='Number of parallel browser instances (1-4 recommended)')
    ap.add_argument('--delay', '-d', type=float, default=1.0, help='Base delay between page visits (seconds)')
    ap.add_argument('--no-random', action='store_true', help='Disable randomized delay')
    ap.add_argument('--max-pages', type=int, default=1000, help='Maximum pages to open')
    ap.add_argument('--headless', action='store_true', help='Run browsers headless')
    ap.add_argument('--no-scroll', action='store_true', help='Disable automatic scrolling')
    ap.add_argument('--page-load-timeout', type=int, default=30, help='Timeout for page load (seconds)')
    ap.add_argument('--js-wait', type=float, default=0.5, help='Extra wait after readyState=complete for JS fetches (seconds)')
    ap.add_argument('--user-agent', type=str, default=None, help='Optional user-agent string')
    ap.add_argument('--proxies', type=str, default=None, help='Optional file with proxy urls (one per line) to rotate among workers')
    args = ap.parse_args()

    proxies = load_proxies(args.proxies) if args.proxies else None

    run_warm_crawl(
        root_url=args.root,
        workers=args.workers,
        delay=args.delay,
        random_delay=(not args.no_random),
        max_pages=args.max_pages,
        headless=args.headless,
        scroll=(not args.no_scroll),
        page_load_timeout=args.page_load_timeout,
        js_wait=args.js_wait,
        user_agent=args.user_agent,
        proxies=proxies
    )

if __name__ == '__main__':
    main()
