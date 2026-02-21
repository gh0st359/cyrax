"""
CYRAX Browser Automation Module
Playwright-based browser control for web interaction, crawling, and exploitation.
"""

import base64
import json
import re
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

from utils.logging import get_logger


class BrowserResult:
    """Result of a browser action."""

    def __init__(
        self,
        action: str,
        success: bool,
        data: str = "",
        error: str = "",
        screenshot_path: str = "",
        url: str = "",
    ):
        self.action = action
        self.success = success
        self.data = data
        self.error = error
        self.screenshot_path = screenshot_path
        self.url = url

    @property
    def output(self) -> str:
        if not self.success:
            return f"Browser error: {self.error}"
        parts = []
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.data:
            parts.append(self.data)
        if self.screenshot_path:
            parts.append(f"Screenshot saved: {self.screenshot_path}")
        return "\n".join(parts) if parts else "OK"


class BrowserManager:
    """
    Manages a Playwright browser instance for CYRAX agents.
    Provides high-level methods for web interaction, crawling, and exploitation testing.
    """

    def __init__(self, headless: bool = True, work_dir: str = "/tmp/cyrax"):
        self.headless = headless
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir = self.work_dir / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._screenshot_counter = 0
        self._started = False

    @staticmethod
    def _suppress_greenlet_errors(loop, context):
        """Suppress greenlet thread-switching errors from Playwright callbacks.

        These occur when Playwright's async callbacks fire on a different thread
        than the one that created the browser (e.g., during sub-agent execution).
        They are harmless but spam the console.
        """
        exception = context.get("exception")
        if exception and "greenlet" in str(type(exception).__module__).lower():
            return  # Silently ignore greenlet errors
        if "greenlet" in context.get("message", "").lower():
            return
        if "Cannot switch to a different thread" in context.get("message", ""):
            return
        # For non-greenlet errors, use default handler
        loop.default_exception_handler(context)

    def _ensure_started(self):
        """Lazily start the browser on first use."""
        if self._started:
            return
        try:
            from playwright.sync_api import sync_playwright

            # Suppress greenlet "Cannot switch to a different thread" errors
            # that occur when Playwright callbacks fire on a non-creating thread.
            # These are harmless but noisy in the console.
            # Note: background threads (e.g. _threaded_chat) don't get an event
            # loop automatically in Python 3.10+, so we create one if needed.
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("closed")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.set_exception_handler(self._suppress_greenlet_errors)

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(30000)
            self._started = True
            get_logger().info("Browser started (Chromium, headless)")
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start browser: {e}")

    @staticmethod
    def preflight() -> tuple[bool, str]:
        """Check whether the browser stack is usable without starting it.

        Returns (available: bool, message: str).  Call this before the first
        browser action to give the operator an actionable diagnosis instead of
        a raw exception.

        Checks:
        1. playwright Python package is importable.
        2. At least one Chromium/Chrome executable is discoverable.
        """
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, (
                "Playwright package not installed. Fix: pip install playwright && playwright install chromium"
            )

        # Attempt to locate the Playwright-managed Chromium binary
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                chromium_path = p.chromium.executable_path
                if not chromium_path or not Path(chromium_path).exists():
                    return False, (
                        f"Chromium binary not found at '{chromium_path}'. "
                        "Fix: playwright install chromium"
                    )
        except Exception as exc:
            return False, (
                f"Browser preflight failed: {exc}. "
                "Fix: playwright install chromium"
            )

        return True, "Browser stack is available (Playwright + Chromium found)."

    def available(self) -> bool:
        """Return True if the browser stack appears usable (non-throwing check)."""
        ok, _ = self.preflight()
        return ok

    # === Navigation ===

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> BrowserResult:
        """Navigate to a URL."""
        self._ensure_started()
        try:
            response = self._page.goto(url, wait_until=wait_until, timeout=30000)
            status = response.status if response else "unknown"
            return BrowserResult(
                action="goto",
                success=True,
                data=f"Navigated to {url} (HTTP {status})\nTitle: {self._page.title()}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="goto", success=False, error=str(e))

    def back(self) -> BrowserResult:
        """Go back in browser history."""
        self._ensure_started()
        try:
            self._page.go_back()
            return BrowserResult(
                action="back", success=True,
                data=f"Navigated back to: {self._page.title()}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="back", success=False, error=str(e))

    def refresh(self) -> BrowserResult:
        """Refresh the current page."""
        self._ensure_started()
        try:
            self._page.reload()
            return BrowserResult(
                action="refresh", success=True,
                data=f"Refreshed: {self._page.title()}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="refresh", success=False, error=str(e))

    def _snapshot(self, max_length: int = 2000) -> str:
        """Return a short snippet of the visible page text for auto-injection after actions."""
        try:
            text = self._page.inner_text("body")
            if len(text) > max_length:
                text = text[:max_length] + f"\n... [truncated, {len(text)} total chars]"
            return text.strip()
        except Exception:
            return ""

    # === Content Extraction ===

    def content(self, max_length: int = 10000) -> BrowserResult:
        """Get the visible text content of the current page."""
        self._ensure_started()
        try:
            text = self._page.inner_text("body")
            if len(text) > max_length:
                text = text[:max_length] + f"\n... [truncated, {len(text)} total chars]"
            return BrowserResult(
                action="content", success=True, data=text, url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="content", success=False, error=str(e))

    def html(self, max_length: int = 20000) -> BrowserResult:
        """Get the full HTML of the current page."""
        self._ensure_started()
        try:
            page_html = self._page.content()
            if len(page_html) > max_length:
                page_html = page_html[:max_length] + f"\n... [truncated, {len(page_html)} total chars]"
            return BrowserResult(
                action="html", success=True, data=page_html, url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="html", success=False, error=str(e))

    def title(self) -> BrowserResult:
        """Get the page title."""
        self._ensure_started()
        try:
            return BrowserResult(
                action="title", success=True,
                data=self._page.title(),
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="title", success=False, error=str(e))

    def url(self) -> BrowserResult:
        """Get the current URL."""
        self._ensure_started()
        return BrowserResult(
            action="url", success=True, data=self._page.url, url=self._page.url,
        )

    # === Interaction ===

    def click(self, selector: str) -> BrowserResult:
        """Click an element by CSS selector."""
        self._ensure_started()
        try:
            self._page.click(selector, timeout=10000)
            self._page.wait_for_load_state("domcontentloaded")
            snapshot = self._snapshot()
            data = f"Clicked: {selector}\nPage: {self._page.title()}"
            if snapshot:
                data += f"\n--- Page Content ---\n{snapshot}"
            return BrowserResult(
                action="click", success=True,
                data=data,
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="click", success=False, error=str(e))

    def fill(self, selector: str, value: str) -> BrowserResult:
        """Fill a form field with a value."""
        self._ensure_started()
        try:
            self._page.fill(selector, value, timeout=10000)
            return BrowserResult(
                action="fill", success=True,
                data=f"Filled '{selector}' with value ({len(value)} chars)",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="fill", success=False, error=str(e))

    def type_text(self, selector: str, text: str, delay: int = 50) -> BrowserResult:
        """Type text into an element character by character (more realistic)."""
        self._ensure_started()
        try:
            self._page.type(selector, text, delay=delay, timeout=10000)
            return BrowserResult(
                action="type", success=True,
                data=f"Typed into '{selector}' ({len(text)} chars)",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="type", success=False, error=str(e))

    def press(self, key: str) -> BrowserResult:
        """Press a keyboard key (e.g., 'Enter', 'Tab', 'Escape')."""
        self._ensure_started()
        try:
            self._page.keyboard.press(key)
            return BrowserResult(
                action="press", success=True,
                data=f"Pressed: {key}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="press", success=False, error=str(e))

    def select(self, selector: str, value: str) -> BrowserResult:
        """Select an option from a dropdown."""
        self._ensure_started()
        try:
            self._page.select_option(selector, value, timeout=10000)
            return BrowserResult(
                action="select", success=True,
                data=f"Selected '{value}' in '{selector}'",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="select", success=False, error=str(e))

    def upload(self, selector: str, file_path: str) -> BrowserResult:
        """Upload a file to a file input."""
        self._ensure_started()
        try:
            self._page.set_input_files(selector, file_path, timeout=10000)
            return BrowserResult(
                action="upload", success=True,
                data=f"Uploaded '{file_path}' to '{selector}'",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="upload", success=False, error=str(e))

    def submit(self, selector: str = "form") -> BrowserResult:
        """Submit a form by clicking a submit button, pressing Enter, or using JS."""
        self._ensure_started()
        try:
            # Try submit elements in order of likelihood (input[type=submit] is
            # most common in PHP apps like DVWA, then button variants)
            submit_btn = self._page.query_selector(
                f"{selector} input[type='submit'], "
                f"{selector} button[type='submit'], "
                f"{selector} button:not([type])"
            )
            if submit_btn:
                submit_btn.click()
            else:
                # Fallback: press Enter on the first input in the form
                first_input = self._page.query_selector(
                    f"{selector} input:not([type='hidden'])"
                )
                if first_input:
                    first_input.press("Enter")
                else:
                    # Last resort: JavaScript submission
                    self._page.evaluate(
                        f"document.querySelector('{selector}').submit()"
                    )
            self._page.wait_for_load_state("domcontentloaded")
            snapshot = self._snapshot()
            data = f"Submitted form. Page: {self._page.title()}"
            if snapshot:
                data += f"\n--- Page Content ---\n{snapshot}"
            return BrowserResult(
                action="submit", success=True,
                data=data,
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="submit", success=False, error=str(e))

    # === Screenshots & Visual ===

    def screenshot(self, full_page: bool = False, name: str = "") -> BrowserResult:
        """Take a screenshot of the current page."""
        self._ensure_started()
        try:
            self._screenshot_counter += 1
            filename = name or f"screenshot_{self._screenshot_counter:04d}.png"
            if not filename.endswith(".png"):
                filename += ".png"
            path = str(self.screenshots_dir / filename)
            self._page.screenshot(path=path, full_page=full_page)
            return BrowserResult(
                action="screenshot", success=True,
                data=f"Screenshot saved ({self._page.title()})",
                screenshot_path=path,
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="screenshot", success=False, error=str(e))

    def screenshot_element(self, selector: str, name: str = "") -> BrowserResult:
        """Screenshot a specific element."""
        self._ensure_started()
        try:
            self._screenshot_counter += 1
            filename = name or f"element_{self._screenshot_counter:04d}.png"
            if not filename.endswith(".png"):
                filename += ".png"
            path = str(self.screenshots_dir / filename)
            element = self._page.query_selector(selector)
            if not element:
                return BrowserResult(
                    action="screenshot_element", success=False,
                    error=f"Element not found: {selector}",
                )
            element.screenshot(path=path)
            return BrowserResult(
                action="screenshot_element", success=True,
                data=f"Element screenshot saved ({selector})",
                screenshot_path=path,
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(
                action="screenshot_element", success=False, error=str(e),
            )

    # === JavaScript Execution ===

    def evaluate(self, js_code: str) -> BrowserResult:
        """Execute JavaScript on the page and return the result."""
        self._ensure_started()
        try:
            result = self._page.evaluate(js_code)
            result_str = json.dumps(result, indent=2, default=str) if result is not None else "undefined"
            return BrowserResult(
                action="evaluate", success=True,
                data=result_str,
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="evaluate", success=False, error=str(e))

    # === Cookies & Storage ===

    def cookies(self, url: Optional[str] = None) -> BrowserResult:
        """Get cookies, optionally filtered by URL."""
        self._ensure_started()
        try:
            if url:
                cookie_list = self._context.cookies(url)
            else:
                cookie_list = self._context.cookies()
            formatted = json.dumps(cookie_list, indent=2)
            return BrowserResult(
                action="cookies", success=True,
                data=f"Cookies ({len(cookie_list)}):\n{formatted}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="cookies", success=False, error=str(e))

    def set_cookie(
        self, name: str, value: str, domain: str, path: str = "/"
    ) -> BrowserResult:
        """Set a cookie."""
        self._ensure_started()
        try:
            self._context.add_cookies(
                [{"name": name, "value": value, "domain": domain, "path": path}]
            )
            return BrowserResult(
                action="set_cookie", success=True,
                data=f"Cookie set: {name}={value} (domain={domain})",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="set_cookie", success=False, error=str(e))

    def local_storage(self) -> BrowserResult:
        """Get all localStorage data."""
        self._ensure_started()
        try:
            data = self._page.evaluate(
                "() => JSON.stringify(Object.entries(localStorage))"
            )
            return BrowserResult(
                action="local_storage", success=True, data=data, url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="local_storage", success=False, error=str(e))

    def session_storage(self) -> BrowserResult:
        """Get all sessionStorage data."""
        self._ensure_started()
        try:
            data = self._page.evaluate(
                "() => JSON.stringify(Object.entries(sessionStorage))"
            )
            return BrowserResult(
                action="session_storage", success=True, data=data, url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(
                action="session_storage", success=False, error=str(e),
            )

    # === Element Inspection ===

    def query(self, selector: str) -> BrowserResult:
        """Query for elements matching a CSS selector and return their text/attributes."""
        self._ensure_started()
        try:
            elements = self._page.query_selector_all(selector)
            results = []
            for i, el in enumerate(elements[:50]):  # Limit to 50 elements
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                text = el.inner_text()[:200] if el.inner_text() else ""
                href = el.get_attribute("href") or ""
                src = el.get_attribute("src") or ""
                el_id = el.get_attribute("id") or ""
                cls = el.get_attribute("class") or ""

                desc = f"[{i}] <{tag}"
                if el_id:
                    desc += f' id="{el_id}"'
                if cls:
                    desc += f' class="{cls[:60]}"'
                if href:
                    desc += f' href="{href}"'
                if src:
                    desc += f' src="{src}"'
                desc += ">"
                if text:
                    desc += f" {text[:100]}"
                results.append(desc)

            data = f"Found {len(elements)} elements matching '{selector}':\n" + "\n".join(results)
            return BrowserResult(
                action="query", success=True, data=data, url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="query", success=False, error=str(e))

    def links(self) -> BrowserResult:
        """Extract all links from the current page."""
        self._ensure_started()
        try:
            link_data = self._page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href,
                    text: a.innerText.trim().substring(0, 100)
                })).filter(l => l.href && !l.href.startsWith('javascript:'))
            """)
            lines = [f"Links found: {len(link_data)}"]
            seen = set()
            for link in link_data:
                href = link["href"]
                if href in seen:
                    continue
                seen.add(href)
                text = link["text"][:80] if link["text"] else ""
                lines.append(f"  {href}" + (f"  ({text})" if text else ""))
            return BrowserResult(
                action="links", success=True,
                data="\n".join(lines),
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="links", success=False, error=str(e))

    def forms(self) -> BrowserResult:
        """Extract all forms and their inputs from the current page."""
        self._ensure_started()
        try:
            form_data = self._page.evaluate("""
                () => Array.from(document.querySelectorAll('form')).map((form, i) => ({
                    index: i,
                    action: form.action,
                    method: form.method || 'GET',
                    inputs: Array.from(form.querySelectorAll('input, textarea, select')).map(inp => ({
                        tag: inp.tagName.toLowerCase(),
                        type: inp.type || '',
                        name: inp.name || '',
                        id: inp.id || '',
                        placeholder: inp.placeholder || '',
                        value: inp.type === 'password' ? '***' : (inp.value || '').substring(0, 50)
                    }))
                }))
            """)
            lines = [f"Forms found: {len(form_data)}"]
            for form in form_data:
                lines.append(
                    f"\n  Form #{form['index']}: {form['method'].upper()} {form['action']}"
                )
                for inp in form["inputs"]:
                    desc = f"    <{inp['tag']}"
                    if inp["type"]:
                        desc += f" type={inp['type']}"
                    if inp["name"]:
                        desc += f" name={inp['name']}"
                    if inp["id"]:
                        desc += f" id={inp['id']}"
                    if inp["placeholder"]:
                        desc += f" placeholder=\"{inp['placeholder']}\""
                    desc += ">"
                    lines.append(desc)
            return BrowserResult(
                action="forms", success=True,
                data="\n".join(lines),
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="forms", success=False, error=str(e))

    # === Network Interception ===

    def intercept_requests(self, url_pattern: str = "**/*") -> BrowserResult:
        """Start intercepting network requests matching a glob/substring pattern.

        ``url_pattern`` supports glob wildcards (* and **) as well as plain
        substring matching.  Every new call resets the captured request list so
        that callers always get a clean slate.
        """
        import fnmatch
        self._ensure_started()
        try:
            self._intercepted_requests = []
            self._intercept_pattern = url_pattern  # stored for display

            def _matches(url: str, pattern: str) -> bool:
                if pattern == "**/*":
                    return True
                # Try glob match first; fall back to substring
                if fnmatch.fnmatch(url, pattern):
                    return True
                return pattern.lstrip("*").rstrip("*") in url

            def handle_request(request):
                if _matches(request.url, url_pattern):
                    self._intercepted_requests.append({
                        "url": request.url,
                        "method": request.method,
                        "headers": dict(request.headers),
                        "post_data": request.post_data[:500] if request.post_data else None,
                    })

            self._page.on("request", handle_request)
            return BrowserResult(
                action="intercept_requests", success=True,
                data=f"Now intercepting requests matching: {url_pattern}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(
                action="intercept_requests", success=False, error=str(e),
            )

    def get_intercepted(self) -> BrowserResult:
        """Get all intercepted requests."""
        self._ensure_started()
        requests = getattr(self, "_intercepted_requests", [])
        formatted = json.dumps(requests[-50:], indent=2)  # Last 50
        return BrowserResult(
            action="get_intercepted", success=True,
            data=f"Intercepted requests ({len(requests)} total, showing last 50):\n{formatted}",
            url=self._page.url,
        )

    # === Waiting ===

    def wait(self, selector: str, timeout: int = 10000) -> BrowserResult:
        """Wait for an element to appear."""
        self._ensure_started()
        try:
            self._page.wait_for_selector(selector, timeout=timeout)
            return BrowserResult(
                action="wait", success=True,
                data=f"Element appeared: {selector}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="wait", success=False, error=str(e))

    def wait_for_navigation(self, timeout: int = 30000) -> BrowserResult:
        """Wait for page navigation to complete."""
        self._ensure_started()
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=timeout)
            return BrowserResult(
                action="wait_for_navigation", success=True,
                data=f"Navigation complete: {self._page.title()}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(
                action="wait_for_navigation", success=False, error=str(e),
            )

    # === Tab Management ===

    def new_tab(self, url: str = "") -> BrowserResult:
        """Open a new tab, optionally navigating to a URL."""
        self._ensure_started()
        try:
            self._page = self._context.new_page()
            if url:
                self._page.goto(url, wait_until="domcontentloaded")
            return BrowserResult(
                action="new_tab", success=True,
                data=f"New tab opened" + (f": {url}" if url else ""),
                url=self._page.url if url else "",
            )
        except Exception as e:
            return BrowserResult(action="new_tab", success=False, error=str(e))

    # === Crawling ===

    def crawl(self, base_url: str, max_pages: int = 20) -> BrowserResult:
        """
        Crawl a website starting from base_url, collecting pages and links.
        Returns a sitemap of discovered URLs.
        """
        self._ensure_started()
        try:
            visited = set()
            to_visit = [base_url]
            sitemap = []
            parsed_base = urlparse(base_url)
            base_domain = parsed_base.netloc

            while to_visit and len(visited) < max_pages:
                current_url = to_visit.pop(0)
                if current_url in visited:
                    continue

                visited.add(current_url)
                try:
                    response = self._page.goto(
                        current_url, wait_until="domcontentloaded", timeout=15000
                    )
                    status = response.status if response else 0
                    title = self._page.title()

                    page_links = self._page.evaluate("""
                        () => Array.from(document.querySelectorAll('a[href]'))
                            .map(a => a.href)
                            .filter(h => h && !h.startsWith('javascript:') && !h.startsWith('mailto:'))
                    """)

                    sitemap.append({
                        "url": current_url,
                        "status": status,
                        "title": title,
                        "links_found": len(page_links),
                    })

                    # Add same-domain links to visit queue
                    for link in page_links:
                        parsed_link = urlparse(link)
                        if parsed_link.netloc == base_domain and link not in visited:
                            # Strip fragments
                            clean_link = link.split("#")[0]
                            if clean_link not in visited and clean_link not in to_visit:
                                to_visit.append(clean_link)

                except Exception:
                    sitemap.append({
                        "url": current_url,
                        "status": "error",
                        "title": "",
                        "links_found": 0,
                    })

            lines = [f"Crawled {len(sitemap)} pages from {base_url}:"]
            for page in sitemap:
                lines.append(
                    f"  [{page['status']}] {page['url']} - {page['title']} ({page['links_found']} links)"
                )
            if to_visit:
                lines.append(f"\n  {len(to_visit)} URLs remaining (hit max_pages={max_pages})")

            return BrowserResult(
                action="crawl", success=True,
                data="\n".join(lines),
                url=base_url,
            )
        except Exception as e:
            return BrowserResult(action="crawl", success=False, error=str(e))

    # === XSS Testing ===

    def test_xss(self, url: str, param: str, payloads: Optional[list[str]] = None) -> BrowserResult:
        """
        Test a URL parameter for XSS by injecting payloads and checking for execution.
        """
        self._ensure_started()
        default_payloads = [
            '<script>window.__xss_fired=true</script>',
            '<img src=x onerror="window.__xss_fired=true">',
            '<svg onload="window.__xss_fired=true">',
            '" onmouseover="window.__xss_fired=true" x="',
            "'-alert(1)-'",
            '<details open ontoggle="window.__xss_fired=true">',
        ]
        test_payloads = payloads or default_payloads
        results = []

        try:
            for i, payload in enumerate(test_payloads):
                # Reset the flag
                test_url = url.replace(f"{param}=", f"{param}={payload}")
                if f"{param}=" not in test_url:
                    sep = "&" if "?" in test_url else "?"
                    test_url = f"{url}{sep}{param}={payload}"

                self._page.goto(test_url, wait_until="domcontentloaded", timeout=10000)

                # Check if XSS fired
                fired = self._page.evaluate("() => window.__xss_fired === true")
                # Also check if payload appears in DOM unencoded
                in_dom = self._page.evaluate(
                    f"() => document.body.innerHTML.includes({json.dumps(payload)})"
                )

                status = "VULNERABLE" if fired else ("REFLECTED" if in_dom else "blocked")
                results.append(f"  Payload {i+1}: {status} - {payload[:60]}")

                if fired:
                    # Reset for next test
                    self._page.evaluate("() => { window.__xss_fired = false; }")

            vulnerable = sum(1 for r in results if "VULNERABLE" in r)
            reflected = sum(1 for r in results if "REFLECTED" in r)

            summary = f"XSS Test Results for {param} on {url}:\n"
            summary += f"  Vulnerable: {vulnerable}, Reflected: {reflected}, Blocked: {len(results) - vulnerable - reflected}\n"
            summary += "\n".join(results)

            return BrowserResult(
                action="test_xss", success=True, data=summary, url=url,
            )
        except Exception as e:
            return BrowserResult(action="test_xss", success=False, error=str(e))

    # === Session Management ===

    def clear_cookies(self) -> BrowserResult:
        """Clear all cookies."""
        self._ensure_started()
        try:
            self._context.clear_cookies()
            return BrowserResult(
                action="clear_cookies", success=True,
                data="All cookies cleared",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(
                action="clear_cookies", success=False, error=str(e),
            )

    def set_headers(self, headers: dict) -> BrowserResult:
        """Set extra HTTP headers for all subsequent requests."""
        self._ensure_started()
        try:
            self._context.set_extra_http_headers(headers)
            return BrowserResult(
                action="set_headers", success=True,
                data=f"Headers set: {json.dumps(headers)}",
                url=self._page.url,
            )
        except Exception as e:
            return BrowserResult(action="set_headers", success=False, error=str(e))

    # === Lifecycle ===

    def create_child_context(self, share_cookies: bool = True) -> "BrowserManager":
        """
        Create an isolated BrowserManager sharing the same browser process
        but with its own context (cookies, localStorage, etc.).

        This enables parallel agent browser use without session collisions.
        The child context can optionally copy cookies from the parent for
        authenticated access.
        """
        self._ensure_started()

        child = BrowserManager.__new__(BrowserManager)
        child.headless = self.headless
        child.work_dir = self.work_dir
        child.screenshots_dir = self.screenshots_dir
        child._playwright = None  # Don't own the playwright instance
        child._browser = self._browser  # Share browser process
        child._screenshot_counter = 0
        child._started = True
        child._owns_browser = False  # Mark as non-owning

        # Create isolated context with same settings
        child._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        child._page = child._context.new_page()
        child._page.set_default_timeout(30000)

        # Copy cookies from parent for authenticated access
        if share_cookies and self._context:
            try:
                cookies = self._context.cookies()
                if cookies:
                    child._context.add_cookies(cookies)
            except Exception:
                pass

        get_logger().info("Created child browser context (isolated)")
        return child

    def close(self):
        """Close the browser and clean up resources."""
        try:
            owns_browser = getattr(self, "_owns_browser", True)
            if owns_browser:
                if self._browser:
                    self._browser.close()
                if self._playwright:
                    self._playwright.stop()
            else:
                # Child context: only close our context, not the shared browser
                if self._context:
                    try:
                        self._context.close()
                    except Exception:
                        pass
        except Exception:
            pass
        self._started = False

    def __del__(self):
        self.close()


# Command dispatcher for agent integration
BROWSER_COMMANDS = {
    "goto": {"args": ["url"], "optional": {"wait_until": "domcontentloaded"}},
    "back": {"args": []},
    "refresh": {"args": []},
    "content": {"args": [], "optional": {"max_length": 10000}},
    "html": {"args": [], "optional": {"max_length": 20000}},
    "title": {"args": []},
    "url": {"args": []},
    "click": {"args": ["selector"]},
    "fill": {"args": ["selector", "value"]},
    "type": {"args": ["selector", "text"]},
    "press": {"args": ["key"]},
    "select": {"args": ["selector", "value"]},
    "upload": {"args": ["selector", "file_path"]},
    "submit": {"args": [], "optional": {"selector": "form"}},
    "screenshot": {"args": [], "optional": {"full_page": False, "name": ""}},
    "screenshot_element": {"args": ["selector"]},
    "evaluate": {"args": ["js_code"]},
    "cookies": {"args": [], "optional": {"url": None}},
    "set_cookie": {"args": ["name", "value", "domain"]},
    "local_storage": {"args": []},
    "session_storage": {"args": []},
    "query": {"args": ["selector"]},
    "links": {"args": []},
    "forms": {"args": []},
    "intercept_requests": {"args": [], "optional": {"url_pattern": "**/*"}},
    "get_intercepted": {"args": []},
    "wait": {"args": ["selector"]},
    "wait_for_navigation": {"args": []},
    "new_tab": {"args": [], "optional": {"url": ""}},
    "crawl": {"args": ["base_url"], "optional": {"max_pages": 20}},
    "test_xss": {"args": ["url", "param"]},
    "clear_cookies": {"args": []},
    "set_headers": {"args": ["headers"]},
}





def browser_command_has_shell_operators(command_str: str) -> bool:
    """Return True if command mixes browser call with shell operators/pipes."""
    cmd = command_str.strip()
    return any(op in cmd for op in (" | ", " || ", " && ", " ; "))


def validate_browser_command(method: str, args: list, kwargs: dict) -> Optional[str]:
    """Validate parsed browser command against declared method signature."""
    spec = BROWSER_COMMANDS.get(method)
    if not spec:
        return f"Unknown browser method: {method}"

    required = spec.get("args", [])
    optional = spec.get("optional", {})

    if len(args) < len(required):
        return (
            f"browser.{method}() requires {len(required)} positional arg(s): "
            f"{', '.join(required)}"
        )

    allowed_kwargs = set(optional.keys())
    unexpected = sorted(k for k in kwargs.keys() if k not in allowed_kwargs)
    if unexpected:
        return (
            f"browser.{method}() got unexpected keyword(s): {', '.join(unexpected)}. "
            f"Allowed kwargs: {', '.join(sorted(allowed_kwargs)) or 'none'}"
        )

    return None

def is_browser_command(command_str: str) -> bool:
    """Check if a command string looks like a browser command (browser.xxx(...))."""
    return bool(re.match(r"browser\.\w+\(", command_str.strip()))


def parse_browser_command(command_str: str) -> Optional[tuple[str, list, dict]]:
    """
    Parse a browser command string into (method_name, args, kwargs).

    Supported formats:
        browser.goto("https://example.com")
        browser.fill("#username", "admin")
        browser.screenshot(full_page=True)
        browser.crawl("https://example.com", max_pages=50)
    """
    command_str = command_str.strip()

    # Match browser.method(args)
    match = re.match(r"browser\.(\w+)\((.*)\)$", command_str, re.DOTALL)
    if not match:
        return None

    method = match.group(1)
    args_str = match.group(2).strip()

    if method not in BROWSER_COMMANDS:
        return None

    # Parse arguments
    args = []
    kwargs = {}

    if not args_str:
        return method, args, kwargs

    # Simple argument parser that handles strings and basic values
    # Split on commas not inside quotes
    parts = []
    current = ""
    in_string = False
    string_char = ""
    depth = 0

    for char in args_str:
        if in_string:
            current += char
            if char == string_char and (not current or current[-2:] != "\\" + string_char):
                in_string = False
        elif char in ('"', "'"):
            in_string = True
            string_char = char
            current += char
        elif char in ("{", "["):
            depth += 1
            current += char
        elif char in ("}", "]"):
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char

    if current.strip():
        parts.append(current.strip())

    for part in parts:
        if "=" in part and not part.startswith(("'", '"', "{", "[")):
            key, value = part.split("=", 1)
            kwargs[key.strip()] = _parse_value(value.strip())
        else:
            args.append(_parse_value(part))

    return method, args, kwargs


def parse_browser_command_with_error(
    command_str: str,
) -> tuple[Optional[tuple[str, list, dict]], Optional[str]]:
    """Like parse_browser_command but returns (result, error_message) instead of None.

    Returns:
        (parsed_tuple, None)   — on success
        (None, error_message)  — on parse failure, with actionable diagnostics
    """
    command_str = command_str.strip()

    # Does it look like a browser call at all?
    if not re.match(r"browser\.\w+\(", command_str):
        return None, (
            f"Not a browser command: '{command_str[:80]}'. "
            "Browser commands must start with browser.<method>(...)."
        )

    # Extract method name
    method_match = re.match(r"browser\.(\w+)\(", command_str)
    if not method_match:
        return None, f"Could not extract method name from: '{command_str[:80]}'"

    method = method_match.group(1)
    if method not in BROWSER_COMMANDS:
        valid = ", ".join(sorted(BROWSER_COMMANDS.keys()))
        return None, (
            f"Unknown browser method '{method}'. "
            f"Valid methods: {valid}"
        )

    # Does it end with a closing paren?
    if not re.match(r"browser\.(\w+)\(.*\)$", command_str, re.DOTALL):
        return None, (
            f"Malformed browser command — missing closing ')': '{command_str[:80]}'. "
            "Ensure the command is on a single line and has balanced parentheses."
        )

    parsed = parse_browser_command(command_str)
    if parsed is None:
        return None, (
            f"Failed to parse browser command arguments: '{command_str[:80]}'. "
            "Check that string arguments are quoted and the call is well-formed."
        )

    return parsed, None


def _parse_value(value_str: str):
    """Parse a string value into the appropriate Python type."""
    value_str = value_str.strip()

    # String literals
    if (value_str.startswith('"') and value_str.endswith('"')) or (
        value_str.startswith("'") and value_str.endswith("'")
    ):
        return value_str[1:-1]

    # Boolean
    if value_str.lower() == "true":
        return True
    if value_str.lower() == "false":
        return False

    # None
    if value_str.lower() in ("none", "null"):
        return None

    # Numbers
    try:
        if "." in value_str:
            return float(value_str)
        return int(value_str)
    except ValueError:
        pass

    # JSON objects/arrays
    try:
        return json.loads(value_str)
    except (json.JSONDecodeError, ValueError):
        pass

    # Return as string
    return value_str
