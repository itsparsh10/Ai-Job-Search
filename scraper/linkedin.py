"""
scraper/linkedin.py — Scraper for LinkedIn job search (guest mode).

Follows the same architecture as scraper/naukri.py but uses `requests`
instead of Selenium, because LinkedIn's guest search returns plain HTML
fragments that are easy to parse without a headless browser.

Returns a list of dictionaries, one per job:

    {
        "title", "company", "experience", "salary",
        "location", "description", "posted_date", "url", "source"
    }
"""

import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

# How many job detail pages to fetch descriptions for (keeps the run fast).
MAX_DETAIL_FETCHES = 25

DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
SEARCH_URL = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
              "?keywords={keywords}&location={location}&start={start}")

# Friendly user-agent so the guest pages render normally.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _job_id_from_url(url):
    """Extracts the numeric LinkedIn job id from a job URL, if present."""
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return match.group(1) if match else None


def _fetch_description(job_id, session):
    """Best-effort fetch of the full job description from the guest API."""
    try:
        resp = session.get(DETAIL_URL.format(job_id=job_id), timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            soup = BeautifulSoup(resp.text, "html.parser")
            node = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
            if node:
                return " ".join(node.get_text(" ", strip=True).split())
    except Exception as exc:
        print(f"[linkedin] could not fetch description for job {job_id}: {exc}")
    return ""


def _experience_from_text(text):
    """Finds an experience requirement such as '3-6 Yrs' or '2+ years' in text."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}-{match.group(2)} Yrs"
    match = re.search(r"(\d+)\s*\+?\s*(?:years?|yrs)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}+ Yrs"
    return None


def scrape_linkedin(keyword="software developer", location="india", max_pages=1, max_jobs=None):
    """
    Scrapes jobs from LinkedIn (guest search) based on keyword and location.
    Returns a list of dictionaries containing job details.
    """
    job_results = []
    seen_urls = set()
    session = requests.Session()
    session.headers.update(_HEADERS)

    try:
        keywords = urllib.parse.quote_plus(keyword)
        loc = urllib.parse.quote_plus(location)

        for page in range(1, max_pages + 1):
            start = (page - 1) * 25  # LinkedIn guest search returns 25 results per page
            url = SEARCH_URL.format(keywords=keywords, location=loc, start=start)
            print(f"Scraping URL: {url}")

            response = session.get(url, timeout=15)
            if response.status_code != 200:
                print(f"[linkedin] giving up on page {page}: Status code {response.status_code}")
                break

            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select("li.job-card-container") or soup.select("div.base-card") or soup.select("li")

            page_new = 0
            for card in cards:
                # Title & URL
                title_elem = card.select_one(".base-search-card__title")
                link_elem = card.select_one("a.base-card__full-link")
                if not title_elem or not link_elem:
                    continue
                url_href = link_elem.get("href", "").strip()

                # Skip duplicates
                if url_href in seen_urls:
                    continue
                seen_urls.add(url_href)

                job_data = {
                    "title": title_elem.get_text(" ", strip=True),
                    "company": None,
                    "experience": None,
                    "salary": None,
                    "location": None,
                    "description": None,
                    "posted_date": None,
                    "url": url_href,
                    "source": "LinkedIn",
                }

                # Company
                comp_elem = card.select_one(".base-search-card__subtitle a") or card.select_one(".base-search-card__subtitle")
                if comp_elem:
                    job_data["company"] = comp_elem.get_text(" ", strip=True)

                # Location
                loc_elem = card.select_one(".job-search-card__location")
                if loc_elem:
                    job_data["location"] = loc_elem.get_text(" ", strip=True)

                # Salary (when LinkedIn shows it on the card)
                sal_elem = card.select_one(".job-search-card__salary-info")
                if sal_elem:
                    job_data["salary"] = sal_elem.get_text(" ", strip=True)

                # Posted date (prefer the machine-readable datetime attribute)
                time_elem = card.select_one("time")
                if time_elem:
                    job_data["posted_date"] = (
                        time_elem.get("datetime") or time_elem.get_text(" ", strip=True) or None
                    )

                # Description (best-effort, capped so the run stays fast)
                job_id = _job_id_from_url(url_href)
                if job_id and len(job_results) < MAX_DETAIL_FETCHES:
                    description = _fetch_description(job_id, session)
                    job_data["description"] = description
                    job_data["experience"] = _experience_from_text(description)

                job_results.append(job_data)
                page_new += 1

            print(f"Page {page} scraped. Found {len(cards)} jobs ({page_new} new).")

            if page_new == 0:
                print("[linkedin] no new jobs on this page — stopping pagination")
                break

            if max_jobs and len(job_results) >= max_jobs:
                break

            time.sleep(2)  # be polite between pages

    except Exception as e:
        print(f"An error occurred during scraping: {e}")
    
    return job_results[:max_jobs] if max_jobs else job_results


if __name__ == "__main__":
    # Test execution
    print("Testing LinkedIn Scraper...")
    results = scrape_linkedin(keyword="python developer", location="India", max_pages=1)
    for idx, r in enumerate(results[:5]):  # Print first 5
        print(f"{idx+1}. {r['title']} at {r['company']} - {r['location']}")
