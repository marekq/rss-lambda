rss-lambda
==========

Monitor the AWS blog through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out through email using SES. 


Installation
------------

- Make sure the AWS SAM CLI and pip3 are installed on your local machine.
- Change the S3 bucket name where the Lambda code artifact will be uploaded. The bucket needs to be in the same AWS region as the stack. 
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'.
- Run 'bash deploy.sh' to deploy the stack. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
