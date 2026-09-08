"""Keeps the dedicated WotoHub browser visible in the upper-right corner."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
with sync_playwright() as pw:
    profile=ROOT/'state'/'profile'
    profile.mkdir(parents=True,exist_ok=True)
    try:
        context=pw.chromium.launch_persistent_context(
            str(profile), channel='msedge', headless=False,
            args=['--window-size=700,800','--window-position=730,20','--remote-debugging-port=9222'],
            viewport=None,
        )
    except Exception:
        context=pw.chromium.launch_persistent_context(
            str(profile), headless=False,
            args=['--window-size=700,800','--window-position=730,20','--remote-debugging-port=9222'], viewport=None,
        )
    page=context.pages[0] if context.pages else context.new_page()
    page.goto('https://www.wotohub.com/workbenchSearch',wait_until='domcontentloaded')
    print('WotoHub persistent browser is open. Close this browser window to stop it.',flush=True)
    page.wait_for_event('close', timeout=0)
