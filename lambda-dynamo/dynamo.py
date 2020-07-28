#!/usr/bin/python
# @marekq
# www.marek.rocks
import base64, botocore, boto3, json, re, os, requests, queue, sys, threading, time
from boto3.dynamodb.conditions import Key, Attr
from datetime import date

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

from bs4 import *
import feedparser

patch_all()

# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 25)).Table(os.environ['dynamo_table'])
com = boto3.client(service_name = 'comprehend', region_name = os.environ['AWS_REGION'])
ses = boto3.client('ses')

# create a queue for multiprocessing
q1 = queue.Queue()

# get the blogpost guids that are already stored in dynamodb table
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
		c = ddb.query(ExclusiveStartKey = c['LastEvaluatedKey'], IndexName = 'guids', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

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
	r		= ddb.query(KeyConditionExpression=Key('source').eq(source))
	ts 		= ['0']

	for y in r['Items']:
		ts.append(y['timest'])

	return max(ts)


# write the blogpost record into DynamoDB
@xray_recorder.capture("put_dynamo")
def put_dynamo(s, timest_post, title, desc, link, source, auth, guid, tags, category):

	# if no description was submitted, put a dummy value to prevent issues parsing the output
	if len(desc) == 0:
		desc = '...'
	
	# put the record into dynamodb
	ddb.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest_post,
			'title' : title,
			'desc' : desc,
			'link' : link,
			'source' : source,
			'author' : auth,
			'tag' : tags,
			'lower-tag' : tags.lower(),
			'guid' : guid,
			'tags' : tags,
			'category' : category,
			'visible' : 'y'			
			# set the blogpost to visible by default - this "hack" allows for a simple query on a static primary key
		})

	# add dynamodb xray traces
	xray_recorder.current_subsegment().put_annotation('ddbposturl', str(link))
	xray_recorder.current_subsegment().put_annotation('ddbpostfields', str(str(timest_post)+' '+title+' '+desc+' '+link+' '+source+' '+auth+' '+guid+' '+tags+' '+category))


# retrieve the url of a blogpost
@xray_recorder.capture("retrieveurl")
def retrieve(url):

	# retrieve the url
	r = requests.get(url)
	s = BeautifulSoup(r.text, 'html.parser')

	# try finding an aws-page-content block in the html output
	# limit the blog text size to under 5000 bytes so that it can be analyzed with Comprehend (this section can be improved...)
	try:					
		t = s.find("div",  attrs = {"id" : "aws-page-content"}).getText(separator=' ')[:4750]

	except Exception as e:
		t = '.'

	return t


# analyze the text of a blogpost using the AWS Comprehend service
@xray_recorder.capture("comprehend")
def comprehend(txt, title):
	c = []
	f = False

	# check whether organization or title labels were found by comprehend
	for x in com.detect_entities(Text = txt[:4000], LanguageCode = 'en')['Entities']:
		if x['Type'] == 'ORGANIZATION' or x['Type'] == 'TITLE':
			if x['Text'] not in c and x['Text'] != 'AWS' and x['Text'] != 'Amazon' and x['Text'] != 'aws':
				c.append(x['Text'])
				f	= True

	# if no tags were retrieved, add the default aws tag
	if f:
		tags = ', '.join(c)
		
	else:
		tags = 'aws'

	# return tag values	
	return(tags)


# send an email out whenever a new blogpost was found - this feature is optional
@xray_recorder.capture("send_mail")
def send_mail(msg, subj, dest, title, auth, desc, link):

	# create a simple html body for the email
	mamsg = '<html><body><h2>'+title+'</h2><br><i>Posted by '+str(auth)+'</i><br><br>'+desc+'<br><br><a href='+link+'">view post here</a></body></html>'

	# send the email using SES
	r = ses.send_email(
		Source = os.environ['fromemail'],
		Destination = {'ToAddresses': [dest]},
		Message = {
			'Subject': {
				'Data': subj
			},
			'Body': {
				'Html': {
					'Data': mamsg
				}
			}
		}
	)
	
	print('sent email with subject '+subj)


# main function to kick off collection of an rss feed
@xray_recorder.capture("get_feed")
def get_feed(f):

	# set the url and source value of the blog
	url = f[0]
	source = f[1]

	# get the rss feed
	d = get_rss(url)

	# get the newest blogpost article from DynamoDB
	maxts = ts_dynamo(ddb, source)

	# get the title and category name of the blogpost for debugging purposes
	t = ddb.get_item(Key = {'timest': maxts, 'source': source})

	# print an error if no blogpost article was found in DynamoDB
	try:
		x = 'last blogpost in dynamodb for '+source+' has title '+str(t['Item']['title'])+'\n'

	except Exception as e:
		x = 'could not find blogs for '+source + ' in dynamodb table. by default, only blog posts from the last 3 days are retrieved.'
	
	# print debug string to stdout - disabled by default
	#print(x)

	# check all the retrieved articles for published dates
	for x in d['entries']:

		# retrieve post guid
		guid = str(x['guid'])
		timest_post = int(time.mktime(x['published_parsed']))
		timest_now = int(time.time())

		# if the post guid is not found in dynamodb and newer than 3 days, retrieve the record
		if guid not in guids and (timest_now < timest_post + (86400 * 3)):

			# retrieve other blog post values
			link = str(x['link'])
			title = str(x['title'])
			auth = str(x['author'])

			# retrieve blogpost link			
			print('retrieving '+str(title)+' in '+str(source)+' using url '+str(link)+'\n')
			txt = retrieve(link)

			# discover tags with comprehend on html output
			tags = comprehend(txt, title)	

			# retrieve blog date and description text
			date = str(x['published_parsed'])

			# clean up blog post description text and remove unwanted characters (this can be improved further)
			des	= str(x['description'])
			r = re.compile(r'<[^>]+>')
			desc = r.sub('', str(des)).strip('&nbsp;')
			
			# submit the retrieved tag values discovered by comprehend to the list
			category_tmp = []

			for tag in x['tags']:
				category_tmp.append(str(tag['term']))
	
			# join category fields in one string
			if len(category_tmp) != 0:
				category = str(', '.join(category_tmp))

			else:
				category = '.'
			
			# write the record to dynamodb
			put_dynamo(ddb, str(timest_post), title, desc, link, source, auth, guid, tags, category)

			# if sendemails enabled, generate the email message body for ses and send email
			if os.environ['sendemails'] == 'y':

				mailt = source.upper()+' - '+title
				recpt = os.environ['toemail']
				send_mail(desc, title, recpt, title, auth, desc, link)


# lambda handler
@xray_recorder.capture("handler")
def handler(event, context): 
	
	# get the unix timestamp from 3 days ago from now
	global ts_old
	ts_old = int(time.time()) - (86400 * 3)

	# get post guids stored in dynamodb
	global guids
	guids = get_guids(ts_old)

	# get feed url's from local feeds.txt file
	feeds, thr = read_feed()

	# submit a thread per url feed to queue 
	for source, url in feeds.items():
		q1.put([url, source])

	# start 20 threads
	for x in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()

