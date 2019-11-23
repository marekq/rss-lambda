#!/bin/bash
# @marekq
# www.marek.rocks

# MANDATORY, CHANGE THIS TO YOUR BUCKET NAME
bucketn='marek-temp'

# OPTIONALLY, CHANGE TO YOUR CLOUDFORMATION STACK NAME
stackn='rss-test'	

############################################################

RED='\033[0;31m'
NC='\033[0m'

rm -rf ./lambda/libs
mkdir ./lambda/libs
pip3 install -r requirements.txt -t ./lambda/libs

echo -e "\n${RED} * Running SAM validate locally to test function... ${NC}\n"
sam validate

echo -e "\n${RED} * Packaging the artifacts to S3 and preparing SAM template... ${NC}\n"
sam package --template-file template.yaml --output-template-file packaged.yaml --s3-bucket $bucketn

echo -e "\n${RED} * Deploying the SAM stack to AWS... ${NC}\n"
sam deploy --template-file ./packaged.yaml --stack-name $stackn --capabilities CAPABILITY_IAM
