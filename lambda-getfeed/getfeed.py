from provider_feed import add_provider
#!/usr/bin/python
# @marekq
# www.marek.rocks

import botocore, boto3, feedparser
import json, os, re, readability, requests
import sys, time

from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

logger = Logger()
tracer = Tracer(patch_modules = ["boto3", "requests"])


# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
com = boto3.client(service_name = 'comprehend', region_name = os.environ['AWS_REGION'])
ses = boto3.client('ses')
s3 = boto3.client('s3')


# get the RSS feed through feedparser
@tracer.capture_method(capture_response = False)
def get_rss(url):
	return feedparser.parse(url)


# update the item count in dynamodb by 1
@tracer.capture_method(capture_response = False)
def update_itemcount(blogsource):
	
	# update guid: <blogsource>, timest: 0
	ddb.update_item(
		Key = { "guid" : blogsource, "timest" : 0 },
		ExpressionAttributeValues = { ":inc" : 1 },
		UpdateExpression = "ADD articlecount :inc"
	)

	print('incremented ' + blogsource + ' count by 1')


# write the blogpost record into DynamoDB
@tracer.capture_method(capture_response = False)
def put_dynamo(timest_post, title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post):

	if not description:
		description = '...'

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
		'fulltxt': cleantxt,
		'lower-tag' : tags.lower(),
		'rawhtml': rawhtml,
		'tag' : tags,
		'visible' : 'y'
	}

	add_provider(fullitem)

	# put the full record into dynamodb
	ddb.put_item(
		TableName = os.environ['dynamo_table'],
		Item = fullitem
	)

	# increment dynamodb counter for blog category by 1
	update_itemcount(blogsource)

	# increment dynamodb counter for all blogs by 1
	update_itemcount('all')


# retrieve the url of a blogpost
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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

			blogupdate = True

			put_dynamo(timest_post, title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post)
			newblogs.append(str(blogsource) + ' ' + str(title) + ' ' + str(guid))

			if send_mail == 'y':
				send_email(os.environ['toemail'], title, blogsource, author, rawhtml, link, datestr_post)

	return blogupdate, newblogs


# check if new items were uploaded to s3
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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
@tracer.capture_method(capture_response = False)
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
@logger.inject_lambda_context(log_event = True)
@tracer.capture_lambda_handler
def handler(event, context):
	global days_to_retrieve, send_mail
	days_to_retrieve = 1
	send_mail = ''

	if event['msg'] == 'all':
		blogsource = 'all'
		blogupdate = get_s3_json_age()
		newblogs = ''
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
