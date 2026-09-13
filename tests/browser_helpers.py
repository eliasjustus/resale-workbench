"""Synthetic browser and clock builders shared by discovery/CLI tests."""
from datetime import datetime


STAMP = '2026-09-13T12:00:00+00:00'


HTML = '''<article data-adid="901"><h3>Synthetic recent listing</h3>
<svg data-title="calendarOutline"></svg><span>13.09.2026, 13:30</span></article>
<article data-adid="902"><h3>Synthetic older listing</h3>
<svg data-title="calendarOutline"></svg><span>13.09.2026, 05:00</span></article>'''


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat('2026-09-13T12:00:30+00:00')


class Browser:
    def __init__(self, trace, redirect=None):
        self.trace, self.redirect = trace, redirect
        self.chromium = self.first = self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def launch(self, **kwargs): return self
    def new_context(self, **kwargs): return self
    def new_page(self): return self
    def goto(self, url, **kwargs):
        self.trace.append(url)
        self.url = self.redirect or url
        return type('Response', (), {'status': 200})()
    def locator(self, *args): return self
    def wait_for(self, **kwargs): pass
    def content(self): return HTML
    def close(self): pass

