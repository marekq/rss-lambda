#!/usr/bin/python
# @marekq
# www.marek.rocks

import boto3, feedparser, re, os, time

def get_feed():
	u 	= 'https://aws.amazon.com/new/feed/'
	return feedparser.parse(u)

def dynamo_sess():
	return boto3.resource('dynamodb', region_name = os.environ['dynamo_region']).Table(os.environ['dynamo_table'])

def ts_dynamo(s):
	r 	= s.scan()
	ts 	= []
	for y in r['Items']:
		ts.append(y['timest'])
	return max(ts)

def put_dynamo(s, timest, title, desc, cat, link):
	s.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest,
			'title' : title,
			'desc' : desc,
			'category' : cat,
			'link' : link
		})

def send_mail(msg, subj):
    c	= boto3.client('ses')
    r	= c.send_email(
        Source		= os.environ['from-email'],
        Destination = {'ToAddresses': [os.environ['to-email']]},
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

def lambda_handler(event, context): 
	d		= get_feed()
	s		= dynamo_sess()

	maxts	= ts_dynamo(s)
	t 		= s.get_item(Key = {'timest': maxts})

	print 'last blogpost in dynamodb has title '+str(t['Item']['title'])+'\n'

	for x in d['entries']:
		timest 		= str(int(time.mktime(x['published_parsed'])))
		date		= str(x['published_parsed'])
		title		= x['title'].encode('utf-8')
		link		= x['link'].encode('utf-8')
		
		des			= x['description'].encode('utf-8')
		r 			= re.compile(r'<[^>]+>')
		desc 		= r.sub('', des).strip('&nbsp;')
		
		try:
			cat 	= x['category'].encode('utf-8')
		except:
			cat 	= ' '

		if int(timest) > int(maxts):
			put_dynamo(s, timest, title, desc, cat, link)
			msg		= str('<html><body><h2>'+title+'</h2><br>'+desc+'<br><br><a href="'+link+'">view post here</a><br><br>'+cat+'</body></html>')
			send_mail(msg, title)
			print 'sending message for article '+title
		else:
			print 'skip sending article '+title