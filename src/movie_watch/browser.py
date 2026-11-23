from __future__ import annotations

from pathlib import Path

from .config import Config, Target, browser_executable, data_dir


def open_bookmyshow_login(config: Config, target: Target) -> None:
    """Open a visible persistent browser so a human can finish Cloudflare/login."""
    from playwright.sync_api import sync_playwright

    user_dir = config.bookmyshow_user_data_dir.strip() or str(data_dir() / "bms-browser")
    Path(user_dir).mkdir(parents=True, exist_ok=True)
    url = target.bookmyshow_url.strip() or (
        f"https://in.bookmyshow.com/explore/movies-{target.city}"
    )

    launch_options: dict[str, object] = {
        "headless": False,
        "viewport": {"width": 1365, "height": 900},
        "locale": "en-IN",
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    browser_path = browser_executable(config.bookmyshow_browser_path)
    if browser_path:
        launch_options["executable_path"] = browser_path
    else:
        print("Warning: no system Chrome/Edge found; using Playwright's bundled browser.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_dir,
            **launch_options,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=int(config.bookmyshow_timeout_seconds * 1000))
            print("\nBrowser opened for target:", target.name)
            print("URL:", url)
            print("Finish any Cloudflare check / login in the browser window.")
            try:
                input("Press Enter here when the movie/showtime page is visible... ")
            except EOFError:
                print("No terminal input available; closing browser.")
        finally:
            context.close()
