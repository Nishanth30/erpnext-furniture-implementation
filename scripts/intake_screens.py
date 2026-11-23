"""Screenshots of the order intake form (service must be running on :8090)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

IMG = Path(__file__).resolve().parent.parent / "docs" / "img"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    page = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    page.goto("http://localhost:8090/?token=PADI-01")
    page.select_option("select[name=item0]", "WRD-3D")
    page.fill("input[name=qty0]", "2")
    page.select_option("select[name=item1]", "COT-STOR-Q")
    page.fill("input[name=qty1]", "1")
    page.screenshot(path=str(IMG / "13-intake-form.png"))
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(IMG / "13b-intake-order-created.png"))
    b.close()
print("intake screenshots saved")
