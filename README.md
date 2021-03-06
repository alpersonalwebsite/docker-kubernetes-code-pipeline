# Docker, Kubernetes and Code Pipeline

## Overview

This is an easy, basic and raw example of **HOW to** containerize and deploy a flask app to `Kubernetes` using `EKS`.

Every change in your repository will trigger a new build (in Code Build) which will result in a new docker image deployed as a container in our EKS cluster. 

## Requirements

* Python 3.6+
* pip
* aws cli (version 2)
* eksctl
* kubectl
* Works on Linux, Windows, macOS, BSD
* An AWS account

## Local development

### Install dependencies

```
pip install -r requirements.txt
```

### Before running the application

Create the file `.env_file` and make sure you are adding it to `.gitignore`. You definitely **don't want** to commit your secrets to GiHub.

```
JWT_SECRET='MyJWTTTT'
LOG_LEVEL=DEBUG
```

If you don't set these environment variables, the app will default to what we define as base case in `main.py` 

```py
JWT_SECRET = os.environ.get('JWT_SECRET', 'abc123abc1234')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
```
---

### Run Flask App

```
python main.py
```

### Run Unit Tests

```
python -m pytest test_main.py
```

### Endpoints

Responses (including errors) are returned as JSON objects.

#### Error Handling
Errors format

```
{
  "error": 400,
  "message": "Bad request"
}
```

**Error types**
* 400: Bad request
* 401

#### GET and POST / 
* Returns an object with the key message and the value Healthy.

##### Request
```
curl http://127.0.0.1:8080/
```

##### Response

```json
{
  "message": "Healthy"
}
```

#### POST /auth
* Returns an object with the key token and the generated JWT token as the value
* Requires email (string) and password (string)

##### Request

```
curl http://127.0.0.1:8080/auth -X POST -H "Content-Type: application/json" -d '{ "email": "email@email.com", "password": "somePassWord" }'
```

##### Response

```json
{
  "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2MjQ0Njk3OTEsIm5iZiI6MTYyMzI2MDE5MSwiZW1haWwiOiJlbWFpbEBlbWFpbC5jb20ifQ.L1FHrGkceqamGyyQeTJ2rjL8B_4xBcc73ESswFWiIus"
}
```

#### GET /contents
* Returns the decrypted content of the JWT including email, exp and nbf with their proper values
* Requires a valid JWT token

*Note:* The lifespan of the token starts after the time stated in the nbf claim and ends at the time stated in the exp claim.

##### Request

```
curl http://127.0.0.1:8080/contents -H "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2MjQ0Njk3OTEsIm5iZiI6MTYyMzI2MDE5MSwiZW1haWwiOiJlbWFpbEBlbWFpbC5jb20ifQ.L1FHrGkceqamGyyQeTJ2rjL8B_4xBcc73ESswFWiIus"
```

##### Response

```json
{
  "email": "email@email.com", 
  "exp": 1624469791, 
  "nbf": 1623260191
}
```

## Docker locally

### Build and tag

```
docker build -t dockerizedflaskapp .
```

### Run the container

```
docker run --name containerDockerizedflaskapp --env-file=.env_file -p 80:8080 dockerizedflaskapp
```

*Notes:*
* We pass the file containing the environment variables
* We expose the port 8080 of the container to the port 80 of the host machine


### Check container

```
curl http://localhost:80/
```

Expected response:

```
{"message":"Healthy"}
```

#### Other useful commands

As a clean up you can remove the `container` and the `image` as well.

1. List containers: `docker container ls`
1. Stop container: `docker container stop <container_id>`
1. Remove container: `docker container rm <container_id>`
1. List images: `docker image ls`
1. Remove image: `docker image rm <image_id>`

## Docker, Kubernetes and AWS Code Pipeline

### Create EKS cluster
With default configuration.

```
eksctl create cluster --name simple-jwt-api --region=us-east-2
```

*Note:* In case of failures, try switching the `region`

Wait until `AWS CloudFormation` finishes creating your stack.
This operation can take `20mins` or more.

**Get the status of the nodes**

```
kubectl get nodes
```

#### Create IAM Role

This is the IAM role that CodeBuild will assume to access EKS cluster (it is going to include the trust relationship and the proper policy)

If you don't know your account id...

```
aws sts get-caller-identity --query Account --output text
```

Then, replace `<ACCOUNT_ID>` in `trust.json` with your account id.

```
aws iam create-role --role-name SimpleJwtApiCodeBuildKubectlRole --assume-role-policy-document file://trust.json --output text --query 'Role.Arn'
```

##### Create Policy

We are going to create a new policy and attach it to the previous role.

```
aws iam put-role-policy --role-name SimpleJwtApiCodeBuildKubectlRole --policy-name eks-describe --policy-document file://iam-role-policy.json
```

#### Allow the role to access the cluster

We are going to modify `aws-auth ConfigMap` (used to grant role-based access control to your cluster).
Currently just us (or the user who created the cluster) has permissions to administer it.

```bash
# The file will be created at `/System/Volumes/Data/private/tmp/aws-auth-patch.yml` path
kubectl get -n kube-system configmap/aws-auth -o yaml > /tmp/aws-auth-patch.yml

vi /System/Volumes/Data/private/tmp/aws-auth-patch.yml
```

Under `mapRoles`, add a new groups block:

```yml
    - groups:
      - system:masters
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/SimpleJwtApiCodeBuildKubectlRole
      username: build  
```

The result would look like...

```yml
  mapRoles: |
    - groups:
      - system:masters
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/SimpleJwtApiCodeBuildKubectlRole
      username: build  
    - groups:
      - system:bootstrappers
      - system:nodes
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/eksctl-simple-jwt-api-nodegroup-n-NodeInstanceRole-1OTCVKJBLMH9Q
      username: system:node:{{EC2PrivateDNSName}}
```

Now we can update the cluster's configmap

```
kubectl patch configmap/aws-auth -n kube-system --patch "$(cat /tmp/aws-auth-patch.yml)"
```

### Create pipeline stack

First, update `parameters.json` with your data.

**IMPORTANT**: Be sure you are not committing `parameters.json` to your repo (include it to your `.gitignore`). I'm doing it here as an example, but with placeholders.

You will need to update at least your GH username and add a personal access token. You can generate one here: https://github.com/settings/tokens. For scope select `repo`.
If you are using other name for your cluster and/or role, update that also in your `parameters.json`

```
aws cloudformation create-stack  \
 --stack-name ci-cd-codepipeline \
 --region us-east-2 \
 --template-body file://ci-cd-codepipeline.cfn.yml \
 --parameters file://parameters.json \
 --capabilities "CAPABILITY_IAM"
```

This will also create a `CodeBuild` project based on `buildspec.yml`

*Note:* We should try to use the same version of `kubectl` locally (when we are creating the cluster) and in `buildspec.yml`.


### Add JWT_SECRET to AWS Parameter Store

```
aws ssm put-parameter --name JWT_SECRET --overwrite --value "MyJWTTTT" --type SecureString --region us-east-2
```

*This secret will be set during build (by CodeBuild) as an environment variable.*

### Testing

Now, every time you make a change in your repository (example, when you merge a PR) `CodePipeline` will execute both stages, first fetching from source (in our case GH) and then building and deploying the artifact to the EKS cluster. 

#### Get the external Ip

```
kubectl get services simple-jwt-api -o wide
```

#### Get a TOKEN

We are getting a TOKEN and setting it as an environment variable

```
export TOKEN=`curl -d '{"email":"email@email","password":"password"}' -H "Content-Type: application/json" -X POST abec308a46cde4b158fbda16e02f7451-1708554601.us-east-2.elb.amazonaws.com/auth  | jq -r '.token'`
```

### Make request with a VALID TOKEN

```
curl --request GET 'abec308a46cde4b158fbda16e02f7451-1708554601.us-east-2.elb.amazonaws.com/contents' -H "Authorization: Bearer ${TOKEN}" | jq 
```

**Sample result**

```
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
100    58  100    58    0     0    325      0 --:--:-- --:--:-- --:--:--   325
{
  "email": "email@email",
  "exp": 1624309579,
  "nbf": 1623099979
}
```

### Clean up

#### Delete Code Pipeline Stack

```
aws cloudformation delete-stack --stack-name ci-cd-codepipeline --region=us-east-2
```

#### Delete secret from Parameter Store

```
aws ssm delete-parameter --name JWT_SECRET --region us-east-2
```

#### Delete EKS cluster

```
eksctl delete cluster simple-jwt-api --region=us-east-2
```