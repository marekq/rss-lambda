#!/usr/bin/python
# @marekq
# www.marek.rocks

import base64, botocore, boto3, csv, fake_useragent, feedparser
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
	queryres = ddb.query(IndexName = 'timest', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

	for x in queryres['Items']:
		if 'guid' in x:
			if x['guid'] not in guids:
				guids.append(x['guid'])

	# paginate the query in case more than 100 results are returned
	while 'LastEvaluatedKey' in queryres:
		queryres = ddb.query(ExclusiveStartKey = queryres['LastEvaluatedKey'], IndexName = 'timest', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(str(ts)))

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


# get the timestamp of the latest blogpost stored in DynamoDB
def ts_dynamo(source):

	# get timestamp from 30 days ago
	now_ts = datetime.now()
	old_ts = now_ts - timedelta(days = 30)
	diff_ts = int(time.mktime(old_ts.timetuple()))

	results	= ddb.query(KeyConditionExpression = Key('source').eq(source) & Key('timest').gt(str(diff_ts)))
	timest 	= ['0']

	for x in results['Items']:
		timest.append(x['timest'])

	# return max timestamp and count of found items
	return max(timest), len(timest) - 1


# write the blogpost record into DynamoDB
@xray_recorder.capture("put_dynamo")
def put_dynamo(timest_post, title, cleantxt, rawhtml, desc, link, source, author, guid, tags, category, datestr_post):

	# if no description was submitted, put a dummy value to prevent issues parsing the output
	if len(desc) == 0:
		desc = '...'
	
	# put the record into dynamodb
	ddb.put_item(
		TableName = os.environ['dynamo_table'], 
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
def send_mail(recpt, title, source, author, rawhtml, link, datestr_post):

	# create a simple html body for the email
	mailmsg = '<html><body><br><i>Posted by '+str(author)+' in ' +str(source) + ' blog on ' + str(datestr_post) + '</i><br><br>'
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
			if source not in blog_queue:
				blog_queue.append(source)

			# retrieve other blog post values
			link = str(x['link'])
			title = str(x['title'])

			# retrieve the blogpost author if available
			author = 'blank'

			if x.has_key('author'):
				author = str(x['author'])
			
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
			
			# write the record to dynamodb, only if the guid is not present already. this prevents double posts from appearing by crossposting. 
			if guid not in guids:
				put_dynamo(str(timest_post), title, cleantxt, rawhtml, desc, link, source, author, guid, tags, category, datestr_post)

				# if sendemails enabled, generate the email message body for ses and send email
				if os.environ['sendemails'] == 'y':

					# get mail title and email recepient
					mailt = source.upper()+' - '+title
					recpt = os.environ['toemail']

					# send the email
					send_mail(recpt, title, source, author, rawhtml, link, datestr_post)

			else:
				
				# skip, print message that a guid was found twice
				print("skipped double guid " + guid + " " + source + " " + title + " " + datestr_post)


# get the contents of the dynamodb table for json object on S3
@xray_recorder.capture("get_table")
def get_table(source):
	res = []
	now_ts = datetime.now()

	# get timestamp from 30 days ago
	old_ts = now_ts - timedelta(days = 30)
	diff_ts = int(time.mktime(old_ts.timetuple()))

	# query the dynamodb table for recent blogposts
	blogs = ddb.query(KeyConditionExpression = Key('source').eq(source) & Key('timest').gt(str(diff_ts)))
		
	# iterate over the returned items
	for a in blogs['Items']:
		b = '{"timest": "' + a['timest'] + '", "source": "' + a['source'] + '", "title": "' + a['title'] + '", "author": "' 
		b += a['author'] + '", "link": "' + a['link'] + '", "desc": "' + str(a['desc']).strip() + '", "author": "'+ a['author'] +'"}'
		res.append(b)
	
		# retrieve additional items if lastevaluatedkey was found 
		while 'LastEvaluatedKey' in blogs:
			lastkey = blogs['LastEvaluatedKey']
			blogs = ddb.query(ExclusiveStartKey = lastkey, KeyConditionExpression = Key('source').eq(source) & Key('timest').gt(str(diff_ts)))
			
			for a in blogs['Items']:
				b = '{"timest": "' + a['timest'] + '", "source": "' + a['source'] + '", "title": "' + a['title'] + '", "author": "' 
				b += a['author'] + '", "link": "' + a['link'] + '", "desc": "' + str(a['desc']).strip() + '", "author": "'+ a['author'] +'"}'
				res.append(b)

	# sort the json file by timestamp in reverse
	out = sorted(res, reverse = True)

	return out


# copy the file to s3 with a public acl
@xray_recorder.capture("cp_s3")
def cp_s3(source):

	s3.put_object(
		Bucket = os.environ['s3bucket'], 
		Body = open('/tmp/' + source + '.json', 'rb'), 
		Key = source + '.json', 
		ACL = 'public-read',
		CacheControl = 'public, max-age=3600'
	)


# update json objects on S3 for single page web apps
@xray_recorder.capture("update_json_s3")
def update_json_s3(blog_queue):

	# update the json per blog
	for blog in blog_queue:

		# get the json content from DynamoDB
		out = get_table(blog)

		# create the json and return path
		fpath = make_json(out, blog)

		# upload the json to s3
		cp_s3(blog)

		print('updated '+ blog + ' blog')


# create a json file
@xray_recorder.capture("make_json")
def make_json(content, source):

	fpath = '/tmp/' + source + '.json'

	# write the json raw output to /tmp/
	jsonf = open(fpath, 'w')
	jsonf.write('{"content":')
	jsonf.write(str(content).replace("'", "").replace("\\", ""))
	jsonf.write('}')

	# write the json gz output to /tmp
	gzipf = gzip.GzipFile(fpath + '.gz', 'w')
	gzipf.write('{"content":'.encode('utf-8') )
	gzipf.write(str(content).replace("'", "").replace("\\", "").encode('utf-8') )
	gzipf.write('}'.encode('utf-8') )
	gzipf.close()

	print('wrote to ' + fpath)


# lambda handler
@xray_recorder.capture("handler")
def handler(event, context): 
	
	# get the unix timestamp from 3 days ago from now
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
	for source, url in feeds.items():
		q1.put([url, source])

	# start thread per feed
	for x in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()

	# update the json files on s3 for updated sources
	if os.environ['storepublics3'] == 'y':
		update_json_s3(blog_queue)
