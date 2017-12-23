rss-lambda
==========

Monitor the AWS blog through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and sent out through email using SES.

Installation
------------

- Add all source files in a zip file and upload the zip to a new Python 2.7 Lambda function. 
- Ensure your Lambda function can read/write from DynamoDB and can send emails through SES.
- Set a Lambda timeout of 10 seconds and 128 MB memory.
- Set the Lambda environment variables with the correct values.
- Run the Lambda function and check the DynamoDB table if 25 blogposts were retrieved. 
- Check your email address if you received a summary of the blogpost.

Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
