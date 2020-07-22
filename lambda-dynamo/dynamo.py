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

# get all the urls of links that are already stored in dynamodb
@xray_recorder.capture("get_guids")
def get_guids():
	a = []
	
	c = ddb.query(IndexName = 'allts', KeyConditionExpression = Key('allts').eq('y'), ProjectionExpression = 'guid')

	for x in c['Items']:
		if 'guid' in x:
			if x['guid'] not in a:
				a.append(x['guid'])

	while 'LastEvaluatedKey' in c:
		c = ddb.query(ExclusiveStartKey = c['LastEvaluatedKey'], IndexName = 'allts', KeyConditionExpression = Key('allts').eq('y'), ProjectionExpression = 'guid')

		for x in c['Items']:
			if 'guid' in x:
				if x['guid'] not in a:
					a.append(x['guid'])

	xray_recorder.current_subsegment().put_annotation('postcountguid', str(len(a)))

	print('len allts '+str(len(a)))
	return a


# worker for queue jobs
def worker():
	while not q1.empty():
		get_feed(q1.get())
		q1.task_done()


# get the RSS feed through feedparser
@xray_recorder.capture("get_rss")
def get_rss(url):
	return feedparser.parse(url)


# read the url's from 'feeds.txt'
def read_feed():
	r = {}
	f = 'feeds.txt'
	c = 0

	# open the feeds file and read line by line
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
def put_dynamo(s, timest, title, desc, link, source, auth, guid, tags, category):
	if len(desc) == 0:
		desc = '...'
	
	xray_recorder.current_subsegment().put_annotation('ddbposturl', str(link))
	xray_recorder.current_subsegment().put_annotation('ddbpostfields', str(str(timest)+' '+title+' '+desc+' '+link+' '+source+' '+auth+' '+guid+' '+tags+' '+category))

	ddb.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest,
			'title' : title,
			'desc' : desc,
			'link' : link,
			'source' : source,
			'author' : auth,
			'tag' : tags,
			'lower-tag' : tags.lower(),
			'allts' : 'y',
			'guid' : guid,
			'tags' : tags,
			'category' : category
		})


# retrieve the url of a blogpost
@xray_recorder.capture("retrieveurl")
def retrieve(url):
	r = requests.get(url)
	s = BeautifulSoup(r.text, 'html.parser')

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

	for x in com.detect_entities(Text = txt[:4000], LanguageCode = 'en')['Entities']:
		if x['Type'] == 'ORGANIZATION' or x['Type'] == 'TITLE':
			if x['Text'] not in c and x['Text'] != 'AWS' and x['Text'] != 'Amazon' and x['Text'] != 'aws':
				c.append(x['Text'])
				f	= True

	if f:
		tags = ', '.join(c)
		
	else:
		tags = 'aws'

	print(title, '\n', tags, '\n')
	
	return(tags)


# send an email out whenever a new blogpost was found
@xray_recorder.capture("send_mail")
def send_mail(msg, subj, dest, title, auth, desc, link):

	mamsg = '<html><body><h2>'+title+'</h2><br><i>Posted by '+str(auth)+'</i><br><br>'+desc+'<br><br><a href='+link+'">view post here</a></body></html>'

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
	url = f[0]
	source = f[1]
	d = get_rss(url)

	# get the newest blogpost article from DynamoDB
	maxts = ts_dynamo(ddb, source)
	t = ddb.get_item(Key = {'timest': maxts, 'source': source})

	# print an error if no blogpost article was found in DynamoDB
	try:
		x = 'last blogpost in '+source+' has title '+str(t['Item']['title'])+'\n'

	except Exception as e:
		print('could not find blogs for '+source)

	# create a list for timestamps
	stamps = []
	
	# check all the retrieved articles
	for x in d['entries']:
		timest = int(time.mktime(x['published_parsed']))
		c = int(stamps.count(timest))

		link = str(x['link'])
		title = str(x['title'])
		guid = str(x['guid'])

		# TODO - make a better fix to prevent blogposts with identical timestamps from overwriting content in dynamodb
		if c != 1:
			#print('$ adding '+str(c)+' to timestamp '+str(timest)+' for '+link.strip()+' due to double timestamp in source feed')
			timest = timest + c

		# if the post guid is not found in dynamodb, retrieve the record
		if guid not in guids:
			print('retrieving '+str(title)+' in '+str(source)+' using url '+str(link)+'\n')
			stamps.append(timest)
	
			# retrieve blog date and description text
			date = str(x['published_parsed'])

			# clean up blog post description text 
			des	= str(x['description'])
			r = re.compile(r'<[^>]+>')
			desc = r.sub('', str(des)).strip('&nbsp;')
			
			guid = str(x['id'])

			# submit retrieved tag values to list
			cc = []

			for a in x['tags']:
				cc.append(str(a['term']))
	
			# join category fields in one string
			if len(cc) != 0:
				category = str(', '.join(cc))
			else:
				category = '.'
			
			# retrieve other blog post values
			auth = str(x['author'])
			txt = retrieve(link)
			tags = comprehend(txt, title)			
			mailt = source.upper()+' - '+title
			recpt = os.environ['toemail']
			
			# write the record to dynamodb
			put_dynamo(ddb, str(timest), title, desc, link, source, auth, guid, tags, category)

			# if sendemails enabled, generate the email message body for ses and send email
			if os.environ['sendemails'] == 'y':
		
				send_mail(desc, title, recpt, title, auth, desc, link)


# lambda handler
@xray_recorder.capture("handler")
def handler(event, context): 
	
	# get post guids
	global guids
	guids = get_guids()

	# get feed url's from local file
	feeds, thr = read_feed()

	# add task per url source
	for source, url in feeds.items():
		q1.put([url, source])

	# start 20 threads
	for x in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()

