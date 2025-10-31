import argparse
import logging
import random
import sys
import threading
import time
from queue import Queue
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from tqdm import tqdm
from webdriver_manager.firefox import GeckoDriverManager

# Disable the verbose logs from webdriver-manager
logging.getLogger('WDM').setLevel(logging.WARNING)


# --- Functions (setup_driver, scroll_page, get_internal_links) remain unchanged ---

def setup_driver(show_browser=False):
    """Sets up a Firefox driver."""
    options = FirefoxOptions()
    if not show_browser:
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")

    # IMPORTANT: GeckoDriverManager().install() is the call that hits the GitHub API.
    # It checks for the latest version. If the driver is already cached and up-to-date,
    # it should be fast and not trigger the rate limit.
    try:
        service = FirefoxService(GeckoDriverManager().install())
        driver = webdriver.Firefox(service=service, options=options)
        return driver
    except Exception as e:
        # We print to stderr here since stdout is used by tqdm
        print(f"Error setting up WebDriver: {e}", file=sys.stderr)
        return None


# (scroll_page function is omitted for brevity but should be included)
def scroll_page(driver, scroll_delay=0.5):
    """Scrolls the entire page down to load dynamic content."""
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(scroll_delay)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
    except WebDriverException:
        pass


# (get_internal_links function is omitted for brevity but should be included)
def get_internal_links(driver, base_url, domain):
    """Extracts all internal links from the page using BeautifulSoup."""
    links = set()
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for a_tag in soup.find_all('a', href=True):
            href = a_tag.attrs['href']
            # Resolve relative links
            full_url = urljoin(base_url, href)

            # Clean up (remove fragments)
            full_url = full_url.split('#')[0]

            # Check if it belongs to the target domain
            if urlparse(full_url).netloc == domain:
                links.add(full_url)
    except Exception as e:
        print(f"Error parsing links: {e}", file=sys.stderr)

    return links


def worker(queue, visited_links, visited_lock, successful_pages, failed_pages, report_lock, pbar, args, base_url,
           domain):
    """The main worker thread function. Each worker sets up its own driver."""

    # Parse delay
    try:
        min_delay, max_delay = map(float, args.delay.split('-'))
    except ValueError:
        min_delay, max_delay = 1.0, 3.0  # Fallback

    try:
        # Setup driver for this specific thread
        driver = setup_driver(args.show_browser)
        if not driver:
            raise Exception("Driver setup failed for this worker.")
    except Exception as e:
        with report_lock:
            print(f"\n[!] Critical Error: Worker failed to initialize driver. {e}", file=sys.stderr)
        # If driver setup fails, this worker cannot do anything.
        while not queue.empty():
            queue.get()  # Consume remaining items to avoid deadlock
            queue.task_done()
        queue.put(None)  # Put sentinel back to stop other workers if necessary
        return

    # --- Main Worker Loop ---
    while True:
        current_url = queue.get()

        # Sentinel value: None means "stop working"
        if current_url is None:
            queue.task_done()
            break

        should_process = False
        with visited_lock:
            # Check if we should process this URL
            if current_url not in visited_links and pbar.n < args.limit:
                should_process = True
                visited_links.add(current_url)

        if not should_process:
            queue.task_done()
            continue

        # --- Process the URL ---

        try:
            # Visit the page
            driver.get(current_url)

            # Wait for JS
            if args.js_wait > 0:
                time.sleep(args.js_wait)

            # Scroll
            if args.scroll:
                scroll_page(driver)

            # Extract links
            new_links = get_internal_links(driver, base_url, domain)

            # Add new, unvisited links to the queue
            with visited_lock:
                for link in new_links:
                    if link not in visited_links:
                        queue.put(link)

            # Report success
            with report_lock:
                successful_pages.append(current_url)

            pbar.update(1)  # Update progress bar

        except Exception as e:
            # Report failure
            with report_lock:
                failed_pages[current_url] = str(e)
                print(f"\n[!] Error visiting {current_url}: {e}", file=sys.stderr)

        finally:
            # Apply random delay
            delay = random.uniform(min_delay, max_delay)
            time.sleep(delay)

            # Mark this task as done
            queue.task_done()

    # Clean up the thread's driver after the loop finishes
    if driver:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Multi-worker website crawler for cache warming.")
    parser.add_argument("url", help="The root URL of the website to start crawling.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of pages to visit (default: 50).")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent workers (default: 3).")
    parser.add_argument("--delay", default="1-3", help="Random delay range between requests (e.g., '1-3' seconds).")
    parser.add_argument("--scroll", action="store_true", help="Enable automatic scrolling on each page.")
    parser.add_argument("--js-wait", type=int, default=2, help="Time (seconds) to wait for JS to execute (default: 2).")
    parser.add_argument("--show-browser", action="store_true",
                        help="Show the browser (disable headless mode) for debugging.")

    args = parser.parse_args()

    # --- Initial Setup ---
    base_url = args.url
    domain = urlparse(base_url).netloc
    if not domain:
        print("Invalid URL. Please start with http:// or https://", file=sys.stderr)
        sys.exit(1)

    # --- Prepare for Crawl (Thread-safe structures) ---
    links_to_visit = Queue()
    visited_links = set()
    visited_lock = threading.Lock()  # To protect visited_links and queue.put

    successful_pages = []
    failed_pages = {}
    report_lock = threading.Lock()  # To protect successful_pages and failed_pages

    pbar = tqdm(total=args.limit, desc="Crawling Pages", unit="page")

    threads = []

    print(f"[*] Starting crawl at {base_url} with {args.workers} workers.")
    print(f"[*] Page Limit: {args.limit}")

    # --- Start Workers ---
    for _ in range(args.workers):
        t = threading.Thread(target=worker, args=(
            links_to_visit, visited_links, visited_lock,
            successful_pages, failed_pages, report_lock,
            pbar, args, base_url, domain
        ))
        t.daemon = True  # Threads will exit when main program exits
        t.start()
        threads.append(t)

    # Add the starting URL
    links_to_visit.put(base_url)

    # Wait for all tasks in the queue to be processed
    links_to_visit.join()

    # Stop the workers by sending sentinel values
    for _ in range(args.workers):
        links_to_visit.put(None)

    # Wait for all threads to finish
    for t in threads:
        t.join()

    pbar.close()

    # --- Final Report ---
    print("\n--- Crawl Report ---")
    print(f"Total Successful Pages Visited: {len(successful_pages)}")
    print(f"Total Errors: {len(failed_pages)}")

    if failed_pages:
        print("\nPages with errors:")
        for url, err in failed_pages.items():
            print(f"  - {url}: {err[:100]}...")  # Summarize error


if __name__ == "__main__":
    main()
