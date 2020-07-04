import boto3, json, os

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()

c	= boto3.client('ses')

# send an email out whenever a new blogpost was found
@xray_recorder.capture("send_mail")
def send_mail(msg, subj, dest):
    r	= c.send_email(
        Source		= os.environ['fromemail'],
        Destination = {'ToAddresses': [dest]},
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
    
    print('sent email with subject '+subj)

@xray_recorder.capture("lambda_handler")
def handler(event, context):
    print(event)
    x = event['responsePayload']
    z = json.loads(x)
    
    print(type(z))
    print(z)

    if len(z) != 0:
        send_mail(z['msg'], z['title'], z['recpt'])

    return z
    
