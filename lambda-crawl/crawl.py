"""Build a compact feed worklist; workers check DynamoDB themselves."""
import os
from pathlib import Path


def handler(event, context):
    options = event.get('msg', event)
    days = int(options.get('days', 1))
    if days < 1:
        raise ValueError('days must be a positive integer')
    # Backfills are quiet unless explicitly requested otherwise.
    email = options.get('email', 'n' if days > 1 else os.environ['sendemails'])
    if email not in ('y', 'yes', 'n', 'no', True, False):
        raise ValueError('email must be y/n, yes/no, or a boolean')
    feeds = []
    for line in Path(__file__).with_name('feeds.txt').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        source, url = line.split(',', 1)
        feeds.append({'url': url.strip(), 'blogsource': source.strip(), 'daystoretrieve': days})
    return {'results': feeds, 'daystoretrieve': days,
            'sendemail': 'y' if email in ('y', 'yes', True) else 'n'}
