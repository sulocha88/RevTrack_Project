import requests
from bs4 import BeautifulSoup
import json
import time
import random
import sys
from urllib.parse import urlparse

# Fix Windows encoding issues
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Retry configuration - Optimized for speed
MAX_RETRIES = 2  # Reduced retries
RETRY_DELAY = 2  # Reduced delay

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

def get_random_user_agent():
    """Get a random user agent from the list"""
    return random.choice(USER_AGENTS)

def sleep_with_jitter(base_delay):
    """Sleep with random jitter to avoid detection"""
    jitter = random.uniform(0.5, 1.5)
    time.sleep(base_delay * jitter)

def calculate_retry_delay(attempt):
    """Calculate exponential backoff delay"""
    return RETRY_DELAY * (2 ** attempt)

def make_request(url, attempt=0):
    """Make HTTP request with retry logic and rate limiting handling"""
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)

        # Check for rate limiting
        if response.status_code == 429:
            if attempt < MAX_RETRIES:
                delay = calculate_retry_delay(attempt)
                print(f"Rate limited (429). Waiting {delay} seconds before retry...", file=sys.stderr)
                sleep_with_jitter(delay)
                return make_request(url, attempt + 1)
            else:
                print(f"Rate limit exceeded after {MAX_RETRIES} attempts", file=sys.stderr)
                return None

        # Check for other client errors
        if response.status_code >= 400 and response.status_code < 500:
            print(f"Client error {response.status_code} for URL: {url}", file=sys.stderr)
            return None

        # Check for server errors
        if response.status_code >= 500:
            if attempt < MAX_RETRIES:
                delay = calculate_retry_delay(attempt)
                print(f"Server error {response.status_code}. Retrying in {delay} seconds...", file=sys.stderr)
                sleep_with_jitter(delay)
                return make_request(url, attempt + 1)
            else:
                print(f"Server error {response.status_code} after {MAX_RETRIES} attempts", file=sys.stderr)
                return None

        return response

    except requests.exceptions.Timeout:
        if attempt < MAX_RETRIES:
            delay = calculate_retry_delay(attempt)
            print(f"Request timeout. Retrying in {delay} seconds...", file=sys.stderr)
            sleep_with_jitter(delay)
            return make_request(url, attempt + 1)
        else:
            print(f"Request timeout after {MAX_RETRIES} attempts", file=sys.stderr)
            return None

    except requests.exceptions.ConnectionError:
        if attempt < MAX_RETRIES:
            delay = calculate_retry_delay(attempt)
            print(f"Connection error. Retrying in {delay} seconds...", file=sys.stderr)
            sleep_with_jitter(delay)
            return make_request(url, attempt + 1)
        else:
            print(f"Connection error after {MAX_RETRIES} attempts", file=sys.stderr)
            return None

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return None

if len(sys.argv) < 2:
    print(json.dumps({"error": "Please provide the Amazon review URL."}))
    sys.exit(1)

reviews_url = sys.argv[1]
max_pages = 3  # Reduced from 10 to 3 for faster processing

def reviewsHtml(url, max_pages):
    """Scrape review pages with improved error handling"""
    soups = []

    for page_no in range(1, max_pages + 1):
        # Construct the paginated URL
        paginated_url = f"{url}/ref=cm_cr_getr_d_paging_btm_next_{page_no}?pageNumber={page_no}"

        print(f"Scraping page {page_no}/{max_pages}", file=sys.stderr)

        response = make_request(paginated_url)

        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            soups.append(soup)

            # Shorter delay between pages for speed
            if page_no < max_pages:
                sleep_with_jitter(1)  # Reduced to 1 second between pages
        else:
            print(f"Failed to retrieve page {page_no}.", file=sys.stderr)
            break

    return soups

def extract_reviews(soups):
    """Extract reviews from scraped pages"""
    review_texts_list = []
    review_ratings_list = []

    for soup in soups:
        # Extract review texts
        review_texts = soup.find_all("span", class_="review-text")
        review_texts_list.extend([text.text.strip() for text in review_texts])

        # Extract review ratings
        review_ratings = soup.find_all("i", class_="review-rating")
        review_ratings_list.extend([rating.text.strip() for rating in review_ratings])

    # Combine extracted fields into a structured list of dictionaries
    all_reviews = [
        {"Description": text, "Stars": rating}
        for text, rating in zip(review_texts_list, review_ratings_list)
    ]
    return all_reviews

# Main execution
if __name__ == "__main__":
    try:
        print("Starting review scraping...", file=sys.stderr)
        soups = reviewsHtml(reviews_url, max_pages)

        if soups:
            all_reviews = extract_reviews(soups)
            print(f"Extracted {len(all_reviews)} reviews", file=sys.stderr)

            # Randomly select 25 reviews if more than 25 are available (reduced for speed)
            if len(all_reviews) > 25:
                selected_reviews = random.sample(all_reviews, 25)
            else:
                selected_reviews = all_reviews

            # Output the JSON data
            print(json.dumps(selected_reviews, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"error": "No data retrieved from any pages."}))

    except Exception as e:
        print(json.dumps({"error": f"An error occurred: {str(e)}"}))
        sys.exit(1)
