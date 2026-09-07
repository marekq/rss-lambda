rss-lambda
==========

Monitor blogs through RSS and store new posts in DynamoDB. The workflow can also send notifications through Amazon SES, refresh JSON feeds in S3, and expose the stored posts through an optional AppSync API. A Standard Step Functions workflow runs at 09:00, 12:00, 15:00, 18:00, and 21:00 Netherlands time on weekdays, and at 09:00 on weekends. Failed, timed-out, and aborted executions send a concise SES alert.

The default feed list includes AWS, Google, and Wiz security feeds. Add or remove feeds in `lambda-crawl/feeds.txt`; each line contains a source name and RSS URL separated by a comma.

The feed retrieval Lambda stores article metadata and the RSS description in DynamoDB; it does not persist the fetched source HTML or full article text. The frontend retrieves readable content from the live article URL when a row is expanded and uses the stored description only as a fallback. If SES notifications are enabled, the fetched HTML is still used in memory for the email and is not written to DynamoDB. When a new article is found, the workflow also refreshes the source JSON file and the combined `all.json` file in the S3 bucket.

The workflow processes feeds with bounded concurrency and retries only transient Lambda service failures. A failed individual feed is recorded while successful feeds still refresh the JSON output; the overall execution then fails so the SES alert is sent. Article writes use a conditional DynamoDB transaction, making retries safe from duplicate records. Derived article counters are recalculated after each feed and after the combined `all.json` refresh, so malformed legacy counter values cannot abort article ingestion.


Installation
------------

- Install and configure the AWS SAM CLI, Docker, and AWS credentials for the target account.
- Edit `lambda-crawl/feeds.txt` if you want to change the monitored feeds.
- Run `make init` for the first deployment. SAM will prompt for the stack name, region, and parameter values, then save them in the ignored `samconfig.toml` file.
- Run `make deploy` for subsequent deployments.

The template parameters are:

- `SourceEmail`: SES-verified sender address.
- `DestEmail`: notification recipient address.
- `SendEmails`: set to `y` to enable notifications or `n` to disable them.
- `CreateAppSync`: set to `y` to create the read-only AppSync API or `n` to skip it.

SES sender and recipient addresses must be verified in the AWS account. The Step Functions console URL is exposed as the `StateMachineURL` CloudFormation output.


CodePipeline deployment
-----------------------

For automated deployment of this repository's local SAM source, use the pipeline definition in `pipeline/codepipeline.yaml` and the build commands in `pipeline/buildspec.yml`. The pipeline is a separate stack from `rssgraph2`:

`GitHub push/merge → CodeConnections → CodePipeline → CodeBuild → sam build → sam deploy → CloudFormation`

The pipeline stack creates a private, encrypted, versioned S3 artifact bucket, a CodePipeline source action, and a CodeBuild project using the AWS SAM Python 3.14 build image. The SAM source paths remain portable in `template.yaml`; CodeBuild packages them into that account's private bucket. No public artifact URLs are required.

### One-time setup

1. Ensure any existing CloudFormation Git sync configuration for `rssgraph2` is disabled or removed. Git sync must not continue to own the same stack once CodePipeline is deployed.
2. Create and authorize a GitHub connection in AWS CodeConnections. The connection must be `AVAILABLE` and have access to `marekq/rss-lambda`. Copy its connection ARN. Creating the connection requires a one-time GitHub authorization in the AWS console.
3. Create or select a CloudFormation execution role for the application stack. It must trust `cloudformation.amazonaws.com` and have permissions for every resource type in `template.yaml`, including Lambda, IAM, S3, DynamoDB, CloudWatch Logs, Step Functions, EventBridge Scheduler, EventBridge rules, AppSync, and tagging operations. The role ARN is passed to CodeBuild and is not stored in the repository.
4. Deploy the pipeline stack. Replace the connection and execution role ARNs with values from your account:

```bash
aws cloudformation deploy \
  --region eu-west-1 \
  --template-file pipeline/codepipeline.yaml \
  --stack-name rssgraph2-pipeline \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ConnectionArn=arn:aws:codeconnections:eu-west-1:123456789012:connection/example \
    FullRepositoryId=marekq/rss-lambda \
    BranchName=master \
    ApplicationStackName=rssgraph2 \
    CloudFormationExecutionRoleArn=arn:aws:iam::123456789012:role/RssGraphCloudFormation \
    SourceEmail=aws@example.com \
    DestEmail=operator@example.com \
    SendEmails=y \
    CreateAppSync=y
```

The pipeline source action is configured with `DetectChanges: true`, so a commit to `master` starts CodePipeline automatically. To run it manually after bootstrap:

```bash
PIPELINE_NAME=$(aws cloudformation describe-stacks \
  --region eu-west-1 \
  --stack-name rssgraph2-pipeline \
  --query 'Stacks[0].Outputs[?OutputKey==`PipelineName`].OutputValue' \
  --output text)

aws codepipeline start-pipeline-execution \
  --region eu-west-1 \
  --name "$PIPELINE_NAME"
```

The first deployment should be monitored in CodePipeline and CloudFormation. Keep the stack execution role stable; changing it outside the pipeline can cause CloudFormation rollback or tagging failures. For this repository, use either this CodePipeline workflow or direct `make deploy` for `rssgraph2`, not both.


Repository contents
-------------------

- `template.yaml` is the SAM/CloudFormation deployment source.
- `pipeline/codepipeline.yaml` defines the separate CodePipeline/CodeBuild bootstrap stack.
- `pipeline/buildspec.yml` packages and deploys the SAM application from CodeBuild.
- `lambda-crawl/` contains the function that discovers feeds and determines the retrieval window.
- `lambda-getfeed/` contains the function that retrieves and stores individual feed entries.
- `lambda-pagecount/` contains the manually invoked counter-refresh function.
- `statemachine/` contains the Standard Step Functions definition.
- `lambda-layer/` contains the shared Python dependency list for the feed retrieval functions.
- `graphql/` contains the AppSync schema and VTL resolver templates used as source/reference files.


License
-------

MIT-0, please see the `LICENSE` file.


Contact
-------

For questions or bugs, please raise an issue or reach out to @marekq.
