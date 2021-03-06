rss-lambda
==========

Monitor your favourite blogs through RSS and get a notification whenever a new blog is posted. New blogposts are stored in DynamoDB and (optionally) sent out to your e-mail address using SES. The Step Function function to retrieve the blogs runs every 15 minutes by default. The cost for running the solution should be less than $3 per month, which is mostly influenced by the polling frequency of the function. 

You can extend the blog scraper by adding your own RSS feeds to monitor. By default various AWS related feeds are included, but you can add any of your own feeds in the *lambda-dynamo/feeds.txt* file. Within the DynamoDB table that is deployed, you can find various details about the blogposts and also the text or html versions of the content. This can be helpful in case you are building your own feed scraper or notification service. You can also use the included AppSync endpoint to read data from the table using GraphQL. 

Optionally, a JSON output for every blog category can be uploaded as a public S3 object. These files can be included in a single page app, such as the one at https://marek.rocks . The output will be compressed using 'brotli' or something similar later in the future to save on S3 storage and bandwidth costs. 

The feed retrieval feature uses a "readability" library which works similarly to the "Reader View" function of the Apple Safari browser. This makes it convenient to read the full text of a blogpost in your email client or on mobile. All of the links, images and text markup is preserved. 

Finally, an AppSync public endpoint can be deployed which retrieves the blogposts from DynamoDB. You can include the endpoint in a single page app to query blogpost context real time in a (web) application. 


![alt text](./docs/architecture.png)


The following fields are stored in DynamoDB per blog article. In the screenshot, the large HTML and text outputs were omitted;


![alt text](./docs/dynamodb.png)


Finally, the following State Machine is created to retrieve blog posts;


![alt text](./docs/statemachine.png)


Installation
------------

- Make sure the AWS SAM CLI and Docker are installed and configured on your local machine.
- If you want, you can edit the RSS feeds in 'lambda/feeds.txt'. These contain various AWS blogs I read by default.
- Run 'make init' to deploy the stack for the first time. Once the 'samconfig.toml' file is present, you can use 'make deploy'.
- If you optionally select to use email notifications using SES, you will need to ensure that you have the SES sender and email address preconfigured in your account. There is unfortunately no simple way to provision this using SAM. 

You can now run the Step Function to trigger the blog refresh. The URL to find the Step Function is given as an output value of the CloudFormation stack.


Roadmap
-------

- [ ] Switch to Step Functions Express to save on costs. The Express option can be used today, but is more difficult to debug in case of Lambda failures. 
- [X] Add AppSync endpoint for retrieval of blog posts through Amplify. 
- [X] Decompose the "monolith" Lambda function into smaller functions. This will allow for easier retries and debugging of blogpost retrieval. 
- [X] Implement Step Function for better coordination of individual functionality.
- [X] Add Lambda Extension to monitor network and CPU usage of the RSS function. 
- [X] Optimize Lambda memory and timeout settings to lower cost. 
- [X] Add "smart" text extraction of the full blogpost, so that the full content of a post can be stored in DynamoDB or sent through e-mail.
- [X] Add generation of JSON files with blogposts to S3 for easier inclusion in a single page app (as seen on https://marek.rocks ).
- [X] Add support for retrieval of non AWS blogposts using RSS.
- [X] Add DynamoDB Global Secondary Indexes for (partial) data retrieval based on GUID, timestamp and blog categories. 


About the repo contents
-----------------------

The following description describes briefly what the files and folder contains;

- Run *make init* to deploy the stack to AWS. It will download all of the Lambda dependancies, pack them and upload them to S3 and deploy a CloudFormation stack using SAM. After the initial run, you can use *make deploy* for incremental changes to your SAM stack.  
- The *template.yaml* file is the SAM CloudFormation stack for the deployment. You do not need to edit this file directly.
- The *lambda-crawl* folder has the Lambda function to discover the RSS feeds, if files are present on S3 and see how much days of data need to be retrieved. It is triggered at the start of the Step Function.
- The *lambda-getfeed* folder contains the source code the function that checks every feed individually. It is triggered in the map state of the Step Function.
- The *statemachine* folder contains the source code for Step Function in JSON.
- The *lambda-layer* folder contains the *requirements.txt* file for the Lambda layer of the blog retrieval function. 
- The *graphql* folder contains the GraphQL schema and VTL resolvers for AppSync. 


License
-------

MIT-0, please see the 'LICENSE' file for more info. 


Contact
-------

In case of questions or bugs, please raise an issue or reach out to @marekq!
