#!/usr/bin/python
# @marekq
# www.marek.rocks

import botocore, boto3
import os, queue, threading, time

from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.conditions import Key

logger = Logger()
tracer = Tracer(patch_modules = ["boto3"])


# establish a session with SES, DynamoDB and Comprehend
ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])
s3 = boto3.client('s3')


# create a queue for multiprocessing
q1 = queue.Queue()


# get the blogpost guids that are already stored in DynamoDB table
@tracer.capture_method(capture_response = False)
def get_guids(ts):
	guids = set()
	queryres = ddb.query(ScanIndexForward = True, IndexName = 'visible', ProjectionExpression = 'guid',
		KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(ts))

	while True:
		for x in queryres['Items']:
			if 'guid' in x:
				guids.add(x['guid'])

		if 'LastEvaluatedKey' not in queryres:
			break

		queryres = ddb.query(ExclusiveStartKey = queryres['LastEvaluatedKey'], ScanIndexForward = True,
			IndexName = 'visible', ProjectionExpression = 'guid',
			KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(ts))

	print('guids found in last day : '+str(len(guids)))
	return list(guids)


# read the url's from 'feeds.txt' stored in the lambda function
@tracer.capture_method(capture_response = False)
def read_feed():
	result = {}
	with open('feeds.txt') as fp:
		for line in fp:
			src, url = line.split(',')
			result[src.strip()] = url.strip()

	count = min(len(result), 50)
	return result, count

# get the contents of the dynamodb table for json object on S3
@tracer.capture_method(capture_response = False)
def get_feed(x):
	url, blogsource = x
	ts_multiplier = 1 if blogsource + '.json' in s3files else 86400
	ts_old = int(time.time()) - (days_to_retrieve * ts_multiplier)

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
	global days_to_retrieve, send_email, res, s3files

	days_to_retrieve = 1
	send_email = os.environ['sendemails']

	try:
		days = int(event['msg']['days'])
		days_to_retrieve = days

	except (KeyError, ValueError) as e:
		print('failed to get valid days input value from step function, proceeding with default value of 1')

	try:
		if event.get('email') in ('y', 'yes'):
			send_email = 'y'
			print('sending emails based on state machine input')
	except Exception:
		print('failed to get valid send email input value from step function')

	print('sending emails: ' + str(send_email))

	res = []
	s3list = s3.list_objects(Bucket = os.environ['s3bucket'])
	s3files = s3list

	ts_old = int(time.time()) - (86400 * days_to_retrieve)
	guids = get_guids(ts_old)

	feeds, thr = read_feed()

	for blogsource, url in feeds.items():
		q1.put([url, blogsource])

	for _ in range(thr):
		t = threading.Thread(target = worker)
		t.daemon = True
		t.start()
	q1.join()

	return {
		'results': res,
		'guids': guids,
		'daystoretrieve': str(days_to_retrieve),
		'sendemail': send_email
	}
