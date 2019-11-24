#!/bin/bash
# @marekq
# www.marek.rocks

# MANDATORY, CHANGE THIS TO YOUR BUCKET NAME
bucketn='marek-temp'

# OPTIONALLY, CHANGE TO YOUR CLOUDFORMATION STACK NAME
stackn='rss-reader'	

############################################################

# set variables
RED='\033[0;31m'
NC='\033[0m'
dirn='./lambda/libs'

# rebuild the lambda package 
rm -rf $dirn
mkdir $dirn
pip3 install -r ./lambda/requirements.txt -t ./lambda/libs

# validate the sam stack
echo -e "\n${RED} * Running SAM validate locally to test function... ${NC}\n"
sam validate

# package the sam template and upload lambda code artifacts to s3
echo -e "\n${RED} * Packaging the artifacts to S3 and preparing SAM template... ${NC}\n"
sam package --template-file template.yaml --output-template-file packaged.yaml --s3-bucket $bucketn

# deploy the sam stack to the aws region
echo -e "\n${RED} * Deploying the SAM stack to AWS... ${NC}\n"
sam deploy --template-file ./packaged.yaml --stack-name $stackn --capabilities CAPABILITY_IAM
