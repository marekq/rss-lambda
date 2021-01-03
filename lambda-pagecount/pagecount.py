import botocore, boto3, os
from boto3.dynamodb.conditions import Key, Attr

ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region'], config = botocore.client.Config(max_pool_connections = 50)).Table(os.environ['dynamo_table'])

blogs = ["apn", "architecture", "big-data", "biz-prod", "cli", "cloudguru", "compute", "contact-center", "containers", "corey", "cost-mgmt", "database", "desktop", "developer", "devops", "enterprise-strat", "gamedev", "gametech", "governance", "industries", "infrastructure", "iot", "java", "jeremy", "management-tools", "marketplace", "media", "messaging", "ml", "mobile", "modernizing", "networking", "newsblog", "open-source", "public-sector", "robotics", "sap", "security", "security-bulletins", "serverless", "storage", "training", "werner", "whats-new", "yan"]

# get all blogsource items per category
def getblog_count(blogsource):

    # get a count of blogpost per category
    blogs = ddb.query(IndexName = "timest", ScanIndexForward = True, Select = 'COUNT', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(1))

    # get retrieved blog count
    count = blogs['Count']
    print(blogs)

    # write the page count record to dynamodb
    ddb.put_item(
		TableName = os.environ['dynamo_table'], 
		Item = {
			'timest' : 0,
            'guid': blogsource,
			'blogsource' : blogsource,
            'articlecount' : int(count),
            'visible': 'y'
		}
	)

    # print status
    print('updated ' + str(count) + ' pagecount for ' + blogsource)

def handler(event, context):
    for blog in blogs:
        getblog_count(blog)
