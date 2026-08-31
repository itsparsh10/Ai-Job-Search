import requests
from bs4 import BeautifulSoup
import json

def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "SystemId": "Naukri",
        "AppId": "109"
    }
    url = "https://www.naukri.com/python-jobs-in-india"
    resp = requests.get(url, headers=headers)
    print("Status:", resp.status_code)
    
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # check if Cloudflare
    if "Just a moment" in resp.text:
        print("Blocked by Cloudflare")
        return
        
    print("Page length:", len(resp.text))
    # Find script with window.__INITIAL_STATE__ or __NEXT_DATA__
    scripts = soup.find_all("script")
    found = False
    for s in scripts:
        if s.string and "window.__INITIAL_STATE__" in s.string:
            print("Found __INITIAL_STATE__")
            found = True
            break
        if s.string and "window.PRELOADED_STATE" in s.string:
            print("Found PRELOADED_STATE")
            found = True
            break
            
    if not found:
        print("No JSON state found in scripts")
        
if __name__ == "__main__":
    test()
