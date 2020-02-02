#!/bin/bash
# @marekq
# www.marek.rocks

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

# deploy the sam stack to the aws region
echo -e "\n${RED} * Deploying the SAM stack to AWS... ${NC}\n"

# check if samconfig.toml file is present
if [ ! -f samconfig.toml ]; then
    echo "no samconfig.toml found, starting guided deploy"
    sam deploy -g
else
    echo "samconfig.toml found, proceeding to deploy"
    sam deploy
fi
