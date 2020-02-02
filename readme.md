rss-lambda
==========

Monitor the AWS blog through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out through email using SES. 


Installation
------------

- Make sure the AWS SAM CLI and pip3 are installed on your local machine.
- Change the S3 bucket name where the Lambda code artifact will be uploaded. The bucket needs to be in the same AWS region as the stack. 
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'.
- Run 'bash deploy.sh' to deploy the stack. 


About the repo contents
-----------------------

The following description describes briefly what the files and folder contains;

- The *deploy.sh* file can be used to deploy the stack to AWS. It will download all of the Lambda dependancies, pack them and upload them to S3 and deploy a CloudFormation stack using SAM. 
- The *template.yaml* file is the SAM CloudFormation stack for the deployment. You do not need to edit this file directly unless you want to change some of the default values of the stack. 
- The *lambda* folder contains the source code and Lambda library artifacts that will be downloaded. You will also find the Python requirements file and *feeds.txt* file here that contain the RSS feeds which should be checked.
- The *vacuum* folder contains an extra Lambda program that can "vacuum" the status of blogposts in DynamoDB, in order to show only the most recent and timely blogposts per category. Since some feeds can post out a lot more messages than others, the amount of high volume blogs will be somewhat limited, which saves on DynamoDB read capacity per website view. You do not need to use this folder or its code altogether in order to use the RSS Lambda function, its purely extra functionality used by the author of the code. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
