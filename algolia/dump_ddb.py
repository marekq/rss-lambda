# A simple script to dump the CSV results from the RSS DynamoDB table
# You can use the CSV dump to bulk import blogs into Algolia

import botocore, boto3

region = 'eu-west-1'
table = '<tablename>'
proj_expression = "guid, timest, datestr, blogsource, category, link, description, author, title"

ddb = boto3.resource('dynamodb', region_name = region, config = botocore.client.Config(max_pool_connections = 50)).Table(table)

def dump_records_to_csv():

	res = []
	queryres = ddb.scan(ProjectionExpression = proj_expression)

	for x in queryres['Items']:
					
		if x['timest'] != 0:
			y = [x['guid'], x['guid'], str(x['timest']), x['datestr'], x['blogsource'], x['category'], x['link'], x['description'], x['author'],x ['title']]
			res.append('"' + '","'.join(y) + '"')

	while 'LastEvaluatedKey' in queryres:

		queryres = ddb.scan(ExclusiveStartKey = queryres['LastEvaluatedKey'], ProjectionExpression = proj_expression)
		
		for x in queryres['Items']:
			
			if x['timest'] != 0:
				y = [x['guid'], x['guid'], str(x['timest']), x['datestr'], x['blogsource'], x['category'], x['link'], x['description'], x['author'],x ['title']]
				res.append('"' + '","'.join(y) + '"')
		
	z = open('out.csv', 'w')
	z.write('ObjectID,guid,timest,datestr,blogsource,category,link,description,author,title\n')
	for x in res:
		z.write(x+'\n')
	z.close()

dump_records_to_csv()
