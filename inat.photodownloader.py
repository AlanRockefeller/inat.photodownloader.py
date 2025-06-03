#!/usr/bin/env python3

# iNaturalist photo / photo filename downloader

# Version 1.0 - by Alan Rockefeller
# Last updated: 2024-07-16

import argparse
import requests
from bs4 import BeautifulSoup
import csv
import time
import sys
import os
import re
from urllib.parse import urlparse, urljoin

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
parser.add_argument("--cookie", required=True, help="Your _inaturalist_session cookie value (without prefix)")
parser.add_argument("--limit", type=int, help="Maximum number of observations to process")
parser.add_argument("--verbose", action="store_true", help="Print each CSV row")
parser.add_argument("--debug", action="store_true", help="Enable debug logging")
parser.add_argument("--download", action="store_true", help="Download original images")
parser.add_argument("--imagedir", default="images", help="Directory for downloaded images")
parser.add_argument("--add-photo-urls", action="store_true", help="Include photo URLs in CSV output")
parser.add_argument("-o", "--out", "--output", dest="output", help="Output CSV filename (must end with .csv)")

args = parser.parse_args()

if len(sys.argv) == 1:
    parser.print_help() # This can stay as is, it's argparse's own output
    sys.exit(0)

# These initial error checks happen before logging is fully configured if we want timestamps,
# but we can use a simplified version or basic print to stderr.
# For consistency, we could use log_message if we ensure args is parsed early enough,
# or accept that these early messages are simpler.
# The current log_message will work as args is global.
if not args.username:
    log_message("Error: --username is required.\n", level=LOG_LEVEL_ERROR)
    parser.print_help()
    sys.exit(1)

# Validate output filename
if args.output:
    if not args.output.lower().endswith('.csv'):
        log_message("Error: Output filename must end with .csv", level=LOG_LEVEL_ERROR)
        sys.exit(1)

output_filename = args.output if args.output else "inaturalist_filenames.csv"

# -------------------- Constants --------------------
BASE_API_URL = "https://api.inaturalist.org/v1/observations" 
INAT_PHOTO_PAGE_URL_BASE = "https://www.inaturalist.org/photos/"
S3_PHOTO_URL_BASE = "https://inaturalist-open-data.s3.amazonaws.com/photos/"
# S3_ORIGINAL_PHOTO_SUFFIX = "/original.jpeg" # This will be replaced by a list
S3_ATTEMPT_EXTENSIONS = ['.jpeg', '.jpg', '.png', '.gif'] # Common extensions to try for S3 direct download

HTML_FILENAME_HEADER_TEXT = "Filename"
HTML_ORIGINAL_FILENAME_ATTR_SELECTOR = "[data-original-filename]"
HTML_IMG_ID_SELECTOR = "photo" 
HTML_A_TAG_ORIGINAL_TEXT = "original"
HTML_A_TAG_SIZE_ORIGINAL_SUBSTRING = "size=original"
API_PHOTOS_PATH_SEGMENT = "/photos/" 

LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVEL_INFO = "INFO"
LOG_LEVEL_VERBOSE = "VERBOSE"
LOG_LEVEL_WARNING = "WARNING"
LOG_LEVEL_ERROR = "ERROR"

# -------------------- Globals --------------------
HEADERS = {"User-Agent": "Mozilla/5.0"}
COOKIES = {}
# last_request_time is now the single source for rate limiting.
# last_api_time is removed.
last_request_time = 0 

# -------------------- Logging Function --------------------
def log_message(message, level=LOG_LEVEL_INFO):
    """Prints messages based on verbosity/debug settings and level."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    formatted_message = f"{timestamp} [{level}] {message}"
    
    if level == LOG_LEVEL_DEBUG:
        if args.debug:
            print(formatted_message, file=sys.stdout)
    elif level == LOG_LEVEL_VERBOSE:
        if args.verbose or args.debug:
            print(formatted_message, file=sys.stdout)
    elif level == LOG_LEVEL_INFO:
        print(formatted_message, file=sys.stdout)
    elif level == LOG_LEVEL_WARNING:
        print(formatted_message, file=sys.stderr)
    elif level == LOG_LEVEL_ERROR:
        print(formatted_message, file=sys.stderr)

# Session Initialization is done after arg parsing to use the cookie.
# The global COOKIES dictionary is no longer populated with the session cookie;
# the session object handles it directly.

# -------------------- Session Initialization --------------------
session = requests.Session()
session.headers.update(HEADERS)
if args.cookie: # Ensure cookie is provided before trying to set it
    session.cookies.set('_inaturalist_session', args.cookie.strip())
    log_message(f"Session cookie set: _inaturalist_session={args.cookie[:5]}...", level=LOG_LEVEL_DEBUG)
else:
    # This case should ideally be prevented by argparse if cookie is required.
    # If execution reaches here without a cookie and it's needed for scraping,
    # unauthenticated requests will be made to photo pages.
    log_message("No session cookie provided. Filename scraping will likely fail or get limited results.", level=LOG_LEVEL_WARNING)


# -------------------- Rate Limit Logic --------------------
def rate_limited_request(method, url, **kwargs):
    global last_request_time
    now = time.time()
    # Ensure at least 1 second (default) between requests to the same domain.
    # The delay can be adjusted if specific API endpoints have different rate limits.
    delay = max(0, 1.0 - (now - last_request_time))
    if delay > 0:
        log_message(f"Rate limiting: sleeping for {delay:.2f} seconds before request to {url}.", level=LOG_LEVEL_DEBUG)
        time.sleep(delay)
    last_request_time = time.time()
    
    params_str = ""
    if "params" in kwargs:
        params_str = str(kwargs["params"])
    log_message(f"Request: {method.upper()} {url} Params: {params_str}", level=LOG_LEVEL_DEBUG)
    
    # Use session object for requests
    # Explicit headers/cookies in kwargs will override session settings for that specific request.
    # For this script, most calls won't need to pass them if session is configured.
    return session.request(method.upper(), url, **kwargs)

# -------------------- Fetch Observation IDs --------------------
def get_observation_ids(username, limit=None):
    page = 1
    per_page = 200
    ids = []

    while True:
        params = {"user_login": username, "page": page, "per_page": per_page}
        # Debug message for API call is now inside rate_limited_request
        try:
            r = rate_limited_request('get', BASE_API_URL, params=params)
            r.raise_for_status() 
            results = r.json().get("results", [])
        except requests.exceptions.RequestException as e:
            log_message(f"Network error while fetching observation IDs page {page} for {username}: {e}", level=LOG_LEVEL_ERROR)
            return ids 
        except json.JSONDecodeError as e:
            log_message(f"JSON decode error while fetching observation IDs page {page} for {username}: {e}", level=LOG_LEVEL_ERROR)
            return ids


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
    # Debug message for API call is now inside rate_limited_request
    try:
        r = rate_limited_request('get', url)
        r.raise_for_status()
        results = r.json().get("results", [])
    except requests.exceptions.RequestException as e:
        log_message(f"Network error while fetching photo IDs for observation {obs_id}: {e}", level=LOG_LEVEL_ERROR)
        return [] 
    except json.JSONDecodeError as e:
        log_message(f"JSON decode error while fetching photo IDs for observation {obs_id}: {e}", level=LOG_LEVEL_ERROR)
        return []
    
    if not results:
        return []
    
    photos = []
    for photo in results[0].get("photos", []):
        photo_id = photo.get("id")
        if photo_id:
            # Get the URL and process it
            url = photo.get("url", "")
            
            log_message(f"Photo {photo_id} API URL: {url}", level=LOG_LEVEL_DEBUG) # Clarified what 'url' is
            
            photos.append({
                "id": photo_id,
                "url": url, # This is the photo's API data URL, not its page URL or direct image URL
                "photo_page_url": f"{INAT_PHOTO_PAGE_URL_BASE}{photo_id}" # Construct page URL using constant
            })
    
    return photos

# -------------------- Scrape Filename and Image URL from Photo Page --------------------
def scrape_photo_page(photo_id):
    url = f"{INAT_PHOTO_PAGE_URL_BASE}{photo_id}"
    # Debug message for request is in rate_limited_request
    try:
        # HEADERS and COOKIES are now handled by the session, so no need to pass them explicitly
        # if the session's configuration is sufficient.
        r = rate_limited_request('get', url) # Removed explicit headers, cookies
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        log_message(f"Network error while scraping photo page {url}: {e}", level=LOG_LEVEL_ERROR)
        return "", None 

    soup = BeautifulSoup(r.text, "html.parser")
    log_message(f"Page title for {url}: {soup.title.string if soup.title else 'No title'}", level=LOG_LEVEL_DEBUG)
        
    filename = ""
    
    # Method 1: Look for table row with "Filename" header
    for tr in soup.find_all('tr'):
        th = tr.find('th')
        if th and th.get_text(strip=True) == HTML_FILENAME_HEADER_TEXT: # Use constant
            td = tr.find('td')
            if td:
                filename = td.get_text(strip=True)
                log_message(f"Found filename in table for {url}: {filename}", level=LOG_LEVEL_DEBUG)
                break
    
    # Method 2: If not found, try looking for data-original-filename attribute (backup)
    if not filename:
        filename_el = soup.select_one(HTML_ORIGINAL_FILENAME_ATTR_SELECTOR) # Use constant
        if filename_el:
            filename = filename_el.get('data-original-filename')
            log_message(f"Found filename in data attribute for {url}: {filename}", level=LOG_LEVEL_DEBUG)
    
    original_size_url = None
    links = soup.find_all('a')
    log_message(f"Found {len(links)} links on page {url}", level=LOG_LEVEL_DEBUG)
        
    for link_element in links: # Renamed variable
        link_text = link_element.get_text(strip=True).lower()
        href = link_element.get('href', '')
        
        is_original_text = link_text == HTML_A_TAG_ORIGINAL_TEXT # Use constant
        contains_size_original = HTML_A_TAG_SIZE_ORIGINAL_SUBSTRING in href # Use constant

        if args.debug and (is_original_text or contains_size_original): # Log potential matches if in debug
             log_message(f"Potential original link on {url}: text='{link_text}', href='{href}'", level=LOG_LEVEL_DEBUG)
        
        if is_original_text and contains_size_original : 
            original_size_url = urljoin(url, href) # Ensure URL is absolute
            log_message(f"Found original size link for {url}: {original_size_url}", level=LOG_LEVEL_DEBUG)
            break
            
    if not original_size_url and args.debug: # If still not found, and debugging, dump all links
        log_message(f"Could not find '{HTML_A_TAG_ORIGINAL_TEXT}' link with '{HTML_A_TAG_SIZE_ORIGINAL_SUBSTRING}' in href on {url}. Dumping all links:", level=LOG_LEVEL_DEBUG)
        for link_element_dump in links: # Renamed variable
            log_message(f"Link dump for {url}: Text='{link_element_dump.get_text(strip=True)}', Href='{link_element_dump.get('href')}'", level=LOG_LEVEL_DEBUG)

    if filename:
        log_message(f"Successfully extracted filename '{filename}' for photo ID {photo_id} from {url}", level=LOG_LEVEL_DEBUG)
    else:
        # This is a warning because it means data might be missing.
        log_message(f"Failed to find filename for photo ID {photo_id} on page {url}", level=LOG_LEVEL_WARNING)

    return filename, original_size_url

# -------------------- Get Actual Image URL from Original Size Page --------------------
def get_actual_image_url(original_link_from_scrape): 
    if not original_link_from_scrape: # Check the argument name used when function is called
        log_message("No original_link_from_scrape provided to get_actual_image_url", level=LOG_LEVEL_ERROR)
        return None
        
    # Debug message for request is in rate_limited_request
    try:
        # Session handles headers and cookies
        r = rate_limited_request('get', original_link_from_scrape) # Removed explicit headers, cookies
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        log_message(f"Network error while getting actual image URL from {original_link_from_scrape}: {e}", level=LOG_LEVEL_ERROR)
        return None

    try:
        log_message(f"Successfully loaded original size page content from {original_link_from_scrape} ({len(r.content)} bytes)", level=LOG_LEVEL_DEBUG)
        soup = BeautifulSoup(r.text, "html.parser")
        
        log_message(f"Looking for img tag with id '{HTML_IMG_ID_SELECTOR}' in {original_link_from_scrape}", level=LOG_LEVEL_DEBUG)
        img_tag = soup.find('img', id=HTML_IMG_ID_SELECTOR) # Use constant
        
        if img_tag and img_tag.get('src'):
            actual_url = urljoin(original_link_from_scrape, img_tag.get('src')) # Ensure absolute URL
            log_message(f"Found actual image URL in img#{HTML_IMG_ID_SELECTOR} at {original_link_from_scrape}: {actual_url}", level=LOG_LEVEL_DEBUG)
            return actual_url
        
        log_message(f"No img#{HTML_IMG_ID_SELECTOR} found in {original_link_from_scrape}, looking for any img src containing '{HTML_A_TAG_ORIGINAL_TEXT}'", level=LOG_LEVEL_DEBUG)
        # Try finding any image that might be the original, e.g., contains "original" in src
        for img_candidate in soup.find_all('img'): # Iterate all img tags
            src_candidate = img_candidate.get('src', '')
            if HTML_A_TAG_ORIGINAL_TEXT in src_candidate.lower(): # Use constant "original"
                actual_url = urljoin(original_link_from_scrape, src_candidate) # Ensure absolute URL
                log_message(f"Found potential image URL via alternate method (img src containing '{HTML_A_TAG_ORIGINAL_TEXT}') in {original_link_from_scrape}: {actual_url}", level=LOG_LEVEL_DEBUG)
                return actual_url
            
        # Fallback to constructing S3 URL if link structure is as expected
        # Check if original_link_from_scrape contains the photo ID structure like "/photos/12345"
        if API_PHOTOS_PATH_SEGMENT in original_link_from_scrape: 
            try:
                photo_id_match = re.search(r'/photos/(\d+)', original_link_from_scrape)
                if photo_id_match:
                    photo_id_str = photo_id_match.group(1)
                    # Try constructing S3 URL with common extensions as a last resort if other methods fail.
                    # This part is more for if get_actual_image_url itself is trying to be exhaustive.
                    # The main S3 fallback with multiple extensions is better suited in direct_download_by_photo_id.
                    # For now, let's assume the primary S3 URL to guess here could be with a default extension.
                    # We'll use the first one from S3_ATTEMPT_EXTENSIONS.
                    guessed_s3_url = f"{S3_PHOTO_URL_BASE}{photo_id_str}/original{S3_ATTEMPT_EXTENSIONS[0]}"
                    log_message(f"Constructed guessed S3 URL (using {S3_ATTEMPT_EXTENSIONS[0]}) as fallback for {original_link_from_scrape}: {guessed_s3_url}", level=LOG_LEVEL_DEBUG)
                    return guessed_s3_url
            except Exception as e: 
                log_message(f"Failed to construct guessed S3 URL from {original_link_from_scrape}: {e}", level=LOG_LEVEL_ERROR)
        
        log_message(f"Could not find any suitable image URL in HTML of {original_link_from_scrape}", level=LOG_LEVEL_WARNING)
        if args.debug: # Only dump HTML if debugging
             log_message(f"Page HTML structure for {original_link_from_scrape} (first 2k chars): {soup.prettify()[:2000]}", level=LOG_LEVEL_DEBUG)
        return None
    except Exception as e: # Catch other potential errors during parsing
        log_message(f"Failed to parse or process content from {original_link_from_scrape}: {e}", level=LOG_LEVEL_ERROR)
        return None

# -------------------- Download Image --------------------
# MIME type to file extension mapping
MIME_TYPE_MAP = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/tiff': '.tif', # or .tiff
    'image/bmp': '.bmp',
    'image/svg+xml': '.svg',
}

def download_image(img_url, fname, obs_id_str): # obs_id is now consistently string here
    try:
        if not img_url or not fname: 
            log_message(f"No valid URL ('{img_url}') or filename ('{fname}') provided for download (obs: {obs_id_str})", level=LOG_LEVEL_ERROR)
            return False
        
        log_message(f"Attempting to download image from: {img_url} for obs {obs_id_str}, filename {fname}", level=LOG_LEVEL_DEBUG)
            
        os.makedirs(args.imagedir, exist_ok=True)
        
        base_fname, orig_ext = os.path.splitext(fname)
        safe_base_fname = re.sub(r'[^\w\-]', '_', base_fname)

        try:
            # Session handles headers. stream=True is a kwarg.
            r = rate_limited_request('get', img_url, stream=True) 
            r.raise_for_status() 
        except requests.exceptions.RequestException as e:
            log_message(f"Network error during image download from {img_url} for obs {obs_id_str}: {e}", level=LOG_LEVEL_ERROR)
            return False
        
        content_type = r.headers.get('content-type', '').split(';')[0].strip().lower()
        ext = MIME_TYPE_MAP.get(content_type)
        warn_msg_ext = "" # Store warning message for extension derivation

        if not ext: # If MIME type not in our explicit map
            warn_msg_prefix = f"For {img_url} (obs {obs_id_str}): "
            if content_type.startswith('image/'):
                derived_ext_part = content_type.split('/')[-1]
                if derived_ext_part and len(derived_ext_part) <= 4 and derived_ext_part.isalnum() and derived_ext_part not in ['html', 'xml', 'plain']: # Basic sanity check
                    ext = "." + derived_ext_part
                    warn_msg_ext = f"Content-Type '{content_type}' was image/*. Using derived extension '{ext}'."
                else: 
                    ext = orig_ext if orig_ext else '.jpg'
                    warn_msg_ext = f"Content-Type '{content_type}' was image/* but subtype complex or non-standard. Falling back to original ext='{orig_ext}' or default '.jpg'."
            elif content_type == 'application/octet-stream' or not content_type : 
                ext = orig_ext if orig_ext else '.jpg'
                warn_msg_ext = f"Generic or missing Content-Type ('{content_type}'). Falling back to original ext='{orig_ext}' or default '.jpg'."
            else: # Not an image type nor octet-stream, this is an error for an image download function
                log_message(f"Downloaded content from {img_url} (obs {obs_id_str}) is not a recognized image type or stream: '{content_type}'. Cannot determine extension.", level=LOG_LEVEL_ERROR)
                if args.debug:
                    try:
                        peek_content = r.iter_content(100).__next__() # Python 3
                        log_message(f"First 100 bytes of content from {img_url}: {peek_content}", level=LOG_LEVEL_DEBUG)
                    except Exception: pass 
                return False
            log_message(warn_msg_prefix + warn_msg_ext, level=LOG_LEVEL_WARNING)
        
        final_fname = f"{obs_id_str}_{safe_base_fname}{ext}"
        out_path = os.path.join(args.imagedir, final_fname)
        
        content_size = int(r.headers.get('content-length', 0))
        if content_size == 0 and r.status_code == 200: # OK if server sends 0 content-length for empty file
             log_message(f"Content-Length is 0 for {img_url} (obs {obs_id_str}), proceeding with download.", level=LOG_LEVEL_DEBUG)

        try:
            downloaded_length = 0
            with open(out_path, "wb") as f_handle: # Renamed to avoid conflict
                for chunk in r.iter_content(chunk_size=8192):
                    f_handle.write(chunk)
                    downloaded_length += len(chunk)
            
            actual_size_on_disk = os.path.getsize(out_path) if os.path.exists(out_path) else -1

            if content_size > 0 and actual_size_on_disk != content_size:
                 log_message(f"Downloaded size ({actual_size_on_disk}) does not match Content-Length ({content_size}) for {out_path} (obs {obs_id_str}). File may be incomplete.", level=LOG_LEVEL_WARNING)
            elif content_size == 0 and actual_size_on_disk > 0 : 
                 log_message(f"Content-Length was 0 but downloaded {actual_size_on_disk} bytes for {out_path} (obs {obs_id_str}).", level=LOG_LEVEL_INFO)


        except IOError as e:
            log_message(f"Could not write image to {out_path} (obs {obs_id_str}): {e}", level=LOG_LEVEL_ERROR)
            return False
                
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            log_message(f"Successfully downloaded: {out_path} (approx {os.path.getsize(out_path)} bytes)", level=LOG_LEVEL_VERBOSE)
            return True
        elif os.path.exists(out_path) and os.path.getsize(out_path) == 0 and content_size == 0: 
            log_message(f"Successfully downloaded 0-byte file (as expected from Content-Length 0): {out_path}", level=LOG_LEVEL_VERBOSE)
            return True
        else: 
            size_info = os.path.getsize(out_path) if os.path.exists(out_path) else "File not found"
            log_message(f"File {out_path} (obs {obs_id_str}) problem: size is {size_info}. URL: {img_url}", level=LOG_LEVEL_ERROR)
            return False
            
    except Exception as e: 
        log_message(f"An unexpected error occurred in download_image for {img_url} (obs {obs_id_str}): {e}", level=LOG_LEVEL_ERROR)
        if args.debug: import traceback; traceback.print_exc() # Show stack trace if debugging
        return False

# -------------------- Direct Download by Photo ID --------------------
def direct_download_by_photo_id(photo_id_str_val, filename_val, obs_id_str_val):
    """Try to download directly using the S3 URL pattern with multiple common extensions."""
    photo_id = str(photo_id_str_val) # Ensure it's a string
    
    for s3_ext in S3_ATTEMPT_EXTENSIONS:
        direct_s3_url = f"{S3_PHOTO_URL_BASE}{photo_id}/original{s3_ext}"
        log_message(f"Obs {obs_id_str_val}, Photo {photo_id}: Attempting direct S3 download with extension {s3_ext}: {direct_s3_url}", level=LOG_LEVEL_DEBUG)
        
        # We need to check if this URL even exists before calling download_image,
        # or let download_image handle the 404.
        # Letting download_image handle it is fine as it reports errors.
        # If download_image succeeds, it returns True.
        if download_image(direct_s3_url, filename_val, obs_id_str_val):
            log_message(f"Obs {obs_id_str_val}, Photo {photo_id}: Direct S3 download successful with {s3_ext}", level=LOG_LEVEL_INFO)
            return True # Success
            
    log_message(f"Obs {obs_id_str_val}, Photo {photo_id}: All S3 direct download attempts failed.", level=LOG_LEVEL_WARNING)
    return False # All attempts failed

# -------------------- Main Execution --------------------
try:
    log_message(f"Script starting. User: {args.username}, Limit: {args.limit}, Download: {args.download}, Image Dir: {args.imagedir}, Output: {output_filename}", level=LOG_LEVEL_INFO)
    obs_ids_list = get_observation_ids(args.username, limit=args.limit) # list of int
    log_message(f"Found {len(obs_ids_list)} observation IDs to process.", level=LOG_LEVEL_INFO)
    
    fieldnames = ["observation_id", "photo_filenames"]
    if args.add_photo_urls:
        fieldnames.extend(["photo_urls", "original_photo_urls"])

    with open(output_filename, "w", newline="", encoding="utf-8") as csvfile_handle: 
        writer = csv.DictWriter(csvfile_handle, fieldnames=fieldnames)
        writer.writeheader()
        log_message(f"Output CSV file opened: {output_filename}", level=LOG_LEVEL_DEBUG)

        total_photos_processed = 0 
        successfully_downloaded_photos = 0 

        for i, current_obs_id_int in enumerate(obs_ids_list, 1): 
            current_obs_id_str = str(current_obs_id_int) 
            log_message(f"({i}/{len(obs_ids_list)}) Processing observation ID: {current_obs_id_str}", level=LOG_LEVEL_INFO)

            try:
                photo_details_list = get_photo_ids(current_obs_id_int) 
                log_message(f"Obs {current_obs_id_str}: Found {len(photo_details_list)} photo entries via API.", level=LOG_LEVEL_DEBUG)
                
                # Prepare lists to store data for the current observation's CSV row
                observation_filenames = [] 
                observation_photo_page_urls = [] # For --add-photo-urls
                observation_original_image_links = [] # For --add-photo-urls
                
                for photo_data in photo_details_list: # photo_data is a dict
                    photo_id_str = str(photo_data["id"]) # Ensure string for consistency
                    photo_page_url = photo_data["photo_page_url"]
                    
                    scraped_filename, scraped_original_link = scrape_photo_page(photo_id_str)
                    
                    if scraped_filename: 
                        observation_filenames.append(scraped_filename)
                        if args.add_photo_urls: # Collect extra URLs if flag is set
                            observation_photo_page_urls.append(photo_page_url)
                            observation_original_image_links.append(scraped_original_link if scraped_original_link else "")
                        
                        total_photos_processed += 1
                        
                        if args.download:
                            photo_download_succeeded = False 
                            
                            if scraped_original_link: 
                                final_image_url = get_actual_image_url(scraped_original_link) 
                                if final_image_url:
                                    photo_download_succeeded = download_image(final_image_url, scraped_filename, current_obs_id_str)
                            
                            if not photo_download_succeeded: 
                                log_message(f"Obs {current_obs_id_str}, Photo {photo_id_str}: Primary download failed or no original link. Trying direct S3.", level=LOG_LEVEL_DEBUG)
                                photo_download_succeeded = direct_download_by_photo_id(photo_id_str, scraped_filename, current_obs_id_str)
                            
                            if photo_download_succeeded:
                                successfully_downloaded_photos += 1
                            else:
                                log_message(f"Obs {current_obs_id_str}, Photo {photo_id_str} (filename: {scraped_filename}): All download methods failed.", level=LOG_LEVEL_ERROR)
                
                if not observation_filenames: 
                    log_message(f"Obs {current_obs_id_str}: No filenames successfully scraped. Skipping CSV row.", level=LOG_LEVEL_INFO)
                    continue

                row_data = { # Renamed variable
                    "observation_id": current_obs_id_str, 
                    "photo_filenames": ";".join(observation_filenames),
                }
                
                if args.add_photo_urls: 
                    row_data["photo_urls"] = ";".join(observation_photo_page_urls) 
                    row_data["original_photo_urls"] = ";".join(observation_original_image_links) 
                    
                writer.writerow(row_data)
                log_message(f"Obs {current_obs_id_str}: {len(observation_filenames)} photo filenames recorded to CSV.", level=LOG_LEVEL_VERBOSE)

            except requests.exceptions.RequestException as e: 
                log_message(f"Obs {current_obs_id_str}: Network error during processing: {e}", level=LOG_LEVEL_ERROR)
                continue 
            except Exception as e: 
                log_message(f"Obs {current_obs_id_str}: Unexpected error during processing: {e}", level=LOG_LEVEL_ERROR)
                if args.debug: import traceback; traceback.print_exc() 
                continue 

except KeyboardInterrupt:
    log_message("Script interrupted by user. Exiting.", level=LOG_LEVEL_INFO)
    sys.exit(130) 
except Exception as e: 
    log_message(f"CRITICAL: A top-level unexpected error occurred: {e}", level=LOG_LEVEL_ERROR)
    if args.debug: import traceback; traceback.print_exc()
    sys.exit(1)

log_message(f"Script finished. Processed {len(obs_ids_list)} observations. Results saved to {output_filename}", level=LOG_LEVEL_INFO)
if args.download:
    log_message(f"Downloaded {successfully_downloaded_photos} of {total_photos_processed} photos considered to {args.imagedir}/", level=LOG_LEVEL_INFO)
