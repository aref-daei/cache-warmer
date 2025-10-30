import argparse
import logging
import random
import sys
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


def setup_driver():
    """Sets up a headless Firefox driver."""
    options = FirefoxOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")

    try:
        service = FirefoxService(GeckoDriverManager().install())
        driver = webdriver.Firefox(service=service, options=options)
        return driver
    except Exception as e:
        print(f"Error setting up WebDriver: {e}", file=sys.stderr)
        return None


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
        pass  # Page might close during scroll


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


def main():
    parser = argparse.ArgumentParser(description="Single-worker website crawler for cache warming.")
    parser.add_argument("url", help="The root URL of the website to start crawling.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of pages to visit (default: 50).")
    parser.add_argument("--delay", default="1-3", help="Random delay range between requests (e.g., '1-3' seconds).")
    parser.add_argument("--scroll", action="store_true", help="Enable automatic scrolling on each page.")
    parser.add_argument("--js-wait", type=int, default=2, help="Time (seconds) to wait for JS to execute (default: 2).")

    args = parser.parse_args()

    # --- Initial Setup ---
    base_url = args.url
    domain = urlparse(base_url).netloc
    if not domain:
        print("Invalid URL. Please start with http:// or https://", file=sys.stderr)
        sys.exit(1)

    # Parse delay
    try:
        min_delay, max_delay = map(float, args.delay.split('-'))
    except ValueError:
        print("Invalid delay format. Use 'min-max'.", file=sys.stderr)
        sys.exit(1)

    # --- Prepare for Crawl ---
    links_to_visit = Queue()
    links_to_visit.put(base_url)
    visited_links = set()
    successful_pages = []
    failed_pages = {}

    print(f"[*] Starting crawl at {base_url} (Domain lock: {domain})")
    print(f"[*] Page Limit: {args.limit}")
    print(f"[*] Delay: {min_delay}s to {max_delay}s")

    pbar = tqdm(total=args.limit, desc="Crawling Pages", unit="page")

    # --- Main Crawl Loop ---
    while not links_to_visit.empty() and len(visited_links) < args.limit:
        current_url = links_to_visit.get()

        if current_url in visited_links:
            continue

        visited_links.add(current_url)

        driver = None
        try:
            driver = setup_driver()
            if not driver:
                raise Exception("WebDriver setup failed.")

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
            for link in new_links:
                if link not in visited_links:
                    links_to_visit.put(link)

            successful_pages.append(current_url)
            pbar.update(1)
            pbar.set_postfix(Queue=links_to_visit.qsize())

        except Exception as e:
            failed_pages[current_url] = str(e)
            print(f"\n[!] Error visiting {current_url}: {e}", file=sys.stderr)

        finally:
            if driver:
                driver.quit()

            # Apply random delay
            delay = random.uniform(min_delay, max_delay)
            time.sleep(delay)

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
