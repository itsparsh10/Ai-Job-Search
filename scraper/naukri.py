"""
scraper/naukri.py — Selenium scraper for Naukri.com jobs.

Reuses the same driver + scraping flow used across the project.
Returns a list of dictionaries, one per job:

    {
        "title", "company", "experience", "salary",
        "location", "description", "posted_date", "url", "source"
    }
"""

import time
import urllib.parse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


def get_chrome_driver():
    """Sets up a robust, headless Chrome WebDriver."""
    chrome_options = Options()

    # Run headless (no GUI)
    chrome_options.add_argument("--headless=new")

    # Anti-bot stealth arguments
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    # Standard optimizations
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

    # Use webdriver_manager to automatically download/use correct ChromeDriver
    service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=chrome_options)

    # Execute CDP command to prevent bot detection
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        '''
    })

    return driver


# Naukri has changed its job-card markup a few times. The selectors below are
# tried in order, so a UI redesign doesn't silently break the scraper.
JOB_CARD_SELECTORS = [
    (By.CLASS_NAME, "srp-jobtuple-wrapper"),  # current UI
    (By.CSS_SELECTOR, "article.jobTuple"),    # older UI
    (By.CSS_SELECTOR, "div.jobTuple"),
    (By.CSS_SELECTOR, ".srp-jobtuple-wrapper, article.jobTuple, div.jobTuple"),
]


def _load_page(driver, url, attempts=3, wait_timeout=15):
    """
    Loads a URL with retries. Returns True when a job list appears.

    Retrying on timeout fixes a common Naukri flakiness where the first
    request is throttled and a simple refresh is enough to succeed.
    """
    for attempt in range(1, attempts + 1):
        try:
            driver.get(url)
            for by, selector in JOB_CARD_SELECTORS:
                try:
                    WebDriverWait(driver, wait_timeout).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    return True
                except Exception:
                    continue
        except Exception as exc:
            print(f"[naukri] attempt {attempt}/{attempts} failed for {url}: {exc}")
        if attempt < attempts:
            time.sleep(2 * attempt)  # small backoff between retries
    return False


def scrape_naukri(keyword="software developer", location="india", max_pages=1, max_jobs=None):
    """
    Scrapes jobs from Naukri.com based on keyword and location.
    Returns a list of dictionaries containing job details.
    """
    driver = get_chrome_driver()
    job_results = []
    seen_urls = set()

    try:
        # Format the URL safely
        kw_formatted = urllib.parse.quote_plus(keyword.lower().replace(" ", "-"))
        loc_formatted = urllib.parse.quote_plus(location.lower().replace(" ", "-"))
        base_url = f"https://www.naukri.com/{kw_formatted}-jobs-in-{loc_formatted}"

        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else f"{base_url}-{page}"
            print(f"Scraping URL: {url}")

            # Wait for the list container to load (Explicit Wait) with retries
            if not _load_page(driver, url):
                print(f"[naukri] giving up on page {page}: {url}")
                continue

            # Scroll down to ensure dynamic content loads
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)  # Brief pause for lazy loaded images/data

            # Parse page source with BeautifulSoup (fallback selectors for
            # different Naukri UI versions)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            job_elems = soup.select("div.srp-jobtuple-wrapper, article.jobTuple, div.jobTuple")

            page_new = 0
            for job_elem in job_elems:
                job_data = {
                    "title": None,
                    "company": None,
                    "experience": None,
                    "salary": None,
                    "location": None,
                    "description": None,
                    "posted_date": None,
                    "url": None,
                    "source": "Naukri",
                }

                # Title & URL
                title_elem = job_elem.find('a', class_='title')
                if title_elem:
                    job_data["title"] = title_elem.text.strip()
                    job_data["url"] = title_elem.get('href', '')

                # Company
                comp_elem = job_elem.find('a', class_='comp-name')
                if comp_elem:
                    job_data["company"] = comp_elem.text.strip()

                # Experience
                exp_elem = job_elem.find('span', class_='expwdth')
                if exp_elem:
                    job_data["experience"] = exp_elem.text.strip()

                # Salary
                sal_elem = job_elem.find('span', class_='ni-job-tuple-icon-srp-rupee')
                if sal_elem:
                    # Salary might be in the parent's text
                    job_data["salary"] = sal_elem.parent.text.strip()

                # Location
                loc_elem = job_elem.find('span', class_='locWdth')
                if loc_elem:
                    job_data["location"] = loc_elem.text.strip()

                # Description
                desc_elem = job_elem.find('span', class_='job-desc')
                if desc_elem:
                    job_data["description"] = desc_elem.text.strip()
                else:
                    job_data["description"] = ""

                # Scrape Skills / Tags
                tags_elem = job_elem.find('ul', class_='tags-has-description') or job_elem.find('ul', class_='tags')
                if tags_elem:
                    skills_list = [li.text.strip() for li in tags_elem.find_all('li')]
                    if skills_list:
                        job_data["description"] += f"\n\n**Skills Required:** {', '.join(skills_list)}"

                # Date Posted
                date_elem = job_elem.find('span', class_='job-post-day')
                if date_elem:
                    date_text = date_elem.text.strip()
                    job_data["posted_date"] = "Today" if date_text == "Just Now" else date_text

                # Append only if we have a title and we haven't seen this job before
                if job_data["title"] and job_data["url"] not in seen_urls:
                    seen_urls.add(job_data["url"])
                    job_results.append(job_data)
                    page_new += 1

            print(f"Page {page} scraped. Found {len(job_elems)} jobs ({page_new} new).")

            # Stop early when the caller only wants a limited number of jobs
            if max_jobs and len(job_results) >= max_jobs:
                break

    except Exception as e:
        print(f"An error occurred during scraping: {e}")
    finally:
        driver.quit()

    return job_results[:max_jobs] if max_jobs else job_results


if __name__ == "__main__":
    # Test execution
    print("Testing Naukri Scraper...")
    results = scrape_naukri(keyword="python developer", location="bangalore", max_pages=1)
    for idx, r in enumerate(results[:5]):  # Print first 5
        print(f"{idx+1}. {r['title']} at {r['company']} - {r['location']}")
