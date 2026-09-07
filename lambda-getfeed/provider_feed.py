"""Compact plain-text fallback previews and provider membership for feed indexes."""
from html.parser import HTMLParser
import json
from pathlib import Path

GROUPS = json.loads(Path(__file__).with_name('providers.json').read_text())
SOURCE_PROVIDER = {source: provider for provider, sources in GROUPS.items() for source in sources}
PREVIEW_LENGTH = 600


class PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3'):
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def preview_text(description):
    parser = PreviewParser()
    parser.feed(description or '')
    parser.close()
    text = ' '.join(''.join(parser.parts).split())
    return text if len(text) <= PREVIEW_LENGTH else text[:PREVIEW_LENGTH - 1].rstrip() + '…'


def add_provider(item):
    if item.get('timest', 0) <= 0:
        return item
    item['preview'] = preview_text(item.get('description'))
    provider = SOURCE_PROVIDER.get(item.get('blogsource'))
    if provider:
        item['provider'] = provider
    else:
        item.pop('provider', None)
    return item
