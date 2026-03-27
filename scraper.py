"""
Inglis 2026 Easter Yearling Sale – Catalogue Scraper & PDF Generator
====================================================================
Scrapes all 472 lots from the dynamic SPA catalogue, downloads primary
photos via Gallery links, and compiles everything into a polished A4 PDF.

Strategy:
    1. Load the SPA catalogue page once (networkidle wait)
    2. For each lot, use the "Jump to Lot" input to navigate
    3. Parse "Lot N : Sire/Dam" from the page text
    4. Download the high-res gallery image (first <a href="...-main.jpg">)
    5. Resize images to max 500px width
    6. Compile into PDF with ReportLab

Usage:
    python scraper.py
"""

import os
import re
import shutil
import time
import traceback

import requests
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_IMG_DIR = os.path.join(BASE_DIR, "temp_images")
OUTPUT_PDF = os.path.join(BASE_DIR, "inglis_easter_2026_catalogue.pdf")

CATALOGUE_URL = (
    "https://inglis.com.au/sale/"
    "2026-australian-easter-yearling-sale?tab=catalogue"
)
TOTAL_LOTS = 472
DELAY_SECONDS = 1.5           # polite delay between lot navigations
IMAGE_MAX_WIDTH = 500          # px – for Pillow resize
IMAGE_QUALITY = 85             # JPEG quality after resize

# ---------------------------------------------------------------------------
# Stage 1 – Scrape all lots
# ---------------------------------------------------------------------------

def scrape_lots():
    """Navigate the SPA catalogue, extracting data and images for each lot."""
    os.makedirs(TEMP_IMG_DIR, exist_ok=True)

    results = []       # list of dicts
    error_lots = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        # ── Load the catalogue SPA ────────────────────────────────────
        print(f"Loading catalogue: {CATALOGUE_URL}")
        page.goto(CATALOGUE_URL, wait_until="networkidle", timeout=60_000)
        time.sleep(8)  # extra wait for Livewire hydration
        print("Catalogue loaded.\n")

        for lot_num in range(1, TOTAL_LOTS + 1):
            lot_data = {
                "lot": lot_num, "sire": None, "dam": None,
                "image_path": None, "error": None,
            }
            try:
                # ── Navigate to lot ────────────────────────────────────
                if lot_num > 1:
                    _jump_to_lot(page, lot_num)

                # ── Extract sire & dam ─────────────────────────────────
                sire, dam = _extract_sire_dam(page, lot_num)
                lot_data["sire"] = sire
                lot_data["dam"] = dam

                # ── Download primary gallery image ─────────────────────
                img_url = _extract_image_url(page)
                if img_url:
                    img_path = os.path.join(TEMP_IMG_DIR, f"lot_{lot_num}.jpg")
                    _download_image(img_url, img_path)
                    lot_data["image_path"] = img_path
                else:
                    lot_data["error"] = "Image not found"

                status = "✓" if lot_data["image_path"] else "✗"
                print(
                    f"[{lot_num:>3}/{TOTAL_LOTS}] "
                    f"Sire: {sire or '?':25s}  "
                    f"Dam: {dam or '?':25s}  "
                    f"Img: {status}"
                )

            except Exception as exc:
                lot_data["error"] = str(exc)
                error_lots.append(lot_num)
                print(f"[{lot_num:>3}/{TOTAL_LOTS}] ✖ ERROR: {exc}")

            results.append(lot_data)

            # Polite delay
            if lot_num < TOTAL_LOTS:
                time.sleep(DELAY_SECONDS)

        browser.close()

    n_ok = sum(1 for r in results if r.get("sire"))
    n_img = sum(1 for r in results if r.get("image_path"))
    print(f"\n{'='*60}")
    print(f"Scraping complete.  Lots: {len(results)}  "
          f"Data OK: {n_ok}  Images: {n_img}  "
          f"Errors: {len(error_lots)}")
    if error_lots:
        print(f"Error lots: {error_lots}")
    print(f"{'='*60}\n")
    return results


def _jump_to_lot(page, lot_num):
    """Use the Jump-to-Lot input to navigate to a specific lot."""
    jump_input = page.query_selector("input.py-1.px-3")
    if not jump_input:
        # Broader fallback
        inputs = page.query_selector_all("input[type='text']")
        jump_input = inputs[-1] if inputs else None

    if not jump_input:
        raise RuntimeError("Jump-to-Lot input not found")

    jump_input.click()
    jump_input.fill("")
    time.sleep(0.2)
    jump_input.fill(str(lot_num))
    page.keyboard.press("Enter")

    # Wait for content to update (wait for the lot number in page text)
    deadline = time.time() + 10
    while time.time() < deadline:
        body = page.inner_text("body")
        if re.search(rf"Lot\s+{lot_num}\s*:", body, re.I):
            return
        time.sleep(0.5)

    # If we didn't find it, wait a bit more and move on
    time.sleep(2)


def _extract_sire_dam(page, lot_num):
    """Parse 'Lot N : Sire/Dam' from the rendered page text."""
    body = page.inner_text("body")

    # Primary pattern: "Lot N : Sire/Dam"
    m = re.search(
        rf"Lot\s+{lot_num}\s*:\s*(.+?)(?:\n|$)", body, re.I | re.M
    )
    if m:
        after_colon = m.group(1).strip()
        parts = after_colon.split("/", 1)
        sire = parts[0].strip() if parts else None
        dam = parts[1].strip() if len(parts) > 1 else None
        return sire, dam

    return None, None


def _extract_image_url(page):
    """Get the first high-res gallery image URL (…-main.jpg)."""
    # Strategy 1: <a> with href ending in -main.jpg
    try:
        anchors = page.query_selector_all("a[href*='-main.jpg']")
        if anchors:
            return anchors[0].get_attribute("href")
    except Exception:
        pass

    # Strategy 2: via JS – find <img> inside gallery whose parent <a> has
    #             an href to the hi-res version
    try:
        result = page.evaluate("""
            () => {
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const link = img.closest('a');
                    if (link && link.href && link.href.includes('-main.jpg')) {
                        return link.href;
                    }
                }
                return null;
            }
        """)
        if result:
            return result
    except Exception:
        pass

    # Strategy 3: fallback to thumbnail (<img> with webcontent src)
    try:
        imgs = page.query_selector_all("img[src*='webcontent.inglis.com.au']")
        for img in imgs:
            src = img.get_attribute("src") or ""
            if "-thumb" in src:
                return src.replace("-thumb", "-main")
    except Exception:
        pass

    return None


def _download_image(url, dest_path):
    """Download an image to disk."""
    if not url.startswith("http"):
        url = "https:" + url
    resp = requests.get(
        url, timeout=30, stream=True,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)

# ---------------------------------------------------------------------------
# Stage 2 – Process images
# ---------------------------------------------------------------------------

def process_images(results):
    """Resize downloaded images to max width for manageable PDF size."""
    print("Processing images...")
    processed = 0
    for lot_data in results:
        img_path = lot_data.get("image_path")
        if not img_path or not os.path.isfile(img_path):
            continue
        try:
            with Image.open(img_path) as img:
                if img.width > IMAGE_MAX_WIDTH:
                    ratio = IMAGE_MAX_WIDTH / img.width
                    new_size = (IMAGE_MAX_WIDTH, int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(img_path, "JPEG", quality=IMAGE_QUALITY)
                processed += 1
        except Exception as exc:
            print(f"  ⚠ Lot {lot_data['lot']}: {exc}")
            lot_data["image_path"] = None
    print(f"Image processing complete. {processed} images resized.\n")

# ---------------------------------------------------------------------------
# Stage 3 – Generate PDF
# ---------------------------------------------------------------------------

def generate_pdf(results):
    """Build a polished A4 PDF catalogue."""
    print(f"Generating PDF → {OUTPUT_PDF}")

    doc = SimpleDocTemplate(
        OUTPUT_PDF,
        pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CatTitle", parent=styles["Title"],
        fontSize=22, leading=28, textColor=HexColor("#1a1a2e"),
        spaceAfter=6 * mm, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "CatSub", parent=styles["Normal"],
        fontSize=11, leading=14, textColor=HexColor("#555555"),
        spaceAfter=12 * mm, alignment=TA_CENTER,
    )
    lot_hdr = ParagraphStyle(
        "LotHdr", parent=styles["Heading2"],
        fontSize=14, leading=18, textColor=HexColor("#0d47a1"),
        spaceBefore=4 * mm, spaceAfter=2 * mm, alignment=TA_LEFT,
    )
    lot_det = ParagraphStyle(
        "LotDet", parent=styles["Normal"],
        fontSize=11, leading=14, textColor=HexColor("#333333"),
        spaceAfter=3 * mm,
    )
    missing = ParagraphStyle(
        "Miss", parent=styles["Normal"],
        fontSize=10, textColor=HexColor("#cc0000"),
        alignment=TA_CENTER, spaceAfter=4 * mm,
    )

    story = []

    # ── Title page ─────────────────────────────────────────────────────
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(
        "2026 Inglis Easter Yearling Sale", title_style))
    story.append(Paragraph(
        "Complete Catalogue — 472 Lots", subtitle_style))
    story.append(Paragraph(
        "Riverside Stables, Sydney  ·  29–30 March 2026", subtitle_style))
    story.append(PageBreak())

    PAGE_W = A4[0] - 3 * cm
    MAX_IMG_W = PAGE_W * 0.85
    MAX_IMG_H = 200  # pts

    lots_on_page = 0

    for lot_data in results:
        lot_num = lot_data["lot"]
        sire = lot_data.get("sire") or "Unknown"
        dam = lot_data.get("dam") or "Unknown"
        img_path = lot_data.get("image_path")

        story.append(Paragraph(f"<b>Lot {lot_num}</b>", lot_hdr))
        story.append(Paragraph(
            f"<b>Sire:</b> {sire} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Dam:</b> {dam}", lot_det,
        ))

        if img_path and os.path.isfile(img_path):
            try:
                img = RLImage(img_path)
                iw, ih = img.drawWidth, img.drawHeight
                scale = min(MAX_IMG_W / iw, MAX_IMG_H / ih, 1.0)
                img.drawWidth = iw * scale
                img.drawHeight = ih * scale
                img.hAlign = "CENTER"
                story.append(img)
            except Exception:
                story.append(Paragraph("[Image error]", missing))
        else:
            story.append(Paragraph("No Image Available", missing))

        story.append(Spacer(1, 6 * mm))
        lots_on_page += 1
        if lots_on_page >= 2:
            story.append(PageBreak())
            lots_on_page = 0

    doc.build(story)
    size_mb = os.path.getsize(OUTPUT_PDF) / (1024 * 1024)
    print(f"PDF generated: {OUTPUT_PDF} ({size_mb:.1f} MB)\n")

# ---------------------------------------------------------------------------
# Stage 4 – Cleanup
# ---------------------------------------------------------------------------

def cleanup():
    """Remove temporary image folder."""
    if os.path.isdir(TEMP_IMG_DIR):
        shutil.rmtree(TEMP_IMG_DIR)
        print("Temporary images cleaned up.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Inglis 2026 Easter Yearling Sale – Catalogue Scraper")
    print("=" * 60, "\n")

    # Stage 1
    results = scrape_lots()

    # Stage 2
    process_images(results)

    # Stage 3
    generate_pdf(results)

    # Summary
    total = len(results)
    with_img = sum(
        1 for r in results
        if r.get("image_path") and os.path.isfile(r["image_path"])
    )
    with_data = sum(1 for r in results if r.get("sire") and r.get("dam"))
    errors = sum(1 for r in results if r.get("error"))

    print("=" * 60)
    print("  SUMMARY")
    print(f"  Total lots:        {total}")
    print(f"  With sire/dam:     {with_data}")
    print(f"  With image:        {with_img}")
    print(f"  Errors/missing:    {errors}")
    print(f"  Output:            {OUTPUT_PDF}")
    print("=" * 60)

    # Stage 4 – Cleanup
    if os.path.isfile(OUTPUT_PDF) and os.path.getsize(OUTPUT_PDF) > 0:
        cleanup()
    else:
        print("⚠ PDF not verified – keeping temp images.")


if __name__ == "__main__":
    main()
