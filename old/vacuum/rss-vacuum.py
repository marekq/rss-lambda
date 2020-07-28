#!/usr/bin/python
# marek kuczynski
# @marekq
# www.marek.rocks
# coding: utf-8

import boto3, datetime, time
from boto3.dynamodb.conditions import Key, Attr

d       = boto3.resource('dynamodb').Table('rss-aws')
blogs   = ['whats-new', 'newsblog', 'devops', 'big-data', 'security', 'java', 'mobile', 'architecture', 'compute', 'database', 'management-tools', 'security-bulletins', 'public-sector', 'gamedev', 'ml', 'cli', 'serverless']

def write_ddb(timest, source, attr):
    r   = d.update_item(Key = {'timest': timest, 'source': source},
        UpdateExpression = 'set allts = :a',
        ExpressionAttributeValues = {
            ':a': attr
        }
    )

    print('wrote '+attr+' '+source+' '+timest)

def get_source(src):
    a       = []
    e	    = d.query(KeyConditionExpression = Key('source').eq(src))

    for x in e['Items']:
        a.append(x['timest'])

        if x.has_key('allts'):
            if x['allts'] == 'y':
                yes.append(x['timest'])

            elif x['allts'] == 'n':
                no.append(x['timest'])

        else:
            no.append(x['timest'])

    while 'LastEvaluatedKey' in e:
        e   = d.query(ExclusiveStartKey = e['LastEvaluatedKey'], KeyConditionExpression = Key('source').eq(src))

        for x in e['Items']:
            if x['timest'] not in a:
                a.append(x['timest'])

            if x.has_key('allts'):
                if x['allts'] == 'y':
                    if x['timest'] not in yes:
                        yes.append(x['timest'])

                elif x['allts'] == 'n':
                    if x['timest'] not in no:
                        no.append(x['timest'])
                        
            else:
                no.append(x['timest'])

    top10   = sorted(a, reverse=True)[:50]
    rest    = sorted(a, reverse=True)[50:]

    for x in top10:
        if x not in yes:
            write_ddb(x, src, 'y')

    for x in rest:
        if x not in no:
            write_ddb(x, src, 'n')

def handler(event, context):
    yes = [] 
    no  = []
    global yes, no
    
    for x in blogs:
        get_source(x)

    print(str(len(yes))+' total records marked as recent')
    print(str(d.query(IndexName = 'allts', KeyConditionExpression = Key('allts').eq('y'))['Count'])+' total records returned in single query')
