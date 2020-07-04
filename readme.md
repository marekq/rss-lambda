rss-lambda
==========

Monitor the AWS blog through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out through email using SES. 


Installation
------------

- Make sure the AWS SAM CLI and Docker are installed on your local machine.
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'. These contain various AWS blogs by default.
- Run 'bash deploy.sh' to deploy the stack. If the 'samconfig.toml' file is not present, you will have to enter the stack details manually. 


About the repo contents
-----------------------

The following description describes briefly what the files and folder contains;

- The *deploy.sh* file can be used to deploy the stack to AWS. It will download all of the Lambda dependancies, pack them and upload them to S3 and deploy a CloudFormation stack using SAM. 
- The *template.yaml* file is the SAM CloudFormation stack for the deployment. You do not need to edit this file directly unless you want to change some of the default values of the stack. 
- The *lambda-dynamo* folder contains the source code for the RSS retrieval Lambda. You will also find the Python requirements file and *feeds.txt* file here that contain the RSS feeds which should be checked.
- The *lambda-email* folder contains the source code for the Lambda that sends an email when new articles are retrieved. It is invoked asynchronously through Lambda Destinations from the lambda-dynamo function. 
- The *vacuum* folder contains an extra Lambda program that can "vacuum" the status of blogposts in DynamoDB, in order to show only the most recent and timely blogposts per category. Since some feeds can post out a lot more messages than others, the amount of high volume blogs will be somewhat limited, which saves on DynamoDB read capacity per website view. You do not need to use this folder or its code altogether in order to use the RSS Lambda function, its purely extra functionality used by the author of the code. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
