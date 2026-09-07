#!/usr/bin/python

import html
import os

import boto3


ses = boto3.client('ses')


def _value(detail, key, fallback='unknown'):
	value = detail.get(key)
	return str(value) if value not in (None, '') else fallback


def handler(event, context):
	detail = event.get('detail') or {}
	status = _value(detail, 'status')
	state_machine = _value(detail, 'stateMachineArn')
	execution_arn = _value(detail, 'executionArn')
	execution_name = _value(detail, 'name')
	start = _value(detail, 'startDate', _value(event, 'time'))
	stop = _value(detail, 'stopDate', _value(event, 'time'))
	error = _value(detail, 'error', 'none')
	cause = _value(detail, 'cause', 'none')
	# Keep alerts readable and bounded even when a service emits a long cause.
	cause = cause[:4000]

	subject = f'RSS workflow {status}: {execution_name}'
	body = (
		'The RSS Step Functions workflow did not complete successfully.\n\n'
		f'Status: {status}\n'
		f'State machine: {state_machine}\n'
		f'Execution: {execution_arn}\n'
		f'Execution name: {execution_name}\n'
		f'Started: {start}\n'
		f'Stopped: {stop}\n'
		f'Error: {error}\n'
		f'Cause: {cause}\n'
	)
	html_body = '<html><body><p>The RSS Step Functions workflow did not complete successfully.</p><ul>'
	for label, value in (
		('Status', status),
		('State machine', state_machine),
		('Execution', execution_arn),
		('Execution name', execution_name),
		('Started', start),
		('Stopped', stop),
		('Error', error),
		('Cause', cause),
	):
		html_body += f'<li><strong>{html.escape(label)}:</strong> {html.escape(value)}</li>'
	html_body += '</ul></body></html>'

	response = ses.send_email(
		Source=os.environ['fromemail'],
		Destination={'ToAddresses': [os.environ['toemail']]},
		Message={
			'Subject': {'Data': subject},
			'Body': {
				'Text': {'Data': body},
				'Html': {'Data': html_body},
			},
		},
	)
	print(f"sent workflow failure alert for {execution_arn}: {response.get('MessageId', 'unknown')}")
	return {'messageId': response.get('MessageId')}
