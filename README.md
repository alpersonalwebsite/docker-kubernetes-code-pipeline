# Docker, Kubernetes and Code Pipeline

Containerize a small Flask JWT API, deploy it to EKS, and have CodePipeline and CodeBuild
rebuild and redeploy the image on every push to `master`.

Written in June 2021 and kept on its original dependency versions on purpose. The pinned
packages are not upgraded here; what changed is described in
[What changed, and why](#what-changed-and-why), and every claim in this file was measured in
a container or against a real Kubernetes cluster rather than inferred.

## Requirements

* Python 3.9 (see [the version ceiling](#why-python-39) before reaching for something newer)
* pip
* aws cli (version 2)
* eksctl
* kubectl
* docker
* An AWS account

## Local development

### Install dependencies

```
pip install -r requirements.txt
```

### Create a user

`/auth` verifies credentials, so it needs a user to verify against. `hash_password.py`
generates the entry, prompting for the password so it never reaches your shell history:

```
python hash_password.py user@example.com
```

It prints a line ready to paste:

```
AUTH_USERS='{"user@example.com": "pbkdf2_sha256$260000$c2FsdHNhbHQ=$aGFzaGhhc2g="}'
```

### Before running the application

Two variables matter: `JWT_SECRET`, which signs the tokens, and `AUTH_USERS`, which holds
the email-to-hash map above. `LOG_LEVEL` is optional.

For `python main.py`, export them. The JSON needs single quotes so the shell does not eat
the `$` separators in the hash:

```shell
export JWT_SECRET=MyJWTTTT
export LOG_LEVEL=DEBUG
export AUTH_USERS='{"user@example.com": "pbkdf2_sha256$260000$c2FsdHNhbHQ=$aGFzaGhhc2g="}'
```

For `docker run`, put the same values in `.env_file`, which is gitignored. **No quotes in
that file**: `--env-file` takes each value literally to the end of the line, so
`JWT_SECRET='MyJWTTTT'` makes the quote characters part of the secret. Measured: the app
then saw `"'MyJWTTTT'"`.

```
JWT_SECRET=MyJWTTTT
LOG_LEVEL=DEBUG
AUTH_USERS={"user@example.com": "pbkdf2_sha256$260000$c2FsdHNhbHQ=$aGFzaGhhc2g="}
```

> `.env_file` is read by `docker run --env-file` and by nothing else. It is not loaded by
> `python main.py`: an earlier version of this README implied it was, and the app silently
> used a generated key with no users configured instead.

If `JWT_SECRET` is unset the app generates a random key for that process and logs a warning.
It no longer falls back to a fixed default, because that default was committed here and made
every token forgeable. If `AUTH_USERS` is unset or malformed, no one can authenticate and
`/auth` answers 401.

### Run Flask App

```
python main.py
```

### Run Unit Tests

```
python -m pytest test_main.py
```

31 tests. On Python 3.10 or newer, add `--assert=plain`; see [below](#why-python-39).

## Endpoints

Responses, including errors, are JSON objects.

### Error handling

```json
{
  "error": 400,
  "message": "Bad request"
}
```

**Error types**

* 400: Bad request, or a missing `email` or `password`
* 401: no credentials, wrong credentials, or a missing, malformed, expired or
  wrongly-signed token
* 404, 405: unknown route, wrong method

### GET and POST /

Returns an object with the key message and the value Healthy. No credentials needed; this is
the route the Kubernetes probes use.

#### Request

```
curl http://127.0.0.1:8080/
```

#### Response

```json
{
  "message": "Healthy"
}
```

### POST /auth

Returns an object with the key token and the generated JWT as the value.

* Requires `email` (string) and `password` (string)
* The password is checked against `AUTH_USERS`. An unknown email and a wrong password give
  the same 401, so the endpoint cannot be used to discover which accounts exist.

#### Request

```
curl http://127.0.0.1:8080/auth -X POST -H "Content-Type: application/json" -d '{ "email": "user@example.com", "password": "somePassWord" }'
```

#### Response

```json
{
  "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2MjQ0Njk3OTEsIm5iZiI6MTYyMzI2MDE5MSwiZW1haWwiOiJlbWFpbEBlbWFpbC5jb20ifQ.L1FHrGkceqamGyyQeTJ2rjL8B_4xBcc73ESswFWiIus"
}
```

### GET /contents

Returns the decoded content of the JWT: email, exp and nbf. Requires a valid token.

*Note:* the token is valid from the time in `nbf` until the time in `exp`.

#### Request

```
curl http://127.0.0.1:8080/contents -H "Authorization: Bearer <TOKEN>"
```

The `Bearer ` prefix is required, and exactly one is stripped.

#### Response

```json
{
  "email": "user@example.com",
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
* The environment file supplies `JWT_SECRET` and `AUTH_USERS`
* Container port 8080 is published on host port 80
* The container runs as uid 10001, not root

### Check container

```
curl http://localhost:80/
```

Expected response:

```
{"message":"Healthy"}
```

#### Other useful commands

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

Wait until `AWS CloudFormation` finishes creating your stack. This operation can take
`20mins` or more.

**Get the status of the nodes**

```
kubectl get nodes
```

### Create IAM Role

This is the IAM role CodeBuild assumes to reach the EKS cluster.

Get your account id and your own principal ARN:

```
aws sts get-caller-identity --query Account --output text
aws sts get-caller-identity --query Arn --output text
```

In `trust.json`, replace `<YOUR_PRINCIPAL_ARN>` with the second value. That names one
principal rather than the whole account; the pipeline's own CodeBuild role is added
automatically when the stack is created.

```
aws iam create-role --role-name SimpleJwtApiCodeBuildKubectlRole --assume-role-policy-document file://trust.json --output text --query 'Role.Arn'
```

#### Create Policy

In `iam-role-policy.json`, replace `<REGION>`, `<ACCOUNT_ID>` and `<CLUSTER_NAME>`. The
policy grants `eks:DescribeCluster` on that one cluster and `ssm:GetParameters` on this
application's two parameters only.

```
aws iam put-role-policy --role-name SimpleJwtApiCodeBuildKubectlRole --policy-name eks-describe --policy-document file://iam-role-policy.json
```

### Allow the role to deploy to the cluster

Two steps: grant the permissions inside Kubernetes, then map the IAM role to the group that
holds them.

First apply the Role and RoleBinding, as the cluster creator:

```
kubectl apply -f rbac-deployer.yml
```

That grants exactly what the pipeline does in the `default` namespace: upsert the
`simple-jwt-api` Secret, apply the Service and Deployment, and watch the rollout. It cannot
read any other Secret, delete anything, or touch another namespace.

Then edit the `aws-auth` ConfigMap, which maps IAM identities to Kubernetes ones:

```bash
kubectl get -n kube-system configmap/aws-auth -o yaml > /tmp/aws-auth-patch.yml
vi /tmp/aws-auth-patch.yml
```

Under `mapRoles`, add:

```yml
    - groups:
      - simple-jwt-api-deployers
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/SimpleJwtApiCodeBuildKubectlRole
      username: build
```

The result would look like:

```yml
  mapRoles: |
    - groups:
      - simple-jwt-api-deployers
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/SimpleJwtApiCodeBuildKubectlRole
      username: build
    - groups:
      - system:bootstrappers
      - system:nodes
      rolearn: arn:aws:iam::<ACCOUNT_ID>:role/eksctl-simple-jwt-api-nodegroup-n-NodeInstanceRole-1OTCVKJBLMH9Q
      username: system:node:{{EC2PrivateDNSName}}
```

> This used to say `system:masters`, which is unrestricted cluster administration. See
> [What changed](#the-build-role-had-full-cluster-admin) for what that allowed and what the
> group grants instead.

Apply it:

```
kubectl patch configmap/aws-auth -n kube-system --patch "$(cat /tmp/aws-auth-patch.yml)"
```

> Take a copy of `aws-auth` before editing it. A malformed `mapRoles` can lock every IAM
> identity, including yours, out of the cluster's API.

### Add the secrets to AWS Parameter Store

Both are `SecureString`, and CodeBuild reads them into the build environment and then into a
Kubernetes Secret.

```
aws ssm put-parameter --name JWT_SECRET --overwrite --value "MyJWTTTT" --type SecureString --region us-east-2
aws ssm put-parameter --name AUTH_USERS --overwrite --value '{"user@example.com": "pbkdf2_sha256$260000$..."}' --type SecureString --region us-east-2
```

Use the `AUTH_USERS` value that `hash_password.py` printed. Nothing here stores a plaintext
password.

### Create pipeline stack

Copy the template and fill in your values. `parameters.json` is gitignored; the tracked file
is the example.

```
cp parameters.example.json parameters.json
```

Set at least `GitHubUser` and `GitHubToken`. Generate a token at
https://github.com/settings/tokens with `repo` scope. If you renamed the cluster or the role,
update those too.

```
aws cloudformation create-stack  \
 --stack-name ci-cd-codepipeline \
 --region us-east-2 \
 --template-body file://ci-cd-codepipeline.cfn.yml \
 --parameters file://parameters.json \
 --capabilities "CAPABILITY_IAM"
```

This also creates a `CodeBuild` project based on `buildspec.yml`.

*Note:* keep `KUBECTL_VERSION` in `buildspec.yml` within one minor version of the EKS control
plane, and bump it when you upgrade the cluster.

### Testing

Every change to the repository makes `CodePipeline` run both stages: fetch from GitHub, then
build, push and deploy to EKS.

#### Get the external Ip

```
kubectl get services simple-jwt-api -o wide
```

#### Get a TOKEN

```
export TOKEN=`curl -d '{"email":"user@example.com","password":"somePassWord"}' -H "Content-Type: application/json" -X POST abec308a46cde4b158fbda16e02f7451-1708554601.us-east-2.elb.amazonaws.com/auth  | jq -r '.token'`
```

The hostname above is from the original 2021 run and no longer resolves. Recorded output
throughout this file keeps its original identifiers rather than being rewritten.

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

### Rotating the secret

Update the parameter, then force new pods:

```
aws ssm put-parameter --name JWT_SECRET --overwrite --value "NewSecret" --type SecureString --region us-east-2
kubectl rollout restart deployment/simple-jwt-api
```

The restart is required. `env.valueFrom` injects a Secret when the container starts, so
updating the Secret alone leaves running pods on the old value. Measured on k3s v1.31.5: the
Secret read the new value while the pod still reported the previous one. A pipeline run does
this for you, because each build changes the image tag and so the pod template.

### Clean up

#### Delete Code Pipeline Stack

```
aws cloudformation delete-stack --stack-name ci-cd-codepipeline --region=us-east-2
```

The ECR repository and the artifact bucket are `Retain`, so they survive the stack. Empty and
delete them by hand if you want them gone.

#### Delete secrets from Parameter Store

```
aws ssm delete-parameter --name JWT_SECRET --region us-east-2
aws ssm delete-parameter --name AUTH_USERS --region us-east-2
```

#### Delete EKS cluster

```
eksctl delete cluster simple-jwt-api --region=us-east-2
```

## What changed, and why

### The deployed API signed tokens with a key published in this repository

`main.py` read `JWT_SECRET` from the environment and fell back to a literal
`'abc123abc1234'` committed here. `simple_jwt_api.yml` had **no `env:` block at all**, so
every pod used that fallback. `buildspec.yml` did read the real value from Parameter Store,
but into the CodeBuild environment, which the pods never see, and this README described that
as the secret being set for the service.

So anyone could mint a token the API accepts, for any email, using only a value in this file.
Measured: a token signed with that literal was accepted with HTTP 200, and is rejected now.

The Deployment reads `JWT_SECRET` and `AUTH_USERS` from a Kubernetes Secret that
`buildspec.yml` creates from the Parameter Store values before applying the manifest. That
creation step did not exist. Verified end to end on k3s v1.31.5: the pod reports the value
from the Secret, a wrong password gets 401, a correct one gets a token, `/contents` decodes
it, and a token signed with the old key is refused.

### `/auth` authenticated nobody

It issued a valid token for any email and password. The password was read into a dictionary
and discarded. It now verifies against PBKDF2-SHA256 hashes from `AUTH_USERS` using
`hmac.compare_digest`, at 260,000 iterations, which is Django 3.2's 2021 default. Unknown
email and wrong password return the same 401 and both pay the KDF cost, so neither the
message nor the timing reveals which accounts exist. No users configured means no one gets in.

### The local secret was baked into the published image

There was no `.dockerignore` and the Dockerfile does `COPY . /app`, while this README tells
you to create `.env_file` in that directory. Built and inspected: the image contained
`/app/.env_file` with the secret in it, plus `/app/.git` with 28 files of history. `post_build`
pushes that image to ECR. Deleting the file in a later layer would not have helped.

### Every error response was HTTP 200

`jsonify({"message": ...}, 400)` serialises its second argument instead of setting a status,
so all three `/auth` error paths answered 200 with the code buried in a JSON array:

```
missing password -> HTTP 200   [{"message":"Missing parameter: password"},400]
```

A client reading `response.status_code` saw success on every one. They return the documented
`{"error": N, "message": ...}` object with the matching status now, and `abort()` produces
JSON rather than HTML, which is what this README always claimed.

### The pipeline stack could not be created

Three independent defects in one 40-line inline Lambda:

* `Runtime: python2.7`. `cfn-lint` reports **E2533**: deprecated 2021-07-15, with creation
  disabled that same day. Now `python3.12`.
* `from botocore.vendored import requests` then `requests.put(...)`. The import still
  succeeds, which is what makes this easy to misread: on botocore 1.43.70 only `exceptions`
  and `packages` remain, and `put`, `post` and `get` are gone. So the call that signals
  CloudFormation raised `AttributeError` and the custom resource never answered, leaving the
  stack waiting on it. Now `urllib.request`.
* `response["Reason"] = e` stored the exception **object**, and `json.dumps` ran outside the
  `try`, raising `TypeError: Object of type Exception is not JSON serializable`. The error
  path crashed before it could report the error, so a real IAM failure hung rather than
  failing.

Both versions were run against a local server standing in for the CloudFormation callback.
The original signalled **nothing** on all four paths tested, including the two that should
have succeeded. Testing also found a fourth bug: the loop called `.startswith` on
`statement['Principal']['AWS']`, which is a **list** as soon as a second principal exists.

### The build role had full cluster admin

`aws-auth` mapped it into `system:masters`: read every Secret in every namespace, delete
anything, exec into any pod. A compromise of the pipeline, or of anything it installed, was a
compromise of the cluster.

`rbac-deployer.yml` grants only what the build does, in one namespace, with the Secret rules
scoped by name. Verified by impersonating the identity on a real cluster: all 11 operations
the pipeline performs are allowed and the whole `post_build` sequence runs to a successful
rollout, while listing Secrets, reading another application's Secret, reading `kube-system`
Secrets, deleting the Deployment or Service, `pods/exec`, creating clusterrolebindings,
reading nodes and creating a namespace are all denied.

### IAM policies were wildcarded

`ssm:GetParameters` on `Resource: "*"` is read access to every parameter in the account,
including other applications' SecureStrings. It appeared in both
`ci-cd-codepipeline.cfn.yml` and `iam-role-policy.json`. `iam:PassRole` on `"*"` beside
`codebuild:StartBuild` on `"*"` lets the pipeline role hand any role in the account to a
service it can start, which is how a CI role becomes an admin role.

Everything was scoped against the AWS service authorization data rather than guesswork, which
also settled what **cannot** be scoped: `ecr:GetAuthorizationToken` accepts no resource type,
so `"*"` is the only policy that can grant it, and it is the one wildcard left. The eight
`ec2` network-interface actions were removed instead, because their Describe actions likewise
accept no resource type and the CodeBuild project has no `VpcConfig`, so they were never used.

`trust.json` trusted `arn:aws:iam::<ACCOUNT_ID>:root` with no condition, which is every
identity in the account. Worse, the custom-resource Lambda keeps any statement whose principal
starts with `arn:aws:iam:`, so that account-wide trust **survived** stack creation instead of
being replaced.

### `pip install -r requirements.txt` produced an app that could not import

Only the four direct dependencies were pinned, so pip resolved flask 1.1.2's own requirements
to whatever was newest. jinja2 3.0 removed the `escape` re-export that flask 1.1.2 imports at
module scope:

```
from jinja2 import escape
ImportError: cannot import name 'escape' from 'jinja2'
```

`buildspec.yml` installs from this file and then runs pytest, so the CodeBuild stage failed
here too, before it reached the image build. Jinja2, Werkzeug, itsdangerous, MarkupSafe and
click are pinned at their June 2021 versions. The original four pins are untouched.

### The test fixture had no effect

It set `os.environ['JWT_SECRET']` in its body, but `main.py` reads that variable at import
time and `test_main.py` imports `main` at module scope, so the assignment always ran too late.
Measured: `main.JWT_SECRET` was `'abc123abc1234'`. The suite signed its tokens with the exact
key the app should never use and could not have noticed.

2 tests became 31. Each fix was poison-tested by reverting it and confirming a test fails.
Seven of eight were caught that way; the eighth, swapping `hmac.compare_digest` for `==`, is
not observable functionally, so it has a static guard and the test says so.

### Smaller corrections

* `str.replace(data, 'Bearer ', '')` stripped every occurrence rather than one prefix, so a
  token containing the sequence was corrupted (`Bearer aa.Bearer b.cc` became `aa.b.cc`), and
  a header with no scheme was accepted.
* `/contents` reimplemented the `require_jwt` decorator inline, so the decorator was defined
  and never used. It also read `claims['email']`, so a validly signed token missing a claim
  answered 500.
* Two bare `except:` clauses swallowed `KeyboardInterrupt` and turned unrelated internal
  errors into 401s. Now `jwt.InvalidTokenError`.
* `debug=True` served the Werkzeug interactive debugger, which executes Python from the
  browser. Opt-in via `FLASK_DEBUG` now.
* `FROM python:stretch` pinned no Python version and no digest, and Debian stretch reached end
  of life in June 2022 while the tag still resolves. Now `python:3.9-slim-bookworm`, Debian 12.
* The container ran as root. Now uid 10001, with `runAsNonRoot`, `readOnlyRootFilesystem` and
  all capabilities dropped. `/tmp` is an emptyDir because gunicorn needs it.
* `kubectl` was installed by resolving `stable.txt` at build time, so the client version
  changed by itself and could drift outside the one-minor support window. Pinned, and its
  published SHA-256 is verified before the binary is made executable, which it was not.
  Verified that a corrupted download fails the check.
* `aws-iam-authenticator` was pinned to a 2018 release, fetched with no integrity check, and
  unnecessary since AWS CLI 1.16.156 writes an `aws eks get-token` exec stanza.
* `$(aws ecr get-login --no-include-email)` is deprecated in CLI v1 and removed in v2, while
  this README asks for v2, and it printed a `docker login` command including the password into
  the build log. Now `get-login-password` piped to `--password-stdin`.
* The build reported success as soon as `kubectl apply` returned. It waits for the rollout now.
* No probes and `maxUnavailable: 2` of 3 replicas. Measured across a rollout: available
  replicas dropped to 1 with the old value and hold at 2 with `maxUnavailable: 1`.
* No resource requests, so the pods were BestEffort and first to be evicted. Now Burstable.
* The artifact bucket holds a copy of the whole repository per commit and had no encryption, no
  public-access block and no versioning. All three added, plus expiry. ECR gained scan-on-push,
  immutable tags and an untagged-image lifecycle rule.
* `parameters.json` held a `repo`-scoped GitHub token and was tracked, while this README told
  you to remember to ignore it. The tracked file is now `parameters.example.json` and
  `parameters.json` is ignored, so forgetting is no longer enough to leak a token. No real
  token was ever committed: all 11 commits carried the placeholder.
* This README told you to write `/tmp/aws-auth-patch.yml` and then edit
  `/System/Volumes/Data/private/tmp/aws-auth-patch.yml`. Those are the same file on macOS
  through a firmlink and different files everywhere else.
* `cfn-lint` goes from 7 findings to 0.

### Why Python 3.9

Measured, not chosen: pytest is pinned at 6.2.2 and its assertion rewriting fails on Python
3.10+ at collection with `TypeError: required field "lineno" missing from alias`. The suite
passes on 3.7 and 3.9, fails to collect on 3.10 through 3.13, and passes again on 3.11-3.13
with `--assert=plain`, which disables the rewriting and the readable failure output with it.

The app itself is not the constraint: it serves every endpoint correctly on 3.11 and 3.12,
verified directly. 3.9 keeps one Python across the image, the buildspec and local development.

## Not covered

* **Nothing here has been run against AWS.** There are no credentials in this environment.
  The application, the image and the Kubernetes manifests were verified in containers and on a
  local k3s v1.31.5 cluster; the CloudFormation template was checked with `cfn-lint` and its
  Lambda executed directly against a stand-in callback server. **CodeBuild, CodePipeline, ECR,
  Parameter Store, the `aws-auth` mapping and the EKS-specific paths are unverified**, so the
  scoped `logs` permissions and the dropped `aws-iam-authenticator` are the two changes most
  likely to need adjusting on a real run. Both say so at the point they occur.
* **No TLS.** The Service is a `LoadBalancer` on port 80, so every token crosses the network
  in cleartext, and a bearer token is a password with an expiry date. Fixing it needs a
  certificate and a hostname, which is more than this repository can carry.
* **`AUTH_USERS` is a JSON blob in an environment variable.** Fine for a demo with one or two
  users, wrong for anything real: adding a user means redeploying, and there is no lockout,
  rate limiting, password reset or audit trail.
* **The build container still runs Python 3.7**, which is end of life. It is the build
  environment rather than anything deployed, the pinned pytest cannot run on 3.10+, and the
  available runtimes depend on the CodeBuild image, which cannot be checked from here.
* **The CodePipeline GitHub source is the v1 provider**, using an OAuth token. AWS now
  recommends CodeStar Connections. Changing it alters the parameters and the setup steps, so
  it is left as-is.
* **The pinned dependencies are old and carry known CVEs.** That is deliberate: this is a
  snapshot of June 2021, and upgrading them is a different exercise from making the repository
  correct.
