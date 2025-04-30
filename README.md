# iNaturalist Data Downloader

**Version:** 1.0  
**Author:** Alan Rockefeller  
**Date:** April 30, 2025  
**License:** GNU GPL 3.0  

## Description

iNaturalist Data Downloader is a command-line utility that helps iNaturalist users retrieve their observation data, including the original filenames of uploaded photos (which are only visible to the uploader) and optionally download the original-sized images.

This tool is particularly useful for photographers and scientists who need to maintain the relationship between their original file organization and their iNaturalist uploads, or who want to back up their full-resolution iNaturalist photos.   Note that iNaturalist reduces all photos to 2000 pixels in size - if you want to store your full resolution images, use http://mushroomobserver.org

## Features

- Retrieves observation IDs via the iNaturalist API
- Gets photo IDs associated with each observation
- Scrapes the original filenames from iNaturalist photo pages using your session cookie
- Creates a CSV file with observation IDs and their associated photo filenames
- Optionally includes photo URLs in the CSV output
- Optionally downloads original-sized images with their original filenames
- Customizable output filename and image download directory
- Detailed debug output option for troubleshooting

## Requirements

- Python 3.6 or higher
- Required Python packages:
  - requests
  - beautifulsoup4
  - argparse

## Installation

1. Ensure Python 3.6+ is installed on your system
2. Install required packages:
   ```
   pip install requests beautifulsoup4
   ```
3. Download the script and make it executable:
   ```
   chmod +x inaturalist_downloader.py
   ```

## Usage

Basic usage:
```
./inaturalist_downloader.py --username YOUR_USERNAME --cookie YOUR_COOKIE_VALUE
```

Full options:
```
./inaturalist_downloader.py --username YOUR_USERNAME --cookie YOUR_COOKIE_VALUE [options]
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `--username USERNAME` | Your iNaturalist username (required) |
| `--cookie COOKIE` | Your iNaturalist session cookie value without the name prefix (required) |
| `--limit N` | Limit number of observations to process |
| `--verbose` | Print each CSV row as it's written |
| `--debug` | Print detailed debug output (API/web scraping info) |
| `--download` | Download original-sized images |
| `--imagedir DIRECTORY` | Specify custom directory for downloaded images (default: ./images/) |
| `--add-photo-urls` | Include photo URLs in the CSV output |
| `-o, --out, --output FILENAME` | Set the output CSV filename (must end with .csv) |
| `--help` | Show help message and exit |

### How To Get Your iNaturalist Session Cookie

1. Log in to https://www.inaturalist.org in Chrome or Firefox.
2. Open DevTools:
   - Chrome: Right-click anywhere → Inspect → Application tab → Storage → Cookies 
   - Firefox: Right-click anywhere → Inspect → Storage tab
3. Under Cookies, find https://www.inaturalist.org
4. Copy only the VALUE of the cookie named `_inaturalist_session`
5. Use it like: `--cookie 2f065b3aba346277da95bec21d559f3a`

## Examples

Basic usage (creates inaturalist_filenames.csv):
```
./inaturalist_downloader.py --username alan_rockefeller --cookie YOUR_COOKIE_VALUE
```

Download original images for your most recent 10 observations: (good for testing)
```
./inaturalist_downloader.py --username alan_rockefeller --cookie YOUR_COOKIE_VALUE --limit 10 --download
```

Full options with custom output file and image directory:
```
./inaturalist_downloader.py --username alan_rockefeller --cookie YOUR_COOKIE_VALUE --add-photo-urls --download --imagedir my_pictures --output my_photo_data.csv
```

## Output Format

### CSV Output

The CSV file contains the following columns:
- `observation_id` - The iNaturalist observation ID
- `photo_filenames` - Semicolon-separated list of original filenames for photos in this observation

If `--add-photo-urls` is specified, these additional columns are included:
- `photo_urls` - Semicolon-separated list of URLs to the photos on iNaturalist
- `original_photo_urls` - Semicolon-separated list of URLs to the original-sized photos

### Downloaded Images

When using the `--download` option:
- Images are saved to the specified directory (default: ./images/)
- Filenames are in the format: `OBSERVATION_ID_ORIGINAL_FILENAME`
- All images are downloaded at their original, full resolution

## Limitations

- The script can only retrieve filenames for observations that belong to the user who owns the session cookie.
- Rate limiting is implemented to avoid overloading the iNaturalist servers, so processing large numbers of observations may take some time.

## Troubleshooting

If you encounter issues:

1. Try running with the `--debug` flag to see detailed information about what's happening
2. Ensure your session cookie is valid and current (session cookies expire)
3. Check your internet connection
4. Verify you have write permissions in the output directory

## Contributing

- Submit a pull request via https://github.com/AlanRockefeller/inat.photodownloader.py
- Or contact Alan Rockefeller via iNaturalist / Facebook / LinkedIN / Instagram / Email


## License

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License version 3.0.

This program is distributed in the hope that it will be useful,
WITH FULL WARRANTY; if it doesn't work contact Alan and he will fix
it or add which ever feature you need.
