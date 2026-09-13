"""Keep offline parsing usable without the optional browser dependency."""

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:
    class PlaywrightError(Exception):
        """Placeholder used only when browser collection is unavailable."""

    def sync_playwright():
        raise SystemExit(
            'Browser collection needs the optional dependency. '
            'From the downloaded Resale Workbench source directory, run '
            'python -m pip install ".[browser]"; '
            'then python -m playwright install chromium. '
            'Use only sources you are authorized to collect.'
        )
