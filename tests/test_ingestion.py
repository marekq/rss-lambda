import importlib.util
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lambda-getfeed'))
os.environ.update(dynamo_region='eu-west-1', dynamo_table='test',
                  AWS_DEFAULT_REGION='eu-west-1', sendemails='y',
                  fromemail='sender@example.com', toemail='recipient@example.com')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch('boto3.client'), patch('boto3.resource'):
        spec.loader.exec_module(module)
    return module


crawl = load('crawl', 'lambda-crawl/crawl.py')
feed = load('getfeed', 'lambda-getfeed/getfeed.py')
alert = load('alert', 'lambda-alert/alert.py')


class IngestionTests(unittest.TestCase):
    def test_backfill_is_compact_and_quiet(self):
        result = crawl.handler({'msg': {'days': 365}}, None)
        self.assertEqual(result['sendemail'], 'n')
        self.assertNotIn('guids', result)
        self.assertEqual(result['results'][0]['daystoretrieve'], 365)

    def test_explicit_notification_options(self):
        for value, expected in [('n', 'n'), ('yes', 'y'), (False, 'n')]:
            self.assertEqual(crawl.handler({'msg': {'days': 365, 'email': value}}, None)['sendemail'], expected)
        self.assertEqual(crawl.handler({'msg': {}}, None)['sendemail'], 'y')

    def test_invalid_window(self):
        with self.assertRaises(ValueError):
            crawl.handler({'msg': {'days': 0}}, None)

    def test_existing_articles_paginates(self):
        with patch.object(feed, 'ddb') as table:
            table.query.side_effect = [
                {'Items': [{'guid': 'a', 'link': 'https://a'}], 'LastEvaluatedKey': {'guid': 'a'}},
                {'Items': [{'guid': 'b'}]}]
            self.assertEqual(feed.existing_articles('source'), ({'a', 'b'}, {'https://a'}))
            self.assertIn('ExclusiveStartKey', table.query.call_args.kwargs)

    def run_feed(self, entries, guids=(), links=(), email='y', primary_count=0):
        feed.days_to_retrieve = 365
        feed.send_mail = email
        with patch.object(feed, 'existing_articles', return_value=(set(guids), set(links))), \
             patch.object(feed, 'get_rss', return_value={'entries': entries}), \
             patch.object(feed, 'ddb') as table, \
             patch.object(feed, 'put_dynamo', return_value=True) as put, \
             patch.object(feed, 'send_email') as send, \
             patch.object(feed, 'refresh_counter'):
            table.query.return_value = {'Count': primary_count}
            result = feed.get_feed('https://feed', 'source')
            return result, put.call_count, send.call_count

    def test_existing_guid_or_link_never_emails(self):
        result, writes, emails = self.run_feed([
            {'guid': 'known', 'link': 'https://one'},
            {'guid': 'changed', 'link': 'https://known'}], ['known'], ['https://known'])
        self.assertEqual((writes, emails), (0, 0))
        self.assertEqual(result[1]['duplicates'], 2)

    def test_primary_lookup_covers_index_delay(self):
        _, writes, emails = self.run_feed([{'guid': 'known', 'link': 'https://one'}], primary_count=1)
        self.assertEqual((writes, emails), (0, 0))

    def test_new_article_inserted_once_and_quiet(self):
        entry = {'guid': 'new', 'link': 'https://new', 'title': 'New', 'published_parsed': time.gmtime()}
        result, writes, emails = self.run_feed([entry, entry], email='n')
        self.assertEqual((writes, emails), (1, 0))
        self.assertEqual(result[1]['inserted'], 1)
        self.assertEqual(result[1]['duplicates'], 1)

    def test_combined_refresh_preserves_window(self):
        with patch.object(feed, 'refresh_counter'), patch.object(feed, 'update_json_s3') as update:
            feed.handler({'msg': 'all', 'daystoretrieve': 365}, None)
            self.assertEqual(feed.days_to_retrieve, 365)
            update.assert_called_once_with('all')

    def test_alert_uses_configured_sender_without_real_mail(self):
        with patch.object(alert, 'ses') as ses:
            ses.send_email.return_value = {'MessageId': 'mock-only'}
            alert.handler({'detail': {'status': 'FAILED', 'name': 'test'}}, None)
            self.assertEqual(ses.send_email.call_args.kwargs['Source'], os.environ['fromemail'])


if __name__ == '__main__':
    unittest.main()
