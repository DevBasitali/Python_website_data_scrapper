"""
Test: Navigate lots via Jump-to-Lot and extract gallery images from rendered DOM.
"""

import os, re, time
from playwright.sync_api import sync_playwright

TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_images")
os.makedirs(TEMP_DIR, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    # Load catalogue
    url = "https://inglis.com.au/sale/2026-australian-easter-yearling-sale?tab=catalogue"
    print(f"Loading: {url}")
    page.goto(url, wait_until="networkidle", timeout=60000)
    time.sleep(8)
    print("Loaded!\n")

    # Test on Lot 1 (already loaded)
    for lot_num in [1, 2, 10]:
        print(f"\n{'='*60}")
        print(f"LOT {lot_num}")

        if lot_num > 1:
            # Use Jump to Lot
            jump_input = page.query_selector("input.py-1.px-3")
            if not jump_input:
                # Fallback: try by placeholder or position
                inputs = page.query_selector_all("input")
                jump_input = inputs[-1] if inputs else None
            
            if jump_input:
                print(f"  Found jump input, navigating to lot {lot_num}...")
                jump_input.click()
                jump_input.fill("")
                jump_input.fill(str(lot_num))
                page.keyboard.press("Enter")
                time.sleep(4)
            else:
                print("  ✗ No jump input found!")
                continue

        body = page.inner_text("body")

        # Extract title
        m = re.search(rf"Lot\s+{lot_num}\s*:\s*(.+)", body, re.I)
        if m:
            title = m.group(0).strip()
            print(f"  Title: {title}")
            # Parse sire/dam
            after_colon = m.group(1).strip()
            parts = after_colon.split("/", 1)
            sire = parts[0].strip() if parts else "?"
            dam = parts[1].strip() if len(parts) > 1 else "?"
            print(f"  Sire: {sire}")
            print(f"  Dam: {dam}")
        else:
            print(f"  ✗ Title not found")

        # Now let's find all images in the rendered DOM
        print("\n  --- All <img> elements ---")
        all_imgs = page.query_selector_all("img")
        for i, img in enumerate(all_imgs):
            src = img.get_attribute("src") or ""
            alt = img.get_attribute("alt") or ""
            if "inglis" in src.lower() or "webcontent" in src.lower():
                print(f"  img[{i}]: src={src[:120]}")
                print(f"          alt={alt}")

        # Find all <a> tags with image-like hrefs
        print("\n  --- All <a> with image hrefs ---")
        all_anchors = page.query_selector_all("a")
        for i, a in enumerate(all_anchors):
            href = a.get_attribute("href") or ""
            if any(ext in href.lower() for ext in ['.jpg', '.png', '.jpeg', '.webp']):
                print(f"  a[{i}]: href={href[:150]}")

        # Check full HTML for this section
        gallery_els = page.query_selector_all("[class*='gallery'], [class*='Gallery']")
        print(f"\n  Gallery-class elements: {len(gallery_els)}")

        # Look for data URLs or blob URLs
        all_src = page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('img')).map(img => ({
                    src: img.src,
                    alt: img.alt,
                    width: img.naturalWidth,
                    tag: img.closest('a')?.href || 'no-link'
                })).filter(x => x.src.includes('webcontent') || x.src.includes('inglis'))
            }
        """)
        print(f"\n  --- JS-extracted images ({len(all_src)}) ---")
        for item in all_src:
            print(f"  src: {item['src'][:120]}")
            print(f"  alt: {item['alt']}, size: {item['width']}px, link: {item['tag'][:120]}")

        time.sleep(2)

    browser.close()

print("\nDone!")
