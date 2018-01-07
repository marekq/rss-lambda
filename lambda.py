#!/usr/bin/python
# @marekq
# www.marek.rocks

feeds	= {
	'whats-new' 	: 'https://aws.amazon.com/new/feed/', 
	'newsblog'		: 'http://feeds.feedburner.com/AmazonWebServicesBlog',
	"devops"		: "http://blogs.aws.amazon.com/application-management/blog/feed/recentPosts.rss",
	"big-data"		: "http://blogs.aws.amazon.com/bigdata/blog/feed/recentPosts.rss",
	"security"		: "http://blogs.aws.amazon.com/security/blog/feed/recentPosts.rss",
	"java"			: "http://java.awsblog.com/blog/feed/recentPosts.rss",
	"mobile"		: "http://mobile.awsblog.com/blog/feed/recentPosts.rss",
	"architecture"	: "http://www.awsarchitectureblog.com/atom.xml",
	"compute"		: "https://aws.amazon.com/blogs/compute/feed/",
	"database"		: "https://aws.amazon.com/blogs/database/feed/",
	"management-tools" : "https://aws.amazon.com/blogs/mt/feed/",
	"security-bulletins" : "https://aws.amazon.com/security/security-bulletins/feed/"
}


import boto3, feedparser, re, os, time
from boto3.dynamodb.conditions import Key, Attr

def get_rss(url):
	return feedparser.parse(url)

def dynamo_sess():
	return boto3.resource('dynamodb', region_name = os.environ['dynamo_region']).Table(os.environ['dynamo_table'])

def ts_dynamo(s, source):
	r	= s.query(KeyConditionExpression=Key('source').eq(source))
	ts 	= []
	f	= False
	
	for y in r['Items']:
		ts.append(y['timest'])
		f	= True
	
	if f:
		return max(ts)
	else:
		return '0'

def put_dynamo(s, timest, title, desc, cat, link, source):
	s.put_item(TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : timest,
			'title' : title,
			'desc' : desc,
			'category' : cat,
			'link' : link,
			'source' : source
		})

def send_mail(msg, subj):
    c	= boto3.client('ses')
    r	= c.send_email(
        Source	    = os.environ['fromemail'],
        Destination = {'ToAddresses': [os.environ['toemail']]},
        Message     = {
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
	s	= dynamo_sess()

	for source, url in feeds.iteritems():
		get_feed(url, source, s)
		print 'getting '+source
		
def get_feed(url, source, s):
	d	= get_rss(url)
	
	maxts	= ts_dynamo(s, source)
	t 	= s.get_item(Key = {'timest': maxts, 'source': source})

	print 'last blogpost in dynamodb has title '+str(t['Item']['title'])+'\n'

	for x in d['entries']:
		timest 		= str(int(time.mktime(x['published_parsed'])))
		date		= str(x['published_parsed'])
		title		= x['title'].encode('utf-8')
		link		= x['link'].encode('utf-8')
		
		des		= x['description'].encode('utf-8')
		r 		= re.compile(r'<[^>]+>')
		desc 		= r.sub('', des).strip('&nbsp;')
		
		try:
			cat 	= x['category'].encode('utf-8')
		except:
			cat 	= ' '

		if int(timest) > int(maxts):
			put_dynamo(s, timest, title, desc, cat, link, source)
			msg		= str('<html><body><h2>'+title+'</h2><br>'+desc+'<br><br><a href="'+link+'">view post here</a><br><br>'+cat+'</body></html>')
			send_mail(msg, title)
			print 'sending message for article '+title
		else:
			print 'skip sending article '+title
