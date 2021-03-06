#!/usr/bin/python
# @marekq
# www.marek.rocks

import base64, botocore, boto3, csv, feedparser
import gzip, json, os, re, readability, requests
import queue, sys, threading, time

from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.conditions import Key, Attr
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

logger = Logger()
modules_to_be_patched = ["botocore", "boto3", "requests"]
tracer = Tracer(patch_modules = modules_to_be_patched)

# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
s3 = boto3.client('s3')


# create a queue for multiprocessing
q1 = queue.Queue()


# get the blogpost guids that are already stored in DynamoDB table
@tracer.capture_method(capture_response = False)
def get_guids(ts):
	guids = []

	# get the guid values up to x days ago
	queryres = ddb.query(ScanIndexForward = True, IndexName = 'visible', ProjectionExpression = 'guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(ts))

	for x in queryres['Items']:
		if 'guid' in x:
			if x['guid'] not in guids:
				guids.append(x['guid'])

	# paginate the query in case more than 100 results are returned
	while 'LastEvaluatedKey' in queryres:
		queryres = ddb.query(ExclusiveStartKey = queryres['LastEvaluatedKey'], ScanIndexForward = True, IndexName = 'visible', ProjectionExpression = 'guid', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(ts))

		for x in queryres['Items']:
			if 'guid' in x:
				if x['guid'] not in guids:
					guids.append(x['guid'])

	print('guids found in last day : '+str(len(guids)))
	return guids


# read the url's from 'feeds.txt' stored in the lambda function
@tracer.capture_method(capture_response = False)
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

# check if the s3 object exists by listing current s3 objects
def get_s3_files():
	s3list = s3.list_objects(Bucket = os.environ['s3_bucket'])

	return s3list


# get the contents of the dynamodb table for json object on S3
@tracer.capture_method(capture_response = False)
def get_feed(x):
	url = x[0]
	blogsource = x[1]

	# if the blog json is available on s3
	if str(blogsource + '.json') in s3files:
		
		ts_old = int(time.time()) - (days_to_retrieve * 1)

	# if the blog json does not exist on s3
	else:
		
		# set the days to retrieve value based on the given setting
		ts_old = int(time.time()) - (days_to_retrieve * 86400)

	print(ts_old, url, blogsource)
	res.append({'ts': ts_old, 'url': url, 'blogsource': blogsource, 'daystoretrieve': days_to_retrieve})

# worker for queue jobs
@tracer.capture_method(capture_response = False)
def worker():
	while not q1.empty():
		get_feed(q1.get())
		q1.task_done()

# lambda handler
@logger.inject_lambda_context(log_event = True)
@tracer.capture_lambda_handler
def handler(event, context): 

	# set a default value of 1 for 'days_to_retrieve'
	global days_to_retrieve
	days_to_retrieve = int(1)

	send_mail = os.environ['send_mail']

	# check if days input value was given in step function
	try:
		if int(event['msg']['days']) < 90:
			days_to_retrieve = int(event['msg']['days'])
			print('setting days_to_retrieve value to ' + str(days_to_retrieve) + ' based on state machine input')

		else:
			print('failed to get valid days input value from step function, proceeding with default value of 1')

	except Exception as e:		
		print('failed to get valid days input value from step function, proceeding with default value of 1')
		print(e)

	# check if send email input value was given in step function
	try:
		if event['send_mail'] == 'y' or event['send_mail'] == 'yes':
			print('sending emails based on state machine input')
			send_mail = 'y'
			
	except Exception as e:		
		print('failed to get valid send email input value from step function, proceeding with default value of n')
		print(e)

	print('sending emails: ' + str(send_mail))

	# create global list for results
	global res
	res = []

	# get s3 result files
	global s3files
	s3files = get_s3_files()

	# get the unix timestamp from days_to_retrieve days ago
	ts_old = int(time.time()) - (86400 * days_to_retrieve)

	# get post guids stored in dynamodb for days_to_retrieve
	guids = get_guids(ts_old)

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

	# return results, guid and days to retrieve
	return {
		'results': res, 
		'guids': guids, 
		'daystoretrieve': str(days_to_retrieve),
		'send_mail': send_mail,
		'algolia_app': os.environ['algolia_app'],
		'algolia_apikey': os.environ['algolia_apikey'],
		'algolia_index': os.environ['algolia_index'],
		'dynamo_region': os.environ['dynamo_region'],
		'dynamo_table': os.environ['dynamo_table'],
		'from_email': os.environ['from_email'],
		'to_email': os.environ['to_email'],
		's3_bucket': os.environ['s3_bucket'],
		'storepublics3': os.environ['storepublics3'],
		'enable_algolia': os.environ['enable_algolia'],
		'send_mail': os.environ['send_mail']
	}
