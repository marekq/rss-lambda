#!/usr/bin/python
# @marekq
# www.marek.rocks

# import dependancies 
import base64, boto3, re, os, queue, sys, threading, time
from boto3.dynamodb.conditions import Key, Attr

# import packaged dependancies from 'libs/' folder
sys.path.append('./libs')
from bs4 import *
import feedparser, requests

# establish a session with DynamoDB and SES
ses		= boto3.client('ses')
ddb		= boto3.resource('dynamodb', region_name = os.environ['dynamo_region']).Table(os.environ['dynamo_table'])
comp 	= boto3.client(service_name = 'comprehend', region_name = 'eu-west-1')

# get all the urls of links that are already stored in dynamodb
def get_links():
	a	= []
	c	= ddb.scan()

	for x in c['Items']:
		if x['link'] not in a:
			try:
				a.append(str(x['link']))
			except Exception as e:
				print(' --- failed to add '+str(x)+' '+str(e))

	while 'LastEvaluatedKey' in c:
		c 	= ddb.scan(ExclusiveStartKey = c['LastEvaluatedKey'])

		for x in c['Items']:
			if x['link'] not in a:
				try:
					a.append(str(x['link']))
				except:
					print('failed to add '+str(x))

	return a

# create a queue
q1     	= queue.Queue()

# worker for queue jobs
def worker():
    while not q1.empty():
        get_feed(q1.get())
        q1.task_done()

# get the RSS feed through feedparser
def get_rss(url):
	x 	= feedparser.parse(url)
	return x

# get the timestamp of the latest blogpost stored in DynamoDB
def ts_dynamo(source):
	r		= ddb.query(KeyConditionExpression=Key('source').eq(source))
	ts 		= ['0']

	for y in r['Items']:
		ts.append(y['timest'])

	return max(ts)

# write the blogpost record into DynamoDB
def put_dynamo(timest, title, desc, link, source, auth, tags):
	print('$$$', timest, title, desc, link, source, auth, tags)
	if len(desc) == 0:
		desc = '...'
	
	ddb.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest'	: timest,
			'title'		: title,
			'desc'		: desc,
			'link'		: link,
			'source'	: source,
			'author'	: auth,
			'tag'		: tags,
			'lower-tag'	: tags.lower(),
			'allts'		: 'y'
		})

# send an email out whenever a new blogpost was found
def send_mail(msg, subj, dest):
    r	= ses.send_email(
        Source		= os.environ['fromemail'],
        Destination = {'ToAddresses': [os.environ['toemail']]},
        Message 	= {
            'Subject': {
                'Data': subj
            },
            'Body': {
                'Html': {
                    'Data': msg
                }
            }
        }
)

# retrieve the url of a blogpost
def retrieve(url):
	r	= requests.get(url)
	s	= BeautifulSoup(r.text)

	try:					
		t	= s.find("div",  attrs = {"id" : "aws-page-content"}).getText(separator=' ')[:4800]

	except Exception as e:
		t	= '.'

	return t

# read the url's from 'feeds.txt'
def read_feed():
	r 	= {}
	f 	= 'feeds.txt'
	c   = 0

	# open the feeds file and read line by line
	with open(f) as fp:
		line = fp.readline()
		while line:

			# get the src and url value delimited by a ','
			src, url 		= line.split(',')

			# add src and url to dict
			r[src.strip()] 	= url.strip()
			line 			= fp.readline()

			# add one to the count
			c 				+= 1

	# return the dict and count value
	return r, c

# analyze the text of a blogpost using the AWS Comprehend service
def comprehend(txt, title):
	c 	= []
	f	= False

	for x in comp.detect_entities(Text = txt, LanguageCode = 'en')['Entities']:
		if x['Type'] == 'ORGANIZATION' or x['Type'] == 'TITLE':
			if x['Text'] not in c and x['Text'] != 'AWS' and x['Text'] != 'Amazon' and x['Text'] != 'aws':
				c.append(x['Text'])
				f	= True

	if f:
		tags 	= ', '.join(c)
		print(title, '\n', tags, '\n')
		
	else:
		tags	= 'aws'
	
	return tags

# main function to kick off collection of an rss feed
def get_feed(f):
	url 	= f[0]
	source	= f[1]
	stamps	= []

	# retrieve the rss feed
	d		= get_rss(url)

	maxts	= ts_dynamo(source)
	t 		= ddb.get_item(Key = {'timest': maxts, 'source': source})

	# check if previous blog post records were already present in DynamoDB
	try:
		print(' +++ last blogpost in '+source+' has title '+str(t['Item']['title'])+'\n')
	except:
		print(' +++ no blog records found for '+url)

	# get the articles meta data 
	for x in d['entries']:
		timest 		= str(int(time.mktime(x['published_parsed'])))
		c			= int(stamps.count(timest))

		date		= str(x['published_parsed'])
		title		= str(x['title'])
		link		= str(x['link'])

		des			= str(x['description'])
		r 			= re.compile(r'<[^>]+>')
		desc 		= r.sub('', str(des)).strip('&nbsp;')
		auth		= str(x['author'])

		if c != 1:
			timest	= int(timest) + c

		if link.strip() not in links:
			stamps.append(timest)

			# retrieve the articles html page
			txt 	= retrieve(link)

			# use amazon comprehend to detect tags in the description text
			tags	= comprehend(txt, title)
			put_dynamo(str(timest), title, desc, link, source, auth, tags)
			
			# send out an email if the option is set to 'yes' in the sam template.
			if os.environ['sendemails'].lower() == 'y':
				msg		= str('<html><body><h2>'+title+'</h2><br>'+desc+'<br><br><a href="'+link+'">view post here</a></body></html>')
				send_mail(msg, source.upper()+' - '+title, os.environ['toemail'])
				print('sending message for article '+title)

# the lambda handler
def handler(event, context): 
	global links
	links		= get_links()
	feeds, thr 	= read_feed()

	for source, url in feeds.items():
		q1.put([url, source])

	for x in range(50):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()
