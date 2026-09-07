from provider_feed import add_provider, preview_text
#!/usr/bin/python
# @marekq
# www.marek.rocks

import botocore, boto3, feedparser
import hashlib
import json, os, re
import calendar
import time

from boto3.dynamodb.types import TypeSerializer
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Establish service clients once per Lambda execution environment.
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
# Resource clients install automatic serialization hooks. Transactions below
# already contain AttributeValues, so use a separate low-level client.
ddb_transactions = boto3.client('dynamodb', region_name=os.environ['dynamo_region'])
ses = boto3.client('ses')
s3 = boto3.client('s3')
serializer = TypeSerializer()

# These attributes are the table's primary and secondary-index keys. Keep the
# expected wire types next to the serialization boundary so a feed payload can
# never accidentally turn a key into a DynamoDB map/list/etc.
DYNAMODB_KEY_TYPES = {
	'guid': 'S',
	'timest': 'N',
	'blogsource': 'S',
	'visible': 'S',
	'provider': 'S',
}
DYNAMODB_ITEM_SIZE_WARNING = 350 * 1024


# get the RSS feed through feedparser
def get_rss(url):
	return feedparser.parse(url)


def _item_size_bytes(serialized_item):
	"""Return a conservative, loggable size estimate for a DynamoDB item."""
	return len(json.dumps(serialized_item, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))


def _guid_label(guid):
	guid = str(guid)
	if len(guid) <= 120:
		return guid
	return guid[:72] + '...' + guid[-32:]


def _text(value, fallback=''):
	return fallback if value is None else str(value)


def _item_diagnostics(serialized_item, guid, blogsource, timest_post):
	field_sizes = sorted(
		(
			(key, len(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')))
			for key, value in serialized_item.items()
		),
		key=lambda field: field[1],
		reverse=True,
	)
	return {
		'blogsource': blogsource,
		'guid': _guid_label(guid),
		'guid_sha256': hashlib.sha256(str(guid).encode('utf-8')).hexdigest()[:16],
		'timest': timest_post,
		'item_wire_json_bytes': _item_size_bytes(serialized_item),
		'key_types': {
			key: next(iter(serialized_item[key]), None)
			for key in DYNAMODB_KEY_TYPES
			if key in serialized_item
		},
		'largest_fields': field_sizes[:3],
	}


def _log_item_issue(stage, guid, blogsource, timest_post, error=None, serialized_item=None):
	diagnostic = {
		'event': 'dynamodb_item_issue',
		'stage': stage,
		'blogsource': blogsource,
		'guid': _guid_label(guid),
		'guid_sha256': hashlib.sha256(str(guid).encode('utf-8')).hexdigest()[:16],
		'timest': timest_post,
	}
	if serialized_item is not None:
		diagnostic.update(_item_diagnostics(serialized_item, guid, blogsource, timest_post))
	if error is not None:
		diagnostic.update({'error_type': type(error).__name__, 'error': str(error)})
	print('dynamodb_item_issue ' + json.dumps(diagnostic, sort_keys=True, ensure_ascii=True))


# write the blogpost record atomically and idempotently
def put_dynamo(timest_post, title, description, link, blogsource, author, guid, tags, category, datestr_post):
	# Feedparser fields are not guaranteed to be plain scalars. Normalize every
	# value before constructing the item so serialization cannot turn metadata
	# into an unexpected DynamoDB map/list type.
	guid = _text(guid)
	blogsource = _text(blogsource)
	try:
		timest_post = int(timest_post)
	except Exception as error:
		_log_item_issue('normalize', guid, blogsource, timest_post, error=error)
		raise
	title = _text(title)
	description = _text(description, '...')
	link = _text(link)
	author = _text(author)
	tags = _text(tags)
	category = _text(category)
	datestr_post = _text(datestr_post)
	if not guid:
		error = ValueError('article guid must not be empty')
		_log_item_issue('normalize', guid, blogsource, timest_post, error=error)
		raise error
	if not blogsource:
		error = ValueError('article blogsource must not be empty')
		_log_item_issue('normalize', guid, blogsource, timest_post, error=error)
		raise error

	fullitem = {
		'objectID' : guid,
		'timest' : timest_post,
		'title' : title,
		'description' : description,
		'link' : link,
		'blogsource' : blogsource,
		'author' : author,
		'guid' : guid,
		'category' : category,
		'datestr' : datestr_post,
		'lower-tag' : tags.lower(),
		'tag' : tags,
		'visible' : 'y'
	}

	add_provider(fullitem)
	# Persist only the bounded preview. The frontend retrieves the full article
	# from the live URL, and an RSS description can otherwise exceed 400 KB.
	fullitem['description'] = fullitem['preview']
	if not tags:
		fullitem.pop('lower-tag', None)
		fullitem.pop('tag', None)

	try:
		serialized_item = {key: serializer.serialize(value) for key, value in fullitem.items()}
	except Exception as error:
		_log_item_issue('serialize', guid, blogsource, timest_post, error=error)
		raise

	# Set the two table-key values explicitly as AttributeValue objects. This is
	# intentionally redundant with TypeSerializer: it makes the contract clear
	# and prevents a future change to input normalization from sending M/L/etc.
	serialized_item['guid'] = {'S': guid}
	serialized_item['timest'] = {'N': str(timest_post)}

	key_type_errors = []
	for key, expected_type in DYNAMODB_KEY_TYPES.items():
		if key not in serialized_item:
			if key != 'provider':
				key_type_errors.append(f'{key}: missing')
			continue
		actual = serialized_item[key]
		if set(actual) != {expected_type}:
			key_type_errors.append(
				f'{key}: expected {expected_type}, got {json.dumps(actual, sort_keys=True)}'
			)
		elif expected_type == 'S' and not actual[expected_type]:
			key_type_errors.append(f'{key}: must not be empty')
	if key_type_errors:
		error = ValueError('invalid DynamoDB key types: ' + '; '.join(key_type_errors))
		_log_item_issue('key_validation', guid, blogsource, timest_post, error=error, serialized_item=serialized_item)
		raise error

	item_size = _item_size_bytes(serialized_item)
	if item_size >= DYNAMODB_ITEM_SIZE_WARNING:
		_log_item_issue('size_check', guid, blogsource, timest_post, serialized_item=serialized_item)
	# Wire JSON is larger than stored data (especially escaped Unicode).
	# Use it for diagnostics only; DynamoDB enforces the actual 400 KiB limit.

	try:
		ddb_transactions.transact_write_items(
			TransactItems=[
				{
					'Put': {
						'TableName': os.environ['dynamo_table'],
						'Item': serialized_item,
						'ConditionExpression': 'attribute_not_exists(#guid) AND attribute_not_exists(#timest)',
						'ExpressionAttributeNames': {'#guid': 'guid', '#timest': 'timest'}
					}
				}
			]
		)
		print('inserted ' + guid)
		return True

	except ClientError as error:
		if error.response['Error']['Code'] == 'TransactionCanceledException':
			reasons = error.response.get('CancellationReasons', [])
			if reasons and reasons[0].get('Code') == 'ConditionalCheckFailed':
				print('skipping duplicate article ' + guid)
				return False
			diagnostic = _item_diagnostics(serialized_item, guid, blogsource, timest_post)
			diagnostic.update({
				'event': 'dynamodb_transaction_failed',
				'error_code': error.response['Error'].get('Code'),
				'aws_request_id': error.response.get('ResponseMetadata', {}).get('RequestId'),
				'error_message': error.response['Error'].get('Message'),
				'cancellation_codes': [reason.get('Code') for reason in reasons],
				'cancellation_messages': [reason.get('Message') for reason in reasons if reason.get('Message')],
			})
			print('dynamodb_transaction_failed ' + json.dumps(diagnostic, sort_keys=True, ensure_ascii=True))
		raise


# refresh a derived article counter from the current table contents
def refresh_counter(blogsource):
	count = 0

	if blogsource == 'all':
		blogs = ddb.query(
			IndexName = 'visible',
			Select = 'COUNT',
			KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(1)
		)
	else:
		blogs = ddb.query(
			IndexName = 'timest',
			Select = 'COUNT',
			KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(1)
		)

	count += int(blogs['Count'])

	while 'LastEvaluatedKey' in blogs:
		if blogsource == 'all':
			blogs = ddb.query(
				ExclusiveStartKey = blogs['LastEvaluatedKey'],
				IndexName = 'visible',
				Select = 'COUNT',
				KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(1)
			)
		else:
			blogs = ddb.query(
				ExclusiveStartKey = blogs['LastEvaluatedKey'],
				IndexName = 'timest',
				Select = 'COUNT',
				KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(1)
			)
		count += int(blogs['Count'])

	ddb.put_item(
		Item = {
			'timest' : 0,
			'guid' : blogsource,
			'blogsource' : blogsource,
			'articlecount' : count,
			'visible' : 'y'
		}
	)

	print('refreshed ' + blogsource + ' article count to ' + str(count))


# send an email out whenever a new blogpost was found - this feature is optional
def send_email(recpt, title, blogsource, author, description, link, datestr_post):
	# RSS preview only: extracted page HTML can contain megabytes of embedded
	# assets. Bound every component and send UTF-8 text, never page markup.
	mailmsg = (
		str(title)[:300] + '\n\nPosted by ' + str(author)[:200]
		+ ' in ' + str(blogsource)[:100] + ' on ' + str(datestr_post)[:100]
		+ '\n\n' + preview_text(description) + '\n\nRead article: ' + str(link)[:4096]
	)

	# send the email using SES
	r = ses.send_email(
		Source = os.environ['fromemail'],
		Destination = {'ToAddresses': [recpt]},
		Message = {
			'Subject': {
				'Data': (blogsource.upper() + ' - ' + title)[:150],
				'Charset': 'UTF-8'
			},
			'Body': {
				'Text': {
					'Data': mailmsg,
					'Charset': 'UTF-8'
				}
			}
		}
	)
	
	print('sent email with subject ' + blogsource.upper() + ' - ' + title + ' to ' + recpt)


# main function to kick off collection of an rss feed
def existing_articles(blogsource):
	guids, links = set(), set()
	args = {'IndexName': 'timest', 'ProjectionExpression': 'guid, link',
		'KeyConditionExpression': Key('blogsource').eq(blogsource) & Key('timest').gt(0)}
	while True:
		page = ddb.query(**args)
		for item in page.get('Items', []):
			guids.add(item['guid'])
			if item.get('link'):
				links.add(item['link'])
		if 'LastEvaluatedKey' not in page:
			return guids, links
		args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def get_feed(url, blogsource):
	guids, links = existing_articles(blogsource)
	stats = {'found': 0, 'inserted': 0, 'duplicates': 0, 'outside_window': 0, 'emails_sent': 0, 'email_failures': 0}


	# create a variable about blog update and list to store new blogs
	blogupdate = False
	# Return compact counts rather than article IDs.

	# get the rss feed
	rssfeed = get_rss(url)

	stats['found'] = len(rssfeed['entries'])

	# check all the retrieved articles for published dates
	for x in rssfeed['entries']:

		# retrieve post guid
		link = str(x.get('link', ''))
		guid = str(x.get('guid') or x.get('id') or link)
		if not guid:
			raise ValueError('RSS entry has neither GUID nor link')
		if guid in guids or (link and link in links):
			stats['duplicates'] += 1
			continue
		# Primary-key lookup covers secondary-index propagation delay.
		if ddb.query(KeyConditionExpression=Key('guid').eq(guid),
			ConsistentRead=True, Select='COUNT', Limit=1)['Count']:
			stats['duplicates'] += 1
			continue
		published = x.get('published_parsed') or x.get('updated_parsed')
		if not published:
			raise ValueError('RSS entry has no publication date: ' + _guid_label(guid))
		timest_post = calendar.timegm(published)
		timest_now = int(time.time())

		datestr_post = time.strftime('%d-%m-%Y %H:%M', published)

		if timest_now < (timest_post + (86400 * days_to_retrieve)):

			link = str(x['link'])
			title = str(x['title']).replace('"', "'")
			author = str(x.get('author', 'blank'))
			
			print('retrieving '+str(title)+' in '+str(blogsource)+' using url '+str(link)+'\n')
			tags = ''

			description = re.sub(r'<[^>]+>', '', str(x.get('description', x.get('summary', '')))).strip('&nbsp;').replace('"', "'").strip('\n')

			category = 'none'
			if 'tags' in x:
				category = ', '.join(str(tag['term']) for tag in x['tags'])

			inserted = put_dynamo(timest_post, title, description, link, blogsource, author, guid, tags, category, datestr_post)
			if inserted:
				blogupdate = True
				stats['inserted'] += 1
				guids.add(guid)
				if link:
					links.add(link)

				if send_mail == 'y':
					try:
						send_email(os.environ['toemail'], title, blogsource, author, description, link, datestr_post)
						stats['emails_sent'] += 1
					except ClientError as error:
						stats['email_failures'] += 1
						print(json.dumps({'event': 'email_delivery_failed', 'blogsource': blogsource,
							'guid': _guid_label(guid), 'error_code': error.response['Error'].get('Code'),
							'aws_request_id': error.response.get('ResponseMetadata', {}).get('RequestId')}))

			else:
				stats['duplicates'] += 1
		else:
			stats['outside_window'] += 1

	# Counters are derived data. Refreshing them after the feed finishes avoids
	# allowing a malformed legacy counter to abort an otherwise valid article write.
	try:
		refresh_counter(blogsource)
	except Exception as error:
		print('could not refresh ' + blogsource + ' article count: ' + str(error))

	return blogupdate, stats


# get the contents of the dynamodb table for json object on S3
def get_table_json(blogsource):
	s3guids = set()
	res = []

	s3list = s3.list_objects_v2(Bucket = os.environ['s3bucket'])
	s3files = [x['Key'] for x in s3list.get('Contents', [])]

	if blogsource + '.json' in s3files:
		s3obj = s3.get_object(Bucket = os.environ['s3bucket'], Key = blogsource + '.json')
		res = json.loads(s3obj['Body'].read())
		s3guids = {item['guid'] for item in res}
	else:
		print('could not find ' + blogsource + '.json file on s3')

	# A missing export needs the complete table history, not just today's posts.
	diff_ts = int(time.time()) - 86400 * int(days_to_retrieve) if res else 0

	projection = 'blogsource, datestr, timest, title, author, description, link, guid'

	if blogsource != 'all':
		blogs = ddb.query(IndexName = "timest", ScanIndexForward = True, ProjectionExpression = projection,
			KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(diff_ts))
	else:
		blogs = ddb.query(IndexName = "visible", ScanIndexForward = True, ProjectionExpression = projection,
			KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(diff_ts))

	while True:
		for a in blogs['Items']:
			if a['guid'] not in s3guids:
				s3guids.add(a['guid'])
				res.append({'timest': str(a['timest']), 'blogsource': a['blogsource'], 'title': a['title'],
					'datestr': a['datestr'], 'guid': a['guid'], 'author': a['author'], 'link': a['link'],
					'description': a['description'].strip()})

		if 'LastEvaluatedKey' not in blogs:
			break

		if blogsource != 'all':
			blogs = ddb.query(IndexName = "timest", ScanIndexForward = True, ExclusiveStartKey = blogs['LastEvaluatedKey'],
				ProjectionExpression = projection, KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(diff_ts))
		else:
			blogs = ddb.query(IndexName = "visible", ScanIndexForward = True, ExclusiveStartKey = blogs['LastEvaluatedKey'],
				ProjectionExpression = projection, KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(diff_ts))

	return res


# copy the file to s3 with a public acl
def cp_s3(blogsource):

	# put object to s3
	s3.put_object(
		Bucket = os.environ['s3bucket'],
		Body = open('/tmp/' + blogsource + '.json', 'rb'), 
		Key = blogsource + '.json', 
		ACL = 'public-read',
		CacheControl = 'public',
		ContentType = 'application/json'
	)


# update json objects on S3 for single page web apps
def update_json_s3(blog):

	print('updating json for ' + blog)

	# get the json content from DynamoDB
	out = get_table_json(blog)

	# create the json and return path
	make_json(out, blog)

	# upload the json to s3
	cp_s3(blog)


# create a json file from blog content
def make_json(content, blogsource):
	fpath = '/tmp/' + blogsource + '.json'
	filteredcontent = [blog for blog in content if blog['blogsource'] == blogsource or blogsource == 'all']
	dumpfile = sorted(filteredcontent, key = lambda k: k['timest'], reverse = True)

	with open(fpath, "w") as outfile:
		json.dump(dumpfile, outfile)

	print('wrote to ' + fpath)


# lambda handler
def handler(event, context):
	global days_to_retrieve, send_mail
	days_to_retrieve = 1
	send_mail = ''

	if event['msg'] == 'all':
		blogsource = 'all'
		blogupdate = True
		days_to_retrieve = int(event.get('daystoretrieve', 1))
		newblogs = {'refreshed': 'all.json'}
		try:
			refresh_counter('all')
		except Exception as error:
			print('could not refresh all article count: ' + str(error))
	else:
		url = event['msg']['url']
		blogsource = event['msg']['blogsource']
		days_to_retrieve = int(event['msg']['daystoretrieve'])
		send_mail = event['sendemail']
		blogupdate, newblogs = get_feed(url, blogsource)

	# Rebuild exports even on an all-duplicate retry: an earlier invocation may
	# have written articles successfully and failed before publishing its JSON.
	update_json_s3(blogsource)

	return newblogs
