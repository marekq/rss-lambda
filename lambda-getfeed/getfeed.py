#!/usr/bin/python
# @marekq
# www.marek.rocks

import base64, botocore, boto3, csv, feedparser
import gzip, json, os, re, readability, requests
import queue, sys, threading, time
from algoliasearch.search_client import SearchClient

from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.conditions import Key, Attr
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

modules_to_be_patched = [ "boto3", "requests" ]
tracer = Tracer(patch_modules = modules_to_be_patched)

logger = Logger()
tracer = Tracer()


# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['AWS_REGION'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
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
def put_dynamo(timest_post, title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post, table, event):

	# if no description was submitted, put a dummy value to prevent issues parsing the output
	if len(description) == 0:
		description = '...'
	
	# create item payload for algolia
	smallitem = {
		'objectID' : guid,				# add unique object id for Algolia search
		'timest' : timest_post,			# store the unix timestamp of the post as an int
		'title' : title,
		'description' : description,	# store the short rss feed description of the content
		'link' : link,
		'blogsource' : blogsource,
		'author' : author,
		'guid' : guid					# store the blogpost guid as a unique key
	} 

	# add additional attributes for dynamodb record
	extraitem = {
		'category' : category,
		'datestr' : datestr_post,		# store the human friendly timestamp of the post
		'fulltxt': cleantxt,			# store the "clean" text of the blogpost, using \n as a line delimiter
		'lower-tag' : tags.lower(),		# convert the tags to lowercase, which makes it easier to search or match these
		'rawhtml': rawhtml,				# store the raw html output of the readability plugin, in order to include the blog content with text markup
		'tag' : tags,					# set the comprehend tags
		'visible' : 'y'					# set the blogpost to visible by default - this "hack" allows for a simple query on a static primary key
	}

	# optionally, put the small record in your Algolia search DB if the API key is set
	if event['enable_algolia'] == 'y':

		client = SearchClient.create(event['algolia_app'], event['algolia_apikey'])
		index = client.init_index(event['algolia_index'])
		index.save_objects([smallitem])

	# merge small and extra item for dynamodb
	def Merge(dict1, dict2):
		res = {**dict1, **dict2}
		return res

	# create fullitem for dynamodb
	fullitem = Merge(smallitem, extraitem)

	# put the full record into dynamodb
	ddb.put_item(
		TableName = table, 
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

	# retrieve the main text section from the url using the readability module and using the Chrome user agent
	req = requests.get(url, headers = {'User-Agent' : firefox})
	doc = readability.Document(req.text)
	rawhtml = doc.summary(html_partial = True)

	# remove any html tags from output
	soup = BeautifulSoup(rawhtml, 'html.parser')
	cleantext = soup.get_text().strip('\n').encode('utf-8')

	return str(rawhtml), str(cleantext)


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
	ses.send_email(
		Source = event['from_email'],
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
def get_feed(url, blogsource, guids, table, event):

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

		# retrieve blog date and description text
		datestr_post = time.strftime('%d-%m-%Y %H:%M', x['updated_parsed'])

		# if the post guid is not found in dynamodb and newer than the specified amount of days, retrieve the record
		if guid not in guids and (timest_now < (timest_post + (86400 * days_to_retrieve))):

			# retrieve other blog post values, remove double quotes from title
			link = str(x['link'])
			title = str(x['title']).replace('"', "'")

			# retrieve the blogpost author if available
			author = 'blank'

			if x.has_key('author'):
				author = str(x['author'])
			
			# retrieve blogpost link			
			print('retrieving '+str(title)+' in '+str(blogsource)+' using url '+str(link)+'\n')
			rawhtml, cleantxt = retrieve_url(link)

			# discover tags with comprehend on html output
			tags = comprehend(cleantxt, title)	

			# clean up blog post description text and remove unwanted characters such as double quotes and spaces (this can be improved further)
			des	= str(x['description'])
			r = re.compile(r'<[^>]+>')
			description = r.sub('', str(des)).strip('&nbsp;').replace('"', "'").strip('\n')
			
			# submit the retrieved tag values discovered by comprehend to the list
			category_tmp = []
			category = 'none'

			# join category fields in one string
			if x.has_key('tags'):
				for tag in x['tags']:
					category_tmp.append(str(tag['term']))
	
				category = str(', '.join(category_tmp))

			# update the blogpost
			blogupdate = True

			# put record to dynamodb
			put_dynamo(timest_post, title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post, table, event)

			# add blog to newblogs list
			newblogs.append(str(blogsource) + ' ' + str(title) + ' ' + str(guid))

			# if sendemails enabled, generate the email message body for ses and send email
			if send_mail == 'y':

				# get mail title and email recepient
				title = blogsource.upper()+' - '+title
				recpt = event['to_email']

				# send the email
				send_email(recpt, title, blogsource, author, rawhtml, link, datestr_post)

	return blogupdate, newblogs


# check if new items were uploaded to s3
@tracer.capture_method(capture_response = False)
def get_s3_json_age(bucket):

	# set variable for s3 update operation
	updateblog = False

	# list objects in s3
	s3list = s3.list_objects_v2(Bucket = bucket)

	print('get s3 list ' + str(s3list))

	# iterate over present files in s3
	if 'Contents' in s3list:
		for s3file in s3list['Contents']:

			# get last modified time of item
			s3time = s3file['LastModified']

			objtime = int(time.mktime(s3time.timetuple()))
			nowtime = int(time.time())
			difftime = nowtime - objtime

			# if an s3 file was created in the last 300 seconds, update the blog feed
			if difftime < 300:
				updateblog = True
		
			print(str(difftime) + " " + str(s3file['Key']))

	# return true/false about blog update status
	return updateblog


# get the contents of the dynamodb table for json object on S3
@tracer.capture_method(capture_response = False)
def get_table_json(blogsource, bucket):

	# create a list for found guids from json stored on s3
	s3guids = []

	# create a list for s3 objects that were found
	s3files = []

	# check if the s3 object exists by listing current s3 objects
	s3list = s3.list_objects_v2(Bucket = bucket)

	# set days_to_get value
	days_to_get = int(days_to_retrieve)


	# iterate over present files in s3
	if 'Contents' in s3list:

		for x in s3list['Contents']:
			s3files.append(x['Key'])

	# if the blog json is available on s3
	if str(blogsource + '.json') in s3files:
		
		# retrieve the object from s3
		s3obj = s3.get_object(Bucket = bucket, Key = blogsource + '.json')
		
		# create list for results from json
		res = json.loads(s3obj['Body'].read())

		# add guids from json file to s3guids list
		for s3file in res:
			s3guids.append(s3file['guid'])

	# if the blog json does not exist on s3
	else:

		# since the previous results can not be found, create an emptylist for results and get current time
		res = []

		print('could not find ' + blogsource + '.json file on s3')

	# get the current timestamp
	now_ts = datetime.now()

	# get timestamp based on days_to_retrieve 
	old_ts = now_ts - timedelta(days = days_to_get)
	diff_ts = int(time.mktime(old_ts.timetuple()))

	if blogsource != 'all':

		# query the dynamodb table for blogposts of a specific category from up to 1 day ago
		blogs = ddb.query(IndexName = "timest", ScanIndexForward = True, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(diff_ts))
			
	else:

		# query the dynamodb table for all category blogposts from up to 1 day ago
		blogs = ddb.query(IndexName = "visible", ScanIndexForward = True, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(diff_ts))

	# iterate over the returned items
	for a in blogs['Items']:

		# if guid not present in s3 json file
		if a['guid'] not in s3guids:

			b = {'timest': str(a['timest']), 'blogsource': a['blogsource'], 'title': a['title'], 'datestr': a['datestr'], 'guid': a['guid'], 'link': a['link'], 'description': a['description'].strip(), 'author': a['author']}
			
			# add the json object to the result list
			res.append(b)

		# retrieve additional items if lastevaluatedkey was found 
		while 'LastEvaluatedKey' in blogs:
			lastkey = blogs['LastEvaluatedKey']

			if blogsource != 'all':

				# query the dynamodb table for blogposts of a specific category 
				blogs = ddb.query(IndexName = "timest", ScanIndexForward = True, ExclusiveStartKey = lastkey, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(diff_ts))
			
			else:

				# query the dynamodb table for all category blogposts from up to 30 days old
				blogs = ddb.query(IndexName = "visible", ScanIndexForward = True, ExclusiveStartKey = lastkey, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(diff_ts))

			# add an entry per blog to the output list
			for a in blogs['Items']:
				
				# if guid not present in s3 json file
				if a['guid'] not in s3guids:

					b = {'timest': str(a['timest']), 'blogsource': a['blogsource'], 'title': a['title'], 'datestr': a['datestr'], 'guid': a['guid'], 'author': a['author'], 'link': a['link'], 'description': a['description'].strip()}
					
					# add the json object to the result list
					res.append(b)

	return res


# copy the file to s3 with a public acl
@tracer.capture_method(capture_response = False)
def cp_s3(blogsource, bucket):

	# put object to s3
	s3.put_object(
		Bucket = bucket, 
		Body = open('/tmp/' + blogsource + '.json', 'rb'), 
		Key = blogsource + '.json', 
		ACL = 'public-read',
		CacheControl = 'public',
		ContentType = 'application/json'
	)


# update json objects on S3 for single page web apps
@tracer.capture_method(capture_response = False)
def update_json_s3(blog, bucket):

	print('updating json for ' + blog)

	# get the json content from DynamoDB
	out = get_table_json(blog, bucket)

	# create the json and return path
	make_json(out, blog)

	# upload the json to s3
	cp_s3(blog, bucket)


# create a json file from blog content
def make_json(content, blogsource):
	
	# write the json file to /tmp/
	fpath = '/tmp/' + blogsource + '.json'

	# create empty list for filteredcontent
	filteredcontent = []

	# filter blog posts for category
	for blog in content:
		if blog['blogsource'] == blogsource or blogsource == 'all':
			filteredcontent.append(blog)

	# sort the keys by timestamp
	dumpfile = sorted(filteredcontent, key = lambda k: k['timest'], reverse = True)

	with open(fpath, "w") as outfile: 
		json.dump(dumpfile, outfile) 

	print('wrote to ' + fpath)


# lambda handler
@logger.inject_lambda_context(log_event = True)
@tracer.capture_lambda_handler
def handler(event, context): 
	
	print('event ' + str(event))

	# set default value for 'days_to_retrieve' 
	global days_to_retrieve
	days_to_retrieve = int(1)

	# set send email boolean, newblog and blogupdate default values
	global send_mail
	send_mail = event['send_mail']
	blogupdate = False
	newblogs = ''

	bucket = event['s3_bucket']
	table = os.environ['dynamo_table']

	# if updating all blogposts, set source to 'all' and skip blogpost retrieval 
	if event['msg'] == 'all':
		blogsource = 'all'

		# check if there are files on s3 less than 60 seconds old
		blogupdate = get_s3_json_age(bucket)

	else:

		# get submitted values from blog to retrieve
		url = event['msg']['url']
		blogsource = event['msg']['blogsource']
		guids = event['guids']
		days_to_retrieve = int(event['msg']['daystoretrieve'])
		send_mail = event['send_mail']

		# get feed and boolean indicating if an update to s3 is required
		blogupdate, newblogs = get_feed(url, blogsource, guids, table, event)

	# if new blogposts found, create new json output on s3
	if blogupdate == True and event['storepublics3'] == 'y':
		print('updating json output on s3 for ' + blogsource)
		update_json_s3(blogsource,  bucket)

	return newblogs
