#!/usr/bin/python
# @marekq
# www.marek.rocks

import base64, botocore, boto3, csv, feedparser
import gzip, json, os, re, readability, requests
import queue, sys, threading, time

from boto3.dynamodb.conditions import Key, Attr
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()


# set how many days of feeds to retrieve blogpost based on environment variable
days_to_retrieve = int(os.environ['daystoretrieve'])

# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
com = boto3.client(service_name = 'comprehend', region_name = os.environ['AWS_REGION'])
ses = boto3.client('ses')
s3 = boto3.client('s3')


# create a queue for multiprocessing
q1 = queue.Queue()


# get the blogpost guids that are already stored in DynamoDB table
@xray_recorder.capture("get_guids")
def get_guids(ts):
	guids = []

	# get the guid values up to x days ago
	queryres = ddb.query(ScanIndexForward = True, IndexName = 'timest', ProjectionExpression = 'guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

	for x in queryres['Items']:
		if 'guid' in x:
			if x['guid'] not in guids:
				guids.append(x['guid'])

	# paginate the query in case more than 100 results are returned
	while 'LastEvaluatedKey' in queryres:
		queryres = ddb.query(ExclusiveStartKey = queryres['LastEvaluatedKey'], ScanIndexForward = True, IndexName = 'timest', ProjectionExpression = 'guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

		for x in queryres['Items']:
			if 'guid' in x:
				if x['guid'] not in guids:
					guids.append(x['guid'])

	xray_recorder.current_subsegment().put_annotation('postcountguid', str(len(guids)))

	print('guids found in last ' + str(days_to_retrieve) + ' days : '+str(len(guids)))
	return guids


# worker for queue jobs
def worker():
	while not q1.empty():
		get_feed(q1.get())
		q1.task_done()


# get the RSS feed through feedparser
@xray_recorder.capture("get_rss")
def get_rss(url):
	return feedparser.parse(url)


# read the url's from 'feeds.txt' stored in the lambda function
@xray_recorder.capture("read_feed")
def read_feed():
	result = {}
	filen = 'feeds.txt'
	count = 0

	# open the feeds.txt file and read line by line
	with open(filen) as fp:
		line = fp.readline()
		while line:

			# get the src and url value delimited by a ','
			src, url = line.split(',')

			# add src and url to dict
			result[src.strip()] = url.strip()
			line = fp.readline()

			# add one to the count if less than 50, else we will spawn too many threads
			if count < 50:
				count += 1

	# return the result and count value
	return result, count


# write the blogpost record into DynamoDB
@xray_recorder.capture("put_dynamo")
def put_dynamo(timest_post, title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post):

	# if no description was submitted, put a dummy value to prevent issues parsing the output
	if len(description) == 0:
		description = '...'
	
	# put the record into dynamodb
	ddb.put_item(
		TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest_post,			# store the unix timestamp of the post
			'datestr' : datestr_post,		# store the human friendly timestamp of the post
			'title' : title,
			'description' : description,	# store the short rss feed description of the content
			'fulltxt': cleantxt,			# store the "clean" text of the blogpost, using \n as a line delimiter
			'rawhtml': rawhtml,				# store the raw html output of the readability plugin, in order to include the blog content with text markup
			'link' : link,
			'blogsource' : blogsource,
			'author' : author,
			'tag' : tags,
			'lower-tag' : tags.lower(),		# convert the tags to lowercase, which makes it easier to search or match these
			'guid' : guid,					# store the blogpost guid as a unique key
			'category' : category,
			'visible' : 'y'					# set the blogpost to visible by default - this "hack" allows for a simple query on a static primary key
		})

	# add dynamodb xray traces
	xray_recorder.current_subsegment().put_annotation('ddbposturl', str(link))
	xray_recorder.current_subsegment().put_annotation('ddbpostfields', str(str(timest_post)+' '+title+' '+description+' '+link+' '+blogsource+' '+author+' '+guid+' '+tags+' '+category))


# retrieve the url of a blogpost
@xray_recorder.capture("retrieveurl")
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
@xray_recorder.capture("comprehend")
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
@xray_recorder.capture("send_mail")
def send_mail(recpt, title, blogsource, author, rawhtml, link, datestr_post):

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
@xray_recorder.capture("get_feed")
def get_feed(f):

	# set the url and source value of the blog
	url = f[0]
	blogsource = f[1]

	# get the rss feed
	rssfeed = get_rss(url)

	# check all the retrieved articles for published dates
	for x in rssfeed['entries']:

		# retrieve post guid
		guid = str(x['guid'])
		timest_post = int(time.mktime(x['updated_parsed']))
		timest_now = int(time.time())

		# retrieve blog date and description text
		datestr_post = time.strftime('%d-%m-%Y %H:%M', x['updated_parsed'])

		# if the post guid is not found in dynamodb and newer than the specified amount of days, retrieve the record
		if guid not in guids and (timest_now < timest_post + (86400 * days_to_retrieve)):

			# add the blog source to the queue for updating json objects on S3
			if blogsource not in blog_queue:
				blog_queue.append(blogsource)

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
			
			# write the record to dynamodb, only if the guid is not present already. this prevents double posts from appearing by crossposting. 
			if guid not in guids:
				put_dynamo(str(timest_post), title, cleantxt, rawhtml, description, link, blogsource, author, guid, tags, category, datestr_post)

				# if sendemails enabled, generate the email message body for ses and send email
				if os.environ['sendemails'] == 'y':

					# get mail title and email recepient
					mailt = blogsource.upper()+' - '+title
					recpt = os.environ['toemail']

					# send the email
					send_mail(recpt, title, blogsource, author, rawhtml, link, datestr_post)

			else:
				
				# skip, print message that a guid was found twice
				print("skipped double guid " + guid + " " + blogsource + " " + title + " " + datestr_post)


# get the contents of the dynamodb table for json object on S3
@xray_recorder.capture("get_table_json")
def get_table_json(blogsource):

	# create a list for found guids from s3 json
	s3guids = []

	# check if the s3 object exists by listing current s3 objects
	s3files = []
	s3list = s3.list_objects(Bucket = os.environ['s3bucket'])

	# iterate over present files in s3
	for x in s3list['Contents']:
		s3files.append(x['Key'])

	# if the blog json is available on s3
	if str(blogsource + '.json') in s3files:
		
		# since there is a json present, retrieve the blog contents from there and update only 1 days of blogposts from dynamodb
		# this reduces the read capacity consumed by dynamodb on a file update
		days_to_get = 1

		# retrieve the object from s3
		s3obj = s3.get_object(Bucket = os.environ['s3bucket'], Key = blogsource + '.json')
		
		# create list for results from json
		res = json.loads(s3obj['Body'].read())

		# add guids from json file to s3guids list
		for s3file in res:
			s3guids.append(s3file['guid'])

	# if the blog json does not exist on s3
	else:
		
		# set the days to retrieve value based on the given setting
		days_to_get = days_to_retrieve

		# since the previous results can not be found, create an emptylist for results and get current time
		res = []

	# get the current timestamp
	now_ts = datetime.now()

	# get timestamp based on days_to_retrieve 
	old_ts = now_ts - timedelta(days = days_to_get)
	diff_ts = int(time.mktime(old_ts.timetuple()))

	if blogsource != 'all':

		# query the dynamodb table for blogposts of a specific category from up to 1 day ago
		blogs = ddb.query(ScanIndexForward = True, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(str(diff_ts)))
			
	else:

		# query the dynamodb table for all category blogposts from up to 1 day ago
		blogs = ddb.query(ScanIndexForward = True, IndexName = 'timest', ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(diff_ts)))

	# iterate over the returned items
	for a in blogs['Items']:

		# if guid not present in s3 json file
		if a['guid'] not in s3guids:

			b = {'timest': a['timest'], 'blogsource': a['blogsource'], 'title': a['title'], 'datestr': a['datestr'], 'guid': a['guid'], 'author': a['author'], 'link': a['link'], 'description': a['description'].strip(), 'author': a['author']}
			
			# add the json object to the result list
			res.append(b)

		# retrieve additional items if lastevaluatedkey was found 
		while 'LastEvaluatedKey' in blogs:
			lastkey = blogs['LastEvaluatedKey']

			if blogsource != 'all':

				# query the dynamodb table for blogposts of a specific category 
				blogs = ddb.query(ScanIndexForward = True, ExclusiveStartKey = lastkey, ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('source').eq(source) & Key('timest').gt(str(diff_ts)))
			
			else:

				# query the dynamodb table for all category blogposts from up to 30 days old
				blogs = ddb.query(ScanIndexForward = True, ExclusiveStartKey = lastkey, IndexName = 'timest', ProjectionExpression = 'blogsource, datestr, timest, title, author, description, link, guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(diff_ts)))

			# add an entry per blog to the output list
			for a in blogs['Items']:
				
				# if guid not present in s3 json file
				if a['guid'] not in s3guids:

					b = {'timest': a['timest'], 'blogsource': a['blogsource'], 'title': a['title'], 'datestr': a['datestr'], 'guid': a['guid'], 'author': a['author'], 'link': a['link'], 'description': a['description'].strip(), 'author': a['author']}
					
					# add the json object to the result list
					res.append(b)

	return res


# copy the file to s3 with a public acl
@xray_recorder.capture("cp_s3")
def cp_s3(blogsource):

	s3.put_object(
		Bucket = os.environ['s3bucket'], 
		Body = open('/tmp/' + blogsource + '.json', 'rb'), 
		Key = blogsource + '.json', 
		ACL = 'public-read',
		CacheControl = 'public',
		ContentType = 'application/json'
	)


# update json objects on S3 for single page web apps
@xray_recorder.capture("update_json_s3")
def update_json_s3(blog_queue):
	
	# add refresh of all blogposts if at least one category was triggered
	if len(blog_queue) != 0:
		blog_queue.append('all')

		# get the json content from DynamoDB
		out = get_table_json('all')

		# update the json per blog
		for blog in blog_queue:
		
			# create the json and return path
			make_json(out, blog)

			# upload the json to s3
			cp_s3(blog)


# create a json file from blog content
@xray_recorder.capture("make_json")
def make_json(content, blogsource):
	
	# write the json file to /tmp/
	fpath = '/tmp/' + blogsource + '.json'

	filteredcontent = []

	for blog in content:
		if blog['blogsource'] == blogsource or blogsource == 'all':
			filteredcontent.append(blog)

	with open(fpath, "w") as outfile: 
		json.dump(filteredcontent, outfile) 

	print('wrote to ' + fpath)


# lambda handler
@xray_recorder.capture("handler")
def handler(event, context): 
	
	# get the unix timestamp from variable 'days_to_retrieve'
	ts_old = int(time.time()) - (86400 * days_to_retrieve)

	# get post guids stored in dynamodb
	global guids
	guids = get_guids(ts_old)

	# create list to store queues with new blogs
	global blog_queue
	blog_queue = []

	# get feed url's from local feeds.txt file
	feeds, thr = read_feed()

	# submit a thread per url feed to queue 
	for blogsource, url in feeds.items():
		q1.put([url, blogsource])

	# start thread per feed
	for x in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()

	# update the json files on s3 for updated sources
	if os.environ['storepublics3'] == 'y':
		update_json_s3(blog_queue)
