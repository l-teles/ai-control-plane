"""Take screenshots for README using Playwright."""

import subprocess
import sys
import time
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Anonymization JS — replaces personal paths, UUIDs, secrets, URLs
ANONYMIZE_JS = r"""
() => {
    const walk = (node) => {
        if (node.nodeType === 3) {
            let t = node.textContent;
            // Replace home directory paths (macOS/Linux)
            t = t.replace(/\/Users\/[^\/\s]+/g, '/Users/demo');
            // Replace Windows-style paths
            t = t.replace(/%LOCALAPPDATA%[^\\\s]+/g, '%LOCALAPPDATA%\\demo');
            // Replace UUIDs with fake ones
            t = t.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
                (m, offset) => {
                    const i = offset % 5;
                    return ['a1b2c3d4-e5f6-7890-abcd-ef1234567890',
                            'b2c3d4e5-f6a1-8901-bcde-f12345678901',
                            'c3d4e5f6-a1b2-9012-cdef-123456789012',
                            'd4e5f6a1-b2c3-0123-defa-234567890123',
                            'e5f6a1b2-c3d4-1234-efab-345678901234'][i];
                });
            // Replace absolute file paths
            t = t.replace(/\/Users\/[^\/\s]+\/[^\s<]+/g, '/Users/demo/project');
            // Mask anything that looks like a token/key (40+ chars of alphanumeric)
            t = t.replace(/[A-Za-z0-9+\/=_-]{40,}/g, '****');
            // Mask URL-embedded credentials (scheme://user:pass@host)
            t = t.replace(/(:\/\/[^:]+:)[^@]+(@)/g, '$1****$2');
            // Mask values already partially masked (e.g. "abcd****")
            // Replace any "word****" patterns to just "****"
            t = t.replace(/\S{4}\*{4,}/g, '********');
            node.textContent = t;
        }
        // Also anonymize href/title attributes on links
        if (node.nodeType === 1 && node.tagName === 'A') {
            const href = node.getAttribute('href');
            if (href && href.includes('/Users/')) {
                node.setAttribute('href', '#');
            }
        }
        for (const child of node.childNodes) walk(child);
    };
    walk(document.body);
}
"""


def main():
    from playwright.sync_api import sync_playwright

    # Start the Flask app
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "ai_control_plane", "--port", "5099"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # Wait for server startup

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            for theme in ("dark", "light"):
                # Dashboard
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:5099/")
                page.evaluate(f"localStorage.setItem('theme', '{theme}')")
                page.reload()
                page.wait_for_load_state("networkidle")
                page.evaluate(ANONYMIZE_JS)
                page.screenshot(path=str(ASSETS / f"screenshot_{theme}_home.png"))
                page.close()

                # Session detail (pick first session)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:5099/sessions")
                page.evaluate(f"localStorage.setItem('theme', '{theme}')")
                page.reload()
                page.wait_for_load_state("networkidle")
                first_link = page.query_selector("a.session-card, a.recent-item, .session-list a")
                if first_link:
                    href = first_link.get_attribute("href")
                    page.goto(f"http://127.0.0.1:5099{href}")
                    page.wait_for_load_state("networkidle")
                page.evaluate(ANONYMIZE_JS)
                page.screenshot(path=str(ASSETS / f"screenshot_{theme}_session.png"))
                page.close()

                # Tools detail
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:5099/tools/claude")
                page.evaluate(f"localStorage.setItem('theme', '{theme}')")
                page.reload()
                page.wait_for_load_state("networkidle")
                page.evaluate(ANONYMIZE_JS)
                page.screenshot(path=str(ASSETS / f"screenshot_{theme}_tools.png"))
                page.close()

                # Agents
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:5099/agents")
                page.evaluate(f"localStorage.setItem('theme', '{theme}')")
                page.reload()
                page.wait_for_load_state("networkidle")
                page.evaluate(ANONYMIZE_JS)
                page.screenshot(path=str(ASSETS / f"screenshot_{theme}_agents.png"))
                page.close()

                # Skills
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:5099/skills")
                page.evaluate(f"localStorage.setItem('theme', '{theme}')")
                page.reload()
                page.wait_for_load_state("networkidle")
                page.evaluate(ANONYMIZE_JS)
                page.screenshot(path=str(ASSETS / f"screenshot_{theme}_skills.png"))
                page.close()

            browser.close()
            print("Screenshots saved to assets/")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    main()
