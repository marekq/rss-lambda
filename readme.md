rss-lambda
==========

Monitor 40 different AWS blogs through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out through email using SES. The Lambda function to retrieve the blogs runs every 10 minutes by default. 


![alt text](./docs/architecture.png)



Installation
------------

- Make sure the AWS SAM CLI and Docker are installed and configured on your local machine.
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'. These contain 40 AWS blogs by default.
- Run 'bash deploy.sh' to deploy the stack. If the 'samconfig.toml' file is not present, you will have to enter the stack details manually. 
- If you optionally select to use email notifications using SES, you will need to ensure that you have the SES sender and email address preconfigured in your account. There is unfortunately no simple way to provision this using SAM. 


Note: the 'rssdynamo' function is configured with 3GB of memory and a timeout of 60 seconds. On the initial invocation, the function will need to retrieve a few hundred records and may run for a minute or longer. After the table is populated, the memory and timeout settings can be drastically reduced, which is a best practice to prevent any unneccesary costs. If time allows, this will be replaced with an Express Step Function in the future, which will allow for lower default values. 


About the repo contents
-----------------------

The following description describes briefly what the files and folder contains;

- The *deploy.sh* file can be used to deploy the stack to AWS. It will download all of the Lambda dependancies, pack them and upload them to S3 and deploy a CloudFormation stack using SAM. 
- The *template.yaml* file is the SAM CloudFormation stack for the deployment. You do not need to edit this file directly unless you want to change some of the default values of the stack. 
- The *lambda-dynamo* folder contains the source code for the RSS retrieval Lambda. The function also optionally sends an email through SES when new articles are retrieved. You will also find the *feeds.txt* file here that contain the RSS feeds which should be checked.
- The *lambda-layer* folder contains the *requirements.txt* file for the Lambda layer of the blog retrieval function. 
- (OPTIONAL) The *vacuum* folder contains an extra Lambda program that can "vacuum" the status of blogposts in DynamoDB, in order to show only the most recent and timely blogposts per category. Since some feeds can post out a lot more messages than others, the amount of high volume blogs will be somewhat limited, which saves on DynamoDB read capacity per website view. You do not need to use this folder or its code altogether in order to use the RSS Lambda function, its purely extra functionality used by the author of the code. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
