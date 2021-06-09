# Docker, Kubernetes and Code Pipeline

Under development...

## Overview

This is an easy, basic and raw example of **HOW to** containerize and deploy a a flask app to `Kubernetes` using `EKS`.

Every change in your repository (??? is this right or just some changes) will trigger a new build in Code Build which will result in a new docker image deployed as a container in our EKS cluster. 

## Requirements

* Python 3.6+
* pip
* aws cli (version 2)
* eksctl
* kubectl
* Works on Linux, Windows, macOS, BSD

You will also need an AWS account (??? both console and programmatic access)

## Local development

### Install dependencies

```
pip3 install -r requirements.txt
```

### Run Flask App

```
python main.py
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
* Returns an object with email, exp and nbf with their proper values
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


Check this
    - groups:
      - system:bootstrappers
      - system:nodes
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/eksctl-simple-jwt-api-nodegroup-n-NodeInstanceRole-17C402QC9VF6

and how to update

---

Aclarar que tengo .env_file con los valores

```
JWT_SECRET='MyJWTTTT'
LOG_LEVEL=DEBUG
```