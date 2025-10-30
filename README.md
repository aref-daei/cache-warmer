# 🕷️ Website Crawler Script (Cache Warmer)

This is a Python script that uses Selenium (with Firefox) and BeautifulSoup to crawl a website. It works concurrently with multiple workers (threads) to find internal links and open the pages.

The primary purpose is "warming up" the server cache (like Redis or Varnish) by visiting pages, ensuring a faster experience for subsequent users.

-----

## 🚀 Features

  * **Concurrent Crawling:** Uses multiple workers (threads) to visit pages simultaneously.
  * **Real Browser Based:** Uses Selenium and Firefox to fully render pages (including JavaScript execution).
  * **Easy Driver Setup:** Automatically manages the browser driver using `webdriver-manager`.
  * **Smart Link Extraction:** Uses BeautifulSoup to find all internal links.
  * **Domain Locked:** The script stays within the domain of the initial URL provided.
  * **Full Control:**
      * Set the number of workers (`--workers`).
      * Set the total page limit (`--limit`).
      * Set a random delay range (`--delay`).
  * **User Simulation:**
      * Ability to auto-scroll pages to trigger lazy-loaded content (`--scroll`).
      * Ability to wait for scripts to execute (`--js-wait`).
  * **Headless Mode:** Runs in headless mode (no browser window) by default.
  * **Debug Mode:** Option to show the browser to visualize the process (`--show-browser`).
  * **Reporting:** Displays a progress bar (`tqdm`) and a final report of successful and failed pages.

-----

## 🛠️ Installation & Setup

1.  Save the `crawler.py` script to your system.

2.  Install the required Python packages:

    ```bash
    pip install selenium webdriver-manager beautifulsoup4 tqdm
    ```

-----

## 💡 How to Use

The script is run directly from the command line (CLI).

### Basic Command

```bash
python crawler.py [URL] [OPTIONS]
```

### Arguments

  * `url`: (Required) The root URL to start crawling (e.g., `https://example.com`)
  * `--limit`: (Optional) Maximum number of pages to visit. (Default: 50)
  * `--workers`: (Optional) Number of concurrent workers. (Default: 3)
  * `--delay`: (Optional) Random delay range between requests (seconds). (Default: '1-3')
  * `--scroll`: (Optional) Enable automatic scrolling on each page.
  * `--js-wait`: (Optional) Time (seconds) to wait for JS to execute. (Default: 2)
  * `--show-browser`: (Optional) Show the browser (disables headless mode) for debugging.

-----

## ⚙️ Usage Examples

### Example 1: Simple Crawl with Defaults

(1 workers, 10-page limit)

```bash
python crawler.py https://example.com
```

### Example 2: Advanced Crawl with 10 Workers & 200-Page Limit

(Includes scrolling and a shorter delay)

```bash
python crawler.py https://example.com --workers 10 --limit 200 --delay "0.5-1.5" --scroll
```

### Example 3: Run with Visible Browser (for Debugging)

```bash
python crawler.py https://example.com --limit 10 --workers 2 --show-browser
```

-----

## ⚠️ Important Warning

Using this script with a high number of workers can generate a significant load on the server.

**Please use this script only on websites you own or have explicit permission to test.** The developer assumes no responsibility for improper use of this tool.