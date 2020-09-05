rss-lambda
==========

Monitor your favourite blogs through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out to your e-mail address using SES. The Lambda function to retrieve the blogs runs every 10 minutes by default. The cost for running the solution should be less than $3 per month, which is mostly influenced by the polling frequency of the function. 

You can extend the blog scraper by adding your own RSS feeds to monitor. By default various AWS related feeds are included, but you can add any of your own feeds in the *lambda-dynamo/feeds.txt* file. Within the DynamoDB table that is deployed, you can find various details about the blogposts and also the text or html versions of the content. This can be helpful in case you are building your own feed scraper or notification service. 

Optionally, a JSON output for every blog category can be uploaded as a public S3 object. These files can be included in a single page app, such as the one at https://blog.marek.rocks . The output will be compressed using 'brotli' or something similar later in the future to save on S3 storage and bandwidth costs. 

Finally, the feed retrieval feature uses a "readability" library which works similarly to the "Reader View" function of the Apple Safari browser. This makes it convenient to read the full text of a blogpost in your email client or on mobile. All of the links, images and text markup is preserved. 


![alt text](./docs/architecture.png)


The following fields are stored in DynamoDB per blog article. In the screenshot, the large HTML and text outputs were omitted;


![alt text](./docs/dynamodb.png)


Installation
------------

- Make sure the AWS SAM CLI and Docker are installed and configured on your local machine.
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'. These contain various AWS blogs I read by default.
- Run 'bash deploy.sh' to deploy the stack. If the 'samconfig.toml' file is not present, you will have to enter the stack details manually. 
- If you optionally select to use email notifications using SES, you will need to ensure that you have the SES sender and email address preconfigured in your account. There is unfortunately no simple way to provision this using SAM. 


Note: the 'rssdynamo' function is configured with 1GB of memory and a timeout of 30 seconds. On the initial invocation, the function will need to retrieve records from the last 72 hours and may run for up to 20-30 seconds. After the table is populated, the timeout setting can be drastically reduced, which is a best practice to prevent any unneccesary costs. 


If time allows, this will be replaced with an Express Step Function in the future, which will allow for lower default values and better visibility over the process. A Lambda concurrency limit of 2 is set for the Lambda to prevent a high amount of parallel invocations running.


Roadmap
-------

- [ ] Decompose the "monolith" Lambda function into smaller functions. This will allow for easier retries and debugging of blogpost retrieval. 
- [ ] Optimize Lambda memory and timeout settings to lower cost. 
- [X] Add "smart" extraction of the full blogpost text from the post URL, so that the full content of a post can be stored in DynamoDB or sent through e-mail.
- [X] Add generation of JSON files with blogposts to S3 for easier inclusion in a single page app (i.e. deployed using Amplify or Netlify).
- [X] Add support for retrieval of non AWS blogposts using RSS.


About the repo contents
-----------------------

The following description describes briefly what the files and folder contains;

- The *deploy.sh* file can be used to deploy the stack to AWS. It will download all of the Lambda dependancies, pack them and upload them to S3 and deploy a CloudFormation stack using SAM. 
- The *template.yaml* file is the SAM CloudFormation stack for the deployment. You do not need to edit this file directly unless you want to change some of the default values of the stack. 
- The *lambda-dynamo* folder contains the source code for the RSS retrieval Lambda. The function also optionally sends an email through SES when new articles are retrieved. You will also find the *feeds.txt* file here that contain the RSS feeds which should be checked.
- The *lambda-layer* folder contains the *requirements.txt* file for the Lambda layer of the blog retrieval function. 


License
-------

MIT-0, please see the 'LICENSE' file for more info. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
