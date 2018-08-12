#!/usr/bin/python
# @marekq
# www.marek.rocks

import boto3, feedparser, re, os, Queue, threading, time
from botocore.vendored import requests
from boto3.dynamodb.conditions import Key, Attr
from bs4 import *

# establish a session with DynamoDB
def dynamo_sess():
	return boto3.resource('dynamodb', region_name = os.environ['dynamo_region']).Table(os.environ['dynamo_table'])

s		= dynamo_sess()
q1     	= Queue.Queue()

feeds	= {
	"public-sector"			: "https://aws.amazon.com/blogs/publicsector/feed/",
	"gamedev"				: "https://aws.amazon.com/blogs/gamedev/feed/",
	"ml"					: "https://aws.amazon.com/blogs/machine-learning/feed/",
	"cli"					: "https://aws.amazon.com/blogs/developer/category/programing-language/aws-cli/feed/",
	"whats-new" 			: "https://aws.amazon.com/new/feed/", 
	"newsblog"				: "https://aws.amazon.com/blogs/aws/feed/",
	"serverless" 			: "https://aws.amazon.com/blogs/aws/category/serverless/feed/",
	"devops"				: "https://aws.amazon.com/application-management/blog/feed/",
	"big-data"				: "https://aws.amazon.com/blogs/big-data/feed/",
	"security"				: "https://aws.amazon.com/blogs/security/feed/",
	"java"					: "https://aws.amazon.com/blogs/developer/category/programing-language/java/feed/",
	"mobile"				: "https://aws.amazon.com/blogs/mobile/feed/",
	"architecture"			: "https://aws.amazon.com/blogs/architecture/feed/",
	"compute"				: "https://aws.amazon.com/blogs/compute/feed/",
	"database"				: "https://aws.amazon.com/blogs/database/feed/",
	"management-tools"		: "https://aws.amazon.com/blogs/mt/feed/",
	"security-bulletins"	: "https://aws.amazon.com/security/security-bulletins/feed/"
}

# worker for queue jobs
def worker():
    while not q1.empty():
        get_feed(q1.get())
        q1.task_done()

# get the RSS feed through feedparser
def get_rss(url):
	return feedparser.parse(url)

# get the timestamp of the latest blogpost stored in DynamoDB
def ts_dynamo(s, source):
	r		= s.query(KeyConditionExpression=Key('source').eq(source))
	ts 		= ['0']

	for y in r['Items']:
		ts.append(y['timest'])

	return max(ts)

# write the blogpost record into DynamoDB
def put_dynamo(s, timest, title, desc, link, source, auth, tags):
	s.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest'	: timest,
			'title'		: title,
			'desc'		: desc,
			'link'		: link,
			'source'	: source,
			'author'	: auth,
			'tag'		: tags,
			'lower-tag'	: tags.lower()
		})

# send an email out whenever a new blogpost was found
def send_mail(msg, subj):
    c	= boto3.client('ses')
    r	= c.send_email(
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
		print('@@@ url', url, e)
		t	= '.'

	return t

# analyze the text of a blogpost using the AWS Comprehend service
def comprehend(txt, title):
	e 	= boto3.client(service_name = 'comprehend', region_name = 'eu-west-1')
	c 	= []
	f	= False

	for x in e.detect_entities(Text = txt, LanguageCode = 'en')['Entities']:
		if x['Type'] == 'ORGANIZATION' or x['Type'] == 'TITLE':
			if x['Text'] not in c and x['Text'] != 'AWS' and x['Text'] != 'Amazon' and x['Text'] != 'aws':
				c.append(x['Text'])
				f	= True

	if f:
		tags 	= ', '.join(c)
		print(title, '\n', tags, '\n')
		
	else:
		tags	= '.'
	
	print('***', tags)	
	return(tags)

# main function to kick off collection of an rss feed
def get_feed(f):
	url 	= f[0]
	source	= f[1]
	
	d		= get_rss(url)
	maxts	= ts_dynamo(s, source)
	t 		= s.get_item(Key = {'timest': maxts, 'source': source})

	print('last blogpost in '+source+' has title '+str(t['Item']['title'])+'\n')

	for x in d['entries']:
		timest 		= str(int(time.mktime(x['published_parsed'])))
		date		= str(x['published_parsed'])
		title		= x['title'].encode('utf-8')
		link		= x['link'].encode('utf-8')

		des			= x['description'].encode('utf-8')
		r 			= re.compile(r'<[^>]+>')
		desc 		= r.sub('', des).strip('&nbsp;')
		auth		= x['author'].encode('utf-8')

		if int(timest) > int(maxts):
			print('retrieving '+link)

			txt 	= retrieve(link)
			tags	= comprehend(txt, title)
			put_dynamo(s, timest, title, desc, link, source, auth, tags)
			
			msg		= str('<html><body><h2>'+title+'</h2><br>'+desc+'<br><br><a href="'+link+'">view post here</a></body></html>')
			send_mail(msg, source.upper()+' - '+title)
			
			print('sending message for article '+title)

def lambda_handler(event, context): 
	for source, url in feeds.iteritems():
		q1.put([url, source])
		#get_feed([url, source])

	for x in range(5):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()
