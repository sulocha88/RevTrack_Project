import sys
import json
import re
import time
import random
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Fix Windows encoding issues
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Retry configuration - Optimized for speed
# Keep retries low so the overall script duration stays reasonable when called from Node
MAX_RETRIES = 1  # At most 2 total attempts per process
RETRY_DELAY = 3  # Reduced delay for quicker retries

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

async def sleep_with_jitter(base_delay):
    """Sleep with random jitter to avoid detection"""
    jitter = random.uniform(0.5, 1.5)
    await asyncio.sleep(base_delay * jitter)

def calculate_retry_delay(attempt):
    """Calculate exponential backoff delay"""
    return RETRY_DELAY * (2 ** attempt)

if len(sys.argv) < 2:
    print(json.dumps({"error": "Please provide the Amazon product URL."}))
    sys.exit(1)

product_url = sys.argv[1]

async def scrape_amazon_product(url: str, attempt: int = 0) -> dict:
    """
    Scrapes product data from an Amazon product page URL using Playwright,
    emulating a real browser to avoid getting blocked.
    """
    data = {
        "asin": "N/A",
        "title": "N/A",
        "price": "N/A",
        "original_price": None,
        "discount_percentage": None,
        "rating": "0",
        "reviewCount": "0",
        "availability": "N/A",
        "features": [],
        "description": "N/A",
        "images": []
    }

    # Extract ASIN from URL - this is generally domain-agnostic
    try:
        asin_match = re.search(r'/(dp|gp/product|ASIN)/([A-Z0-9]{10})', url.split('?')[0])
        if not asin_match:
            return {"error": "Could not find a valid 10-character ASIN in the URL. Please check the link."}
        data["asin"] = asin_match.group(2)
    except Exception as e:
        return {"error": f"Invalid URL format: {e}"}

    async with async_playwright() as p:
        try:
            print(f"Attempting to scrape product (attempt {attempt + 1}/{MAX_RETRIES + 1})", file=sys.stderr)

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-first-run',
                    '--disable-default-apps',
                    '--disable-features=VizDisplayCompositor',
                    '--window-size=1920,1080'
                ]
            )
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            page = await context.new_page()

            # Apply stealth measures manually to make the browser less detectable.
            stealth_script = """
              Object.defineProperty(navigator, 'webdriver', { get: () => false });
              Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
              Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
              Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
              Object.defineProperty(navigator, 'cookieEnabled', { get: () => true });
            """
            await page.add_init_script(stealth_script)

            # Minimal delay before navigation for speed
            await sleep_with_jitter(1)  # Reduced from 3 to 1 second

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)  # Reduced to 30s

            # Wait for a key element to ensure the page is loaded correctly
            await page.wait_for_selector("#productTitle", timeout=15000)  # Reduced to 15s

            # Minimal delay after page load
            await sleep_with_jitter(0.5)  # Reduced delay

            # --- Scrape Data ---
            try:
                # First, try to get the full title from the hidden input field
                hidden_input = page.locator('input[name="productTitle"]').first
                if await hidden_input.count():
                    title_value = await hidden_input.get_attribute('value')
                    if title_value and title_value.strip():
                        data['title'] = title_value.strip()
                    else:
                        raise Exception("Hidden input exists but has no value")
                else:
                    # Fallback to visible span element if hidden input doesn't exist
                    visible_title = page.locator('span#productTitle').first
                    if await visible_title.count():
                        data['title'] = (await visible_title.inner_text(timeout=20000)).strip()
                    else:
                        # Final fallback to any element with productTitle ID
                        data['title'] = (await page.locator('#productTitle').first.inner_text(timeout=20000)).strip()
            except Exception as e:
                print(f"Warning: Could not extract title: {e}", file=sys.stderr)

            # Price
            try:
                price_elem = page.locator('span.a-price .a-offscreen').first
                if await price_elem.count():
                    data['price'] = await price_elem.inner_text()
            except Exception as e:
                print(f"Warning: Could not extract price: {e}", file=sys.stderr)

            # Original Price & Discount
            try:
                original_price_elem = page.locator('span[data-a-strike="true"] span.a-offscreen').first
                if await original_price_elem.count():
                    data['original_price'] = await original_price_elem.inner_text()

                discount_elem = page.locator('span.savingsPercentage').first
                if await discount_elem.count():
                    discount_text = (await discount_elem.inner_text()).strip()
                    data['discount_percentage'] = re.sub(r'[^0-9]', '', discount_text)
            except Exception as e:
                print(f"Warning: Could not extract pricing info: {e}", file=sys.stderr)

            # Rating
            try:
                rating_elem = page.locator('#acrPopover').first
                if await rating_elem.count():
                    rating_text = await rating_elem.get_attribute("title") or ""
                    match = re.search(r'(\d+[\.,]?\d*)', rating_text)
                    if match:
                        data['rating'] = match.group(1).replace(',', '.')
            except Exception as e:
                print(f"Warning: Could not extract rating: {e}", file=sys.stderr)

            # Review Count
            try:
                review_count_elem = page.locator('#acrCustomerReviewText').first
                if await review_count_elem.count():
                    review_count_text = (await review_count_elem.inner_text()).strip()
                    data['reviewCount'] = re.sub(r'[^\d]', '', review_count_text.split()[0])
            except Exception as e:
                print(f"Warning: Could not extract review count: {e}", file=sys.stderr)

            # Availability
            try:
                availability_elem = page.locator('#availability').first
                if await availability_elem.count():
                    data['availability'] = (await availability_elem.inner_text()).strip()
            except Exception as e:
                print(f"Warning: Could not extract availability: {e}", file=sys.stderr)

            # Features (Bullet Points)
            try:
                features = await page.locator('#feature-bullets ul li span.a-list-item').all()
                data['features'] = [(await f.inner_text()).strip() for f in features if (await f.inner_text()).strip()]
            except Exception as e:
                print(f"Warning: Could not extract features: {e}", file=sys.stderr)

            # Description
            try:
                desc_elem = page.locator('#productDescription').first
                if await desc_elem.count():
                    data['description'] = (await desc_elem.inner_text()).strip()
            except Exception as e:
                print(f"Warning: Could not extract description: {e}", file=sys.stderr)

            # Images
            try:
                image_elems = await page.locator('#altImages ul li span.a-button-text img').all()
                images = [re.sub(r'\._AC_.*?_\.', '._AC_SL1500_.', await img.get_attribute("src") or "") for img in image_elems]
                # Filter out placeholder/blank images
                data['images'] = [img for img in images if img and 'images/I/01' not in img]
            except Exception as e:
                print(f"Warning: Could not extract images: {e}", file=sys.stderr)

            await browser.close()
            return {"data": data}

        except PlaywrightTimeoutError:
            if attempt < MAX_RETRIES:
                delay = calculate_retry_delay(attempt)
                print(f"Timeout occurred. Retrying in {delay} seconds...", file=sys.stderr)
                await sleep_with_jitter(delay)
                return await scrape_amazon_product(url, attempt + 1)
            else:
                return {"error": f"Timeout while loading page after {MAX_RETRIES + 1} attempts: {url}. The page may be blocked or too slow."}

        except Exception as e:
            error_msg = str(e)
            if attempt < MAX_RETRIES:
                # Check if this looks like a rate limiting or blocking error
                if any(keyword in error_msg.lower() for keyword in ['blocked', 'rate limit', 'too many requests', '429', 'captcha', 'access denied']):
                    delay = calculate_retry_delay(attempt)
                    print(f"Rate limiting detected. Retrying in {delay} seconds...", file=sys.stderr)
                    await sleep_with_jitter(delay)
                    return await scrape_amazon_product(url, attempt + 1)
                else:
                    delay = calculate_retry_delay(attempt)
                    print(f"Error occurred: {error_msg}. Retrying in {delay} seconds...", file=sys.stderr)
                    await sleep_with_jitter(delay)
                    return await scrape_amazon_product(url, attempt + 1)
            else:
                return {"error": f"Failed after {MAX_RETRIES + 1} attempts: {error_msg}"}

if __name__ == "__main__":
    try:
        result = asyncio.run(scrape_amazon_product(product_url))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": f"Unexpected error: {str(e)}"}))
        sys.exit(1)
