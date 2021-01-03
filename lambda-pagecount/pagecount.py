import boto3, os
from boto3.dynamodb.conditions import Key, Attr

ddb = boto3.resource('dynamodb', region_name = os.environ['dynamo_region']).Table(os.environ['dynamo_table'])

blogs = ["apn", "architecture", "big-data", "biz-prod", "cli", "cloudguru", "compute", "contact-center", "containers", "corey", "cost-mgmt", "database", "desktop", "developer", "devops", "enterprise-strat", "gamedev", "gametech", "governance", "industries", "infrastructure", "iot", "java", "jeremy", "management-tools", "marketplace", "media", "messaging", "ml", "mobile", "modernizing", "networking", "newsblog", "open-source", "public-sector", "robotics", "sap", "security", "security-bulletins", "serverless", "storage", "training", "werner", "whats-new", "yan", "all"]

# get blogsource item count per category
def getblog_count(blogsource):

    count = 0

    if blogsource == 'all':

        # get all blogs with timestamp greater than 1
        print('getting all blogposts using visible index')
        blogs = ddb.query(IndexName = "visible", Select = 'COUNT', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(1))
        
        count += int(blogs['Count'])

        while 'LastEvaluatedKey' in blogs:
            
            blogs = ddb.query(ExclusiveStartKey = blogs['LastEvaluatedKey'], IndexName = "visible", Select = 'COUNT', KeyConditionExpression = Key('visible').eq('y') & Key('timest').gt(1))

            count += int(blogs['Count'])

    else:

        # get a count of blogpost per category
        print('getting ' + blogsource + ' posts using timest index')
        blogs = ddb.query(IndexName = "timest", Select = 'COUNT', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(1))

        count += int(blogs['Count'])

        while 'LastEvaluatedKey' in blogs:
            blogs = ddb.query(ExclusiveStartKey = blogs['LastEvaluatedKey'], IndexName = "timest", Select = 'COUNT', KeyConditionExpression = Key('blogsource').eq(blogsource) & Key('timest').gt(1))
            
            count += int(blogs['Count'])

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
    print('updated ' + str(count) + ' page count for ' + blogsource)

def handler(event, context):
    for blog in blogs:
        getblog_count(blog)
