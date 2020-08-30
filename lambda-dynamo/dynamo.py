#!/usr/bin/python
# @marekq
# www.marek.rocks

import base64, botocore, boto3, fake_useragent, feedparser, json, os, re, readability, requests, queue, sys, threading, time
from boto3.dynamodb.conditions import Key, Attr
from datetime import date
from bs4 import BeautifulSoup

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()


# set how many days of feeds to retrieve blogpost based on environment variable
days_to_retrieve = int(os.environ['daystoretrieve'])

# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 25)).Table(os.environ['dynamo_table'])
com = boto3.client(service_name = 'comprehend', region_name = os.environ['AWS_REGION'])
ses = boto3.client('ses')

# create a queue for multiprocessing
q1 = queue.Queue()

# get the blogpost guids that are already stored in DynamoDB table
@xray_recorder.capture("get_guids")
def get_guids(ts):
	guids = []

	# get the guid values up to x days ago
	queryres = ddb.query(IndexName = 'guids', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

	for x in queryres['Items']:
		if 'guid' in x:
			if x['guid'] not in guids:
				guids.append(x['guid'])

	# paginate the query in case more than 100 results are returned
	while 'LastEvaluatedKey' in queryres:
		queryres = ddb.query(ExclusiveStartKey = queryres['LastEvaluatedKey'], IndexName = 'guids', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

		for x in queryres['Items']:
			if 'guid' in x:
				if x['guid'] not in guids:
					guids.append(x['guid'])

	xray_recorder.current_subsegment().put_annotation('postcountguid', str(len(guids)))

	print('guids found: '+str(len(guids)))
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
	r = {}
	f = 'feeds.txt'
	c = 0

	# open the feeds.txt file and read line by line
	with open(f) as fp:
		line = fp.readline()
		while line:

			# get the src and url value delimited by a ','
			src, url = line.split(',')

			# add src and url to dict
			r[src.strip()] = url.strip()
			line = fp.readline()

			# add one to the count
			c += 1

	# return the dict and count value
	return r, c


# get the timestamp of the latest blogpost stored in DynamoDB
def ts_dynamo(s, source):
	r		= ddb.query(KeyConditionExpression = Key('source').eq(source))
	ts 		= ['0']

	for y in r['Items']:
		ts.append(y['timest'])

	return max(ts)


# write the blogpost record into DynamoDB
@xray_recorder.capture("put_dynamo")
def put_dynamo(timest_post, title, cleantxt, rawhtml, desc, link, source, author, guid, tags, category, datestr_post):

	# if no description was submitted, put a dummy value to prevent issues parsing the output
	if len(desc) == 0:
		desc = '...'
	
	# put the record into dynamodb
	ddb.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest_post,			# store the unix timestamp of the post
			'datestr' : datestr_post,		# store the human friendly timestamp of the post
			'title' : title,
			'desc' : desc,					# store the short rss feed description of the content
			'fulltxt': cleantxt,			# store the "clean" text of the blogpost, using \n as a line delimiter
			'rawhtml': rawhtml,				# store the raw html output of the readability plugin, in order to include the blog content with text markup
			'link' : link,
			'source' : source,
			'author' : author,
			'tag' : tags,
			'lower-tag' : tags.lower(),		# convert the tags to lowercase, which makes it easier to search or match these
			'guid' : guid,					# store the blogpost guid as a unique key
			'category' : category,
			'visible' : 'y'					# set the blogpost to visible by default - this "hack" allows for a simple query on a static primary key
		})

	# add dynamodb xray traces
	xray_recorder.current_subsegment().put_annotation('ddbposturl', str(link))
	xray_recorder.current_subsegment().put_annotation('ddbpostfields', str(str(timest_post)+' '+title+' '+desc+' '+link+' '+source+' '+author+' '+guid+' '+tags+' '+category))


# retrieve the url of a blogpost
@xray_recorder.capture("retrieveurl")
def retrieve_url(url):

	# get a "real" user agent
	ua = fake_useragent.UserAgent()
	chrome = ua.chrome

	# retrieve the main text section from the url using the readability module and using the Chrome user agent
	req = requests.get(url, headers = {'User-Agent' : chrome})
	doc = readability.Document(req.text)
	rawhtml = doc.summary(html_partial = True)

	# remove any html tags from output
	soup = BeautifulSoup(rawhtml, 'html.parser')
	cleantext = soup.get_text().encode('utf-8')

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
			detections.append(x['Text'])
			found = True

	# if no tags were retrieved, add the default aws tag
	if found:
		tags = ', '.join(detections)
		
	else:
		tags = '.'

	# return tag values	
	return(tags)


# send an email out whenever a new blogpost was found - this feature is optional
@xray_recorder.capture("send_mail")
def send_mail(recpt, title, source, author, rawhtml, link, datestr_post):

	# create a simple html body for the email
	mailmsg = '<html><body><h2>'+title+'</h2><br><i>Posted by '+str(author)+' on ' + str(datestr_post) + '</i><br>'
	mailmsg += '<a href="' + link + '">view post here</a><br><br>' + str(rawhtml) + '<br></body></html>'

	# send the email using SES
	r = ses.send_email(
		Source = os.environ['fromemail'],
		Destination = {'ToAddresses': [recpt]},
		Message = {
			'Subject': {
				'Data': source.upper() + ' - ' + title
			},
			'Body': {
				'Html': {
					'Data': mailmsg
				}
			}
		}
	)
	
	print('sent email with subject ' + source.upper() + ' - ' + title + ' to ' + recpt)


# main function to kick off collection of an rss feed
@xray_recorder.capture("get_feed")
def get_feed(f):

	# set the url and source value of the blog
	url = f[0]
	source = f[1]

	# get the rss feed
	rssfeed = get_rss(url)

	# get the newest blogpost article from DynamoDB
	maxts = ts_dynamo(ddb, source)

	# get the title and category name of the blogpost for debugging purposes
	tablefeed = ddb.get_item(Key = {'timest': maxts, 'source': source})

	# print an error if no blogpost article was found in DynamoDB
	try:
		x = 'last downloaded blogpost for '+source+' has title '+str(tablefeed['Item']['title'])+'\n'
		print(x)

	except Exception as e:
		x = 'could not find blogs for '+source + ' in dynamodb table. by default, only blog posts from the last ' + str(days_to_retrieve) + ' days are retrieved\n'	
		print(x)

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

			# retrieve other blog post values
			link = str(x['link'])
			title = str(x['title'])

			# retrieve the blogpost author if available
			if x.has_key('author'):
				author = str(x['author'])
			else:
				author = 'blank'

			# retrieve blogpost link			
			print('retrieving '+str(title)+' in '+str(source)+' using url '+str(link)+'\n')
			rawhtml, cleantxt = retrieve_url(link)

			# discover tags with comprehend on html output
			tags = comprehend(cleantxt, title)	

			# clean up blog post description text and remove unwanted characters (this can be improved further)
			des	= str(x['description'])
			r = re.compile(r'<[^>]+>')
			desc = r.sub('', str(des)).strip('&nbsp;')
			
			# submit the retrieved tag values discovered by comprehend to the list
			category_tmp = []
			category = 'none'

			# join category fields in one string
			if x.has_key('tags'):
				for tag in x['tags']:
					category_tmp.append(str(tag['term']))
	
				category = str(', '.join(category_tmp))
			
			# write the record to dynamodb
			put_dynamo(str(timest_post), title, cleantxt, rawhtml, desc, link, source, author, guid, tags, category, datestr_post)

			# if sendemails enabled, generate the email message body for ses and send email
			if os.environ['sendemails'] == 'y':

				# get mail title and email recepient
				mailt = source.upper()+' - '+title
				recpt = os.environ['toemail']

				# send the email
				send_mail(recpt, title, source, author, rawhtml, link, datestr_post)


# lambda handler
@xray_recorder.capture("handler")
def handler(event, context): 
	
	# get the unix timestamp from 3 days ago from now
	ts_old = int(time.time()) - (86400 * days_to_retrieve)

	# get post guids stored in dynamodb
	global guids
	guids = get_guids(ts_old)

	# get feed url's from local feeds.txt file
	feeds, thr = read_feed()

	# submit a thread per url feed to queue 
	for source, url in feeds.items():
		q1.put([url, source])

	# start thread per feed
	for x in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()
