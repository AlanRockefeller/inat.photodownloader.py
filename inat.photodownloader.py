#!/usr/bin/env python3

# iNaturalist photo / photo filename downloader

# Version 1.0 - by Alan Rockefeller
# April 30, 2025

import sys

try:
    import argparse
    import requests
    from bs4 import BeautifulSoup
    import csv
    import time
    import os
    import re
    from urllib.parse import urlparse, urljoin
except ImportError as e:
    print(f"\n[!] Critical Requirement Missing: {e}")
    print("    Please install the required libraries by running:")
    print("    pip install requests beautifulsoup4")
    sys.exit(1)

# -------------------- Argument Parsing --------------------
parser = argparse.ArgumentParser(
    description="""
This script downloads your iNaturalist observation data (by username), including the
original filenames of photos (only visible to the uploader) and optionally downloads
the images.

HOW IT WORKS:
1. Uses the iNaturalist public API to get your observation IDs.
2. Gets photo IDs for each observation using the API.
3. Scrapes each photo's web page using your session cookie to retrieve
   the original filenames.
4. Writes this data to a CSV and optionally downloads the "original" size images.

OPTIONS:
  --username USERNAME       iNaturalist username (required).
  --cookie COOKIE           Your iNaturalist session cookie value (without the name prefix).
                            (e.g., 2f065b3aba346277da95bec21d559f3a)
                            Required to access the original filenames.
  --limit N                 Limit number of observations to process.
  --verbose                 Print each CSV row as it's written.
  --debug                   Print detailed debug output (API/web scraping info).
  --download                Download the original-sized images to ./images/ folder.
  --imagedir DIRECTORY      Specify custom directory for downloaded images.
  --add-photo-urls          Include photo URLs in the CSV output.
  -o, --out, --output FILENAME   Set the output CSV filename (must end with .csv).
  --help                    Show this help message and exit.

HOW TO GET YOUR iNaturalist SESSION COOKIE (necessary to get the photo filename):
1. Log in to https://www.inaturalist.org in Chrome or Firefox.
2. Open Developer tools → Application → Storage → Cookies (Chrome) or Storage (Firefox).
3. Under Cookies, find https://www.inaturalist.org.
4. Copy only the VALUE of the cookie named _inaturalist_session.
5. Use it like: --cookie 2f065b3aba346277da95bec21d559f3a

NOTE:
  This program can only retrieves filenames for observations that belong to the
  user who owns the session cookie.
""",
    formatter_class=argparse.RawTextHelpFormatter
)

parser.add_argument("--username", required=False, help="Your iNaturalist username")
parser.add_argument("--cookie", help="Your _inaturalist_session cookie value (without prefix)")
parser.add_argument("--limit", type=int, help="Maximum number of observations to process")
parser.add_argument("--verbose", action="store_true", help="Print each CSV row")
parser.add_argument("--debug", action="store_true", help="Enable debug logging")
parser.add_argument("--download", action="store_true", help="Download original images")
parser.add_argument("--imagedir", default="images", help="Directory for downloaded images")
parser.add_argument("--add-photo-urls", action="store_true", help="Include photo URLs in CSV output")
parser.add_argument("-o", "--out", "--output", dest="output", help="Output CSV filename (must end with .csv)")

args = parser.parse_args()

if len(sys.argv) == 1:
    print("Welcome to iNaturalist Photo Downloader!")
    print("No arguments provided. Showing help menu below:\n")
    parser.print_help()
    sys.exit(0)

if not args.username:
    print("\n[!] Requirement Missing: Username")
    print("    You must provide an iNaturalist username to fetch data.")
    print("    Usage: --username <your_username>")
    sys.exit(1)

# Validate output filename
if args.output:
    if not args.output.lower().endswith('.csv'):
        print(f"\n[!] Invalid Requirement: Output Filename '{args.output}'")
        print("    The output filename must have a .csv extension.")
        print("    Usage: --output my_results.csv")
        sys.exit(1)

output_filename = args.output if args.output else "inaturalist_filenames.csv"

# -------------------- Globals --------------------
BASE_API_URL = "https://api.inaturalist.org/v1/observations"
# Guidelines: Use a custom User-Agent to identify your application.
HEADERS = {"User-Agent": "iNaturalistPhotoDownloader/1.0"}
COOKIES = {}

if args.cookie:
    COOKIES["_inaturalist_session"] = args.cookie.strip()
    if args.debug:
        print(f"[DEBUG] Using cookie: _inaturalist_session={args.cookie[:5]}...")

# -------------------- Unified Rate Limit Logic --------------------
class RateLimiter:
    def __init__(self, requests_per_second=1.0):
        self.delay = 1.0 / requests_per_second
        self.last_request_time = 0

    def wait(self):
        now = time.time()
        elapsed = now - self.last_request_time
        wait_time = max(0, self.delay - elapsed)
        if wait_time > 0:
            time.sleep(wait_time)
        self.last_request_time = time.time()

# Global rate limiter to be shared across API, Scraper, and Downloader
# Guidelines: Limit requests to approximately 1 request per second.
global_limiter = RateLimiter(requests_per_second=1.0)

def rate_limited_request(method, url, **kwargs):
    global_limiter.wait()
    if method.lower() == 'get':
        return requests.get(url, **kwargs)
    elif method.lower() == 'post':
        return requests.post(url, **kwargs)
    else:
        raise ValueError(f"Unsupported method: {method}")

def rate_limited_api_get(url, params=None):
    # Uses the same global limiter to share the "bucket" with other requests
    global_limiter.wait()
    return requests.get(url, params=params)

# -------------------- Fetch Observation IDs --------------------
def get_observation_ids(username, limit=None):
    page = 1
    per_page = 200
    ids = []

    while True:
        params = {"user_login": username, "page": page, "per_page": per_page}
        if args.debug:
            print(f"[DEBUG] API GET: {BASE_API_URL} {params}")
        r = rate_limited_api_get(BASE_API_URL, params)
        r.raise_for_status()
        results = r.json().get("results", [])

        for obs in results:
            ids.append(obs["id"])
            if limit and len(ids) >= limit:
                return ids

        if not results or (limit and len(ids) >= limit):
            break

        page += 1

    return ids

# -------------------- Get Photo IDs for an Observation --------------------
def get_photo_ids(obs_id):
    url = f"{BASE_API_URL}/{obs_id}"
    if args.debug:
        print(f"[DEBUG] API GET: {url}")
    r = rate_limited_api_get(url)
    r.raise_for_status()
    results = r.json().get("results", [])
    
    if not results:
        return []
    
    photos = []
    for photo in results[0].get("photos", []):
        photo_id = photo.get("id")
        if photo_id:
            # Get the URL and process it
            url = photo.get("url", "")
            
            # Debug the URLs
            if args.debug:
                print(f"[DEBUG] Photo {photo_id} URL: {url}")
            
            photos.append({
                "id": photo_id,
                "url": url,
                "photo_page_url": f"https://www.inaturalist.org/photos/{photo_id}"
            })
    
    return photos

# -------------------- Scrape Filename and Image URL from Photo Page --------------------
def scrape_photo_page(photo_id):
    url = f"https://www.inaturalist.org/photos/{photo_id}"
    if args.debug:
        print(f"[DEBUG] Scraping photo page: {url}")
    r = rate_limited_request('get', url, headers=HEADERS, cookies=COOKIES)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    
    if args.debug:
        print(f"[DEBUG] Page title: {soup.title.string if soup.title else 'No title'}")
        
    # Find the filename from the table row with header "Filename"
    filename = ""
    
    # Method 1: Look for table row with "Filename" header
    for tr in soup.find_all('tr'):
        th = tr.find('th')
        if th and th.get_text(strip=True) == "Filename":
            td = tr.find('td')
            if td:
                filename = td.get_text(strip=True)
                if args.debug:
                    print(f"[DEBUG] Found filename in table: {filename}")
                break
    
    # Method 2: If not found, try looking for data-original-filename attribute (backup)
    if not filename:
        filename_el = soup.select_one('[data-original-filename]')
        if filename_el:
            filename = filename_el.get('data-original-filename')
            if args.debug:
                print(f"[DEBUG] Found filename in data attribute: {filename}")
    
    # Find the link to the original size image
    original_size_url = None
    links = soup.find_all('a')
    
    if args.debug:
        print(f"[DEBUG] Found {len(links)} links on page")
        
    for link in links:
        link_text = link.get_text(strip=True).lower()
        href = link.get('href', '')
        if args.debug and (link_text == "original" or "size=original" in href):
            print(f"[DEBUG] Potential match: '{link_text}' -> {href}")
        
        if link_text == "original" and "size=original" in href:
            original_size_url = href
            if args.debug:
                print(f"[DEBUG] Found original size link: {original_size_url}")
            break
    
    if args.debug:
        if not original_size_url:
            # Extract and print all links for debugging
            print("[DEBUG] Could not find original size link, dumping all links:")
            for link in soup.find_all('a'):
                print(f"[DEBUG] Link: '{link.get_text(strip=True)}' -> {link.get('href')}")
    
    if args.debug:
        if filename:
            print(f"[DEBUG] Successfully extracted filename: {filename}")
        else:
            print(f"[DEBUG] Failed to find filename")
    
    return filename, original_size_url

# -------------------- Get Actual Image URL from Original Size Page --------------------
def get_actual_image_url(original_link):
    if not original_link:
        print("[ERROR] No original size link provided")
        return None
        
    if args.debug:
        print(f"[DEBUG] Getting actual image URL from: {original_link}")
        
    try:
        r = rate_limited_request('get', original_link, headers=HEADERS, cookies=COOKIES)
        r.raise_for_status()
        
        if args.debug:
            print(f"[DEBUG] Successfully loaded original size page ({len(r.content)} bytes)")
            
        soup = BeautifulSoup(r.text, "html.parser")
        
        if args.debug:
            print(f"[DEBUG] Looking for img tag with id 'photo'")
        
        # Find the main image tag that contains the actual image URL
        img = soup.find('img', id='photo')
        
        if img and img.get('src'):
            actual_url = img.get('src')
            if args.debug:
                print(f"[DEBUG] Found actual image URL: {actual_url}")
            return actual_url
        
        # Try an alternative approach - look for any large image
        if not img:
            if args.debug:
                print(f"[DEBUG] No img#photo found, looking for any large image")
            
            # Try to find any img tag with a src that includes 'original'
            img = soup.find('img', src=lambda s: s and 'original' in s.lower())
            if img and img.get('src'):
                actual_url = img.get('src')
                if args.debug:
                    print(f"[DEBUG] Found potential image URL via alternate method: {actual_url}")
                return actual_url
            
        # If still not found, try direct approach - construct the Amazon S3 URL
        if not img and original_link and '/photos/' in original_link:
            try:
                photo_id = re.search(r'/photos/(\d+)', original_link).group(1)
                direct_url = f"https://inaturalist-open-data.s3.amazonaws.com/photos/{photo_id}/original.jpeg"
                if args.debug:
                    print(f"[DEBUG] Constructed direct S3 URL: {direct_url}")
                return direct_url
            except Exception as e:
                print(f"[ERROR] Failed to construct direct URL: {e}")
        
        # If we get here, we couldn't find the image
        if args.debug:
            print(f"[DEBUG] Could not find any suitable image URL")
            print("[DEBUG] Page HTML structure:")
            print(soup.prettify()[:2000])  # First 2000 chars for brevity
            
        return None
    except Exception as e:
        print(f"[ERROR] Failed to get actual image URL: {e}")
        return None

# -------------------- Download Image --------------------
def download_image(img_url, fname, obs_id):
    try:
        if not img_url or img_url == "":
            print(f"[ERROR] No valid URL provided for download")
            return False
        
        if args.debug:
            print(f"[DEBUG] Attempting to download image from: {img_url}")
            
        # Make sure image directory exists
        os.makedirs(args.imagedir, exist_ok=True)
        
        # Sanitize filename for filesystem
        safe_fname = re.sub(r'[^\w\-.]', '_', fname)
        out_path = os.path.join(args.imagedir, f"{obs_id}_{safe_fname}")
        
        # Download the image
        r = rate_limited_request('get', img_url, headers=HEADERS, stream=True)
        
        # Check if we got a valid response
        if r.status_code != 200:
            print(f"[ERROR] Failed to download image: HTTP {r.status_code}")
            if args.debug:
                print(f"[DEBUG] Response headers: {r.headers}")
            return False
            
        # Check content type to make sure it's an image
        content_type = r.headers.get('content-type', '')
        if not content_type.startswith('image/'):
            print(f"[ERROR] Downloaded content is not an image: {content_type}")
            if args.debug:
                print(f"[DEBUG] First 100 bytes: {r.content[:100]}")
            return False
        
        # Get content size for debug output
        content_size = int(r.headers.get('content-length', 0))
        
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                
        # Verify file was created and has content
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            if args.debug:
                print(f"[DEBUG] Successfully downloaded image ({content_size} bytes) to: {out_path}")
            elif args.verbose:
                print(f"Downloaded: {out_path}")
            return True
        else:
            print(f"[ERROR] File was not created or is empty: {out_path}")
            return False
            
    except Exception as e:
        print(f"[ERROR] Download failed for {img_url}: {e}")
        return False

# -------------------- Direct Download by Photo ID --------------------
def direct_download_by_photo_id(photo_id, filename, obs_id):
    """Try to download directly using the S3 URL pattern"""
    try:
        direct_url = f"https://inaturalist-open-data.s3.amazonaws.com/photos/{photo_id}/original.jpeg"
        
        if args.debug:
            print(f"[DEBUG] Trying direct download from: {direct_url}")
            
        return download_image(direct_url, filename, obs_id)
    except Exception as e:
        print(f"[ERROR] Direct download failed: {e}")
        return False

# -------------------- Main Execution --------------------
try:
    obs_ids = get_observation_ids(args.username, limit=args.limit)
    
    fieldnames = ["observation_id", "photo_filenames"]
    if args.add_photo_urls:
        fieldnames.extend(["photo_urls", "original_photo_urls"])

    with open(output_filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        total_photos = 0
        downloaded_photos = 0

        for i, obs_id in enumerate(obs_ids, 1):
            if args.debug:
                print(f"[DEBUG] ({i}/{len(obs_ids)}) Processing observation {obs_id}")

            try:
                photos = get_photo_ids(obs_id)
                
                if args.debug:
                    print(f"[DEBUG] Found {len(photos)} photos for observation {obs_id}")
                
                filenames = []
                photo_urls = []
                original_urls = []
                
                for photo in photos:
                    photo_id = photo["id"]
                    photo_page_url = photo["photo_page_url"]
                    
                    # Scrape filename and original link from the photo page
                    filename, original_link = scrape_photo_page(photo_id)
                    
                    if filename:
                        filenames.append(filename)
                        photo_urls.append(photo_page_url)
                        original_urls.append(original_link if original_link else "")
                        
                        total_photos += 1
                        
                        if args.download:
                            # Get the actual image URL and download
                            success = False
                            
                            if original_link:
                                # Method 1: Try to get the actual URL from original size page
                                actual_image_url = get_actual_image_url(original_link)
                                if actual_image_url:
                                    success = download_image(actual_image_url, filename, obs_id)
                            
                            if not success:
                                # Method 2: Try direct S3 URL construction as fallback
                                print(f"[INFO] Trying direct S3 download for photo {photo_id}")
                                success = direct_download_by_photo_id(photo_id, filename, obs_id)
                            
                            if success:
                                downloaded_photos += 1
                                print(f"[INFO] Successfully downloaded photo {photo_id}")
                                # Guidelines: Limit media downloads to < 5GB/hour.
                                # A 5MB image every ~4 seconds is approx 4.5GB/hour.
                                # We wait 3s here + 1s standard rate limit = 4s total per image.
                                time.sleep(3)
                            else:
                                print(f"[ERROR] All download methods failed for photo {photo_id}")
                
                row = {
                    "observation_id": obs_id,
                    "photo_filenames": ";".join(filenames),
                }
                
                if args.add_photo_urls:
                    row["photo_urls"] = ";".join(photo_urls)
                    row["original_photo_urls"] = ";".join(original_urls)
                    
                writer.writerow(row)

                if args.verbose:
                    print(f"Observation {row['observation_id']}: {len(filenames)} photos found")

            except Exception as e:
                print(f"[ERROR] Failed to process observation {obs_id}: {e}")
                continue

except KeyboardInterrupt:
    print("\n[INFO] Interrupted by user.")
    sys.exit(1)

except requests.exceptions.ConnectionError:
    print("\n[!] Network Error: Could not connect to iNaturalist.")
    print("    Please check your internet connection and try again.")
    sys.exit(1)

except requests.exceptions.Timeout:
    print("\n[!] Network Error: Request timed out.")
    print("    The server might be busy or your connection is slow. Please try again later.")
    sys.exit(1)

except IOError as e:
    print(f"\n[!] File Error: {e}")
    print("    Check if the file is open in another program or if you have write permissions.")
    sys.exit(1)

except Exception as e:
    print(f"\n[!] Unexpected Error: {e}")
    print("    If this persists, please report it to the developer.")
    sys.exit(1)

print(f"\n[INFO] Done. Results saved to {output_filename}")
if args.download:
    print(f"[INFO] Downloaded {downloaded_photos} of {total_photos} images to {args.imagedir}/")
