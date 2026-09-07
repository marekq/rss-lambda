from provider_feed import add_provider
#!/usr/bin/python
# @marekq
# www.marek.rocks

import botocore, boto3, feedparser
import hashlib
import json, os, re, readability, requests
import sys, time

from boto3.dynamodb.types import TypeSerializer
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
com = boto3.client(service_name = 'comprehend', region_name = os.environ['AWS_REGION'])
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
DYNAMODB_ITEM_SIZE_LIMIT = 400 * 1024
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
	if item_size > DYNAMODB_ITEM_SIZE_LIMIT:
		error = ValueError(f'DynamoDB item exceeds the {DYNAMODB_ITEM_SIZE_LIMIT}-byte limit')
		_log_item_issue('size_validation', guid, blogsource, timest_post, error=error, serialized_item=serialized_item)
		raise error

	try:
		ddb.meta.client.transact_write_items(
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
			diagnostic = _item_diagnostics(serialized_item, guid, blogsource, timest_post)
			diagnostic.update({
				'event': 'dynamodb_transaction_failed',
				'error_code': error.response['Error'].get('Code'),
				'error_message': error.response['Error'].get('Message'),
				'cancellation_codes': [reason.get('Code') for reason in reasons],
				'cancellation_messages': [reason.get('Message') for reason in reasons if reason.get('Message')],
			})
			print('dynamodb_transaction_failed ' + json.dumps(diagnostic, sort_keys=True, ensure_ascii=True))
			if reasons and reasons[0].get('Code') == 'ConditionalCheckFailed':
				print('skipping duplicate article ' + guid)
				return False
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


# retrieve the url of a blogpost
def retrieve_url(url):

	# set a "real" user agent
	firefox = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:79.0) Gecko/20100101 Firefox/79.0"

	try:
		# retrieve the main text section from the url using the readability module and using the Chrome user agent
		req = requests.get(url, headers = {'User-Agent' : firefox})
		doc = readability.Document(req.text)
		rawhtml = doc.summary(html_partial = True)

		# remove any html tags from output
		soup = BeautifulSoup(rawhtml, 'html.parser')
		cleantext = soup.get_text().strip('\n').encode('utf-8')

		return str(rawhtml), str(cleantext)

	except requests.exceptions.ConnectionError as e:
		# log the connection error and return empty strings to prevent breaking the entire lambda
		print(f'ConnectionError while retrieving URL {url}: {str(e)}')
		return '', ''

	except Exception as e:
		# catch any other exceptions that might occur during URL retrieval
		print(f'Error while retrieving URL {url}: {str(e)}')
		return '', ''


# analyze the text of a blogpost using the AWS Comprehend service
def comprehend(cleantxt, title):
	detections = []
	found = False

	fulltext = title + " " + cleantxt

	# cut down the text to less than 5000 bytes as this is the file limit for Comprehend
	strlen = sys.getsizeof(fulltext)

	while strlen > 5000:
		fulltext = fulltext[:-1]
		strlen = sys.getsizeof(fulltext)

	# check whether organization or title labels were found by Comprehend
	for x in com.detect_entities(Text = fulltext, LanguageCode = 'en')['Entities']:
		if x['Type'] == 'ORGANIZATION' or x['Type'] == 'TITLE' or x['Type'] == 'COMMERCIAL_ITEM' or x['Type'] == 'PERSON':
			if x['Text'] not in detections:
				detections.append(x['Text'])
				found = True

	# if no tags were retrieved, add a default tag
	if found:
		tags = ', '.join(detections)
		
	else:
		tags = 'none'

	# return tag values	
	return(tags)


# send an email out whenever a new blogpost was found - this feature is optional
def send_email(recpt, title, blogsource, author, rawhtml, link, datestr_post):

	# create a simple html body for the email
	mailmsg = '<html><body><br><i>Posted by '+str(author)+' in ' +str(blogsource) + ' blog on ' + str(datestr_post) + '</i><br><br>'
	mailmsg += '<a href="' + link + '">view post here</a><br><br>' + str(rawhtml) + '<br></body></html>'

	# send the email using SES
	r = ses.send_email(
		Source = os.environ['fromemail'],
		Destination = {'ToAddresses': [recpt]},
		Message = {
			'Subject': {
				'Data': blogsource.upper() + ' - ' + title
			},
			'Body': {
				'Html': {
					'Data': mailmsg
				}
			}
		}
	)
	
	print('sent email with subject ' + blogsource.upper() + ' - ' + title + ' to ' + recpt)


# main function to kick off collection of an rss feed
def get_feed(url, blogsource, guids):

	# create a variable about blog update and list to store new blogs
	blogupdate = False
	newblogs = []

	# get the rss feed
	rssfeed = get_rss(url)

	print('found ' + str(len(rssfeed['entries'])) + ' blog entries')

	# check all the retrieved articles for published dates
	for x in rssfeed['entries']:

		# retrieve post guid
		guid = str(x['guid'])
		timest_post = int(time.mktime(x['updated_parsed']))
		timest_now = int(time.time())

		datestr_post = time.strftime('%d-%m-%Y %H:%M', x['updated_parsed'])

		if guid not in guids and (timest_now < (timest_post + (86400 * days_to_retrieve))):

			link = str(x['link'])
			title = str(x['title']).replace('"', "'")
			author = str(x.get('author', 'blank'))
			
			print('retrieving '+str(title)+' in '+str(blogsource)+' using url '+str(link)+'\n')
			rawhtml, cleantxt = retrieve_url(link)
			tags = ''

			description = re.sub(r'<[^>]+>', '', str(x['description'])).strip('&nbsp;').replace('"', "'").strip('\n')

			category = 'none'
			if 'tags' in x:
				category = ', '.join(str(tag['term']) for tag in x['tags'])

			inserted = put_dynamo(timest_post, title, description, link, blogsource, author, guid, tags, category, datestr_post)
			if inserted:
				blogupdate = True
				newblogs.append(str(blogsource) + ' ' + str(title) + ' ' + str(guid))

				if send_mail == 'y':
					send_email(os.environ['toemail'], title, blogsource, author, rawhtml, link, datestr_post)

	# Counters are derived data. Refreshing them after the feed finishes avoids
	# allowing a malformed legacy counter to abort an otherwise valid article write.
	try:
		refresh_counter(blogsource)
	except Exception as error:
		print('could not refresh ' + blogsource + ' article count: ' + str(error))

	return blogupdate, newblogs


# check if new items were uploaded to s3
def get_s3_json_age():
	s3list = s3.list_objects_v2(Bucket = os.environ['s3bucket'])
	print('get s3 list ' + str(s3list))

	if 'Contents' in s3list:
		nowtime = int(time.time())
		for s3file in s3list['Contents']:
			objtime = int(time.mktime(s3file['LastModified'].timetuple()))
			difftime = nowtime - objtime
			print(str(difftime) + " " + str(s3file['Key']))

			if difftime < 300:
				return True

	return False


# get the contents of the dynamodb table for json object on S3
def get_table_json(blogsource):
	s3guids = []
	res = []

	s3list = s3.list_objects_v2(Bucket = os.environ['s3bucket'])
	s3files = [x['Key'] for x in s3list.get('Contents', [])]

	if blogsource + '.json' in s3files:
		s3obj = s3.get_object(Bucket = os.environ['s3bucket'], Key = blogsource + '.json')
		res = json.loads(s3obj['Body'].read())
		s3guids = [item['guid'] for item in res]
	else:
		print('could not find ' + blogsource + '.json file on s3')

	diff_ts = int(time.mktime((datetime.now() - timedelta(days = int(days_to_retrieve))).timetuple()))

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
		blogupdate = get_s3_json_age()
		newblogs = ''
		try:
			refresh_counter('all')
		except Exception as error:
			print('could not refresh all article count: ' + str(error))
	else:
		url = event['msg']['url']
		blogsource = event['msg']['blogsource']
		guids = event['guids']
		days_to_retrieve = int(event['msg']['daystoretrieve'])
		send_mail = event['sendemail']
		blogupdate, newblogs = get_feed(url, blogsource, guids)

	if blogupdate:
		print('updating json output on s3 for ' + blogsource)
		update_json_s3(blogsource)

	return newblogs
