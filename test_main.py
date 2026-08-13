'''
Tests for jwt flask app.

The environment is set BEFORE `import main`, and the fixture reloads the module. main.py
reads JWT_SECRET at import time, so the original fixture body

    os.environ['JWT_SECRET'] = SECRET

ran after the import had already captured the old value and had no effect whatsoever.
Measured: main.JWT_SECRET was 'abc123abc1234', the published default, so the suite signed
its tokens with the very key the app should never use and could not have noticed.
test_fixture_actually_sets_the_secret below is what keeps that from coming back.
'''
import base64
import datetime
import importlib
import inspect
import json
import logging
import os
import re

import jwt
import pytest

SECRET = 'TestSecret'
EMAIL = 'wolf@thedoor.com'
PASSWORD = 'huff-puff'
# The key main.py used to fall back to. Tokens signed with it must be rejected.
OLD_PUBLISHED_DEFAULT = 'abc123abc1234'

os.environ['JWT_SECRET'] = SECRET
os.environ.setdefault('LOG_LEVEL', 'ERROR')

import main  # noqa: E402  (must follow the environment set-up above)
import hash_password  # noqa: E402

os.environ['AUTH_USERS'] = json.dumps({EMAIL: hash_password.hash_password(PASSWORD)})


@pytest.fixture
def client():
    os.environ['JWT_SECRET'] = SECRET
    os.environ['AUTH_USERS'] = json.dumps({EMAIL: hash_password.hash_password(PASSWORD)})
    # Reload so the module-level JWT_SECRET is re-read from the environment above. Without
    # this, a test that changed the variable would not affect the app under test.
    importlib.reload(main)
    main.APP.config['TESTING'] = True
    yield main.APP.test_client()


def _token(client_):
    response = client_.post('/auth',
                            data=json.dumps({'email': EMAIL, 'password': PASSWORD}),
                            content_type='application/json')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.json['token']


def _sign(secret, **claims):
    payload = {'exp': datetime.datetime.utcnow() + datetime.timedelta(weeks=2),
               'nbf': datetime.datetime.utcnow(),
               'email': EMAIL}
    payload.update(claims)
    token = jwt.encode(payload, secret, algorithm='HS256')
    return token.decode('utf-8') if isinstance(token, bytes) else token


def test_fixture_actually_sets_the_secret(client):
    '''The fixture used to be a no-op. This fails if that regresses.'''
    assert main.JWT_SECRET == SECRET
    assert main.JWT_SECRET != OLD_PUBLISHED_DEFAULT


def test_unset_secret_generates_a_random_key_rather_than_the_old_default(caplog):
    '''
    Covers the fallback branch of _load_secret, which no other test can reach.

    The client fixture always sets JWT_SECRET before reloading, so _load_secret always took
    the from_env branch and the generated-key path never executed. Replacing
    secrets.token_urlsafe(32) with the old published literal therefore left the whole suite
    passing, which made the fixture-based tests useless as a guard on exactly the branch
    where a regression to a published key would be silent. That branch is also what the
    function's docstring relies on when it claims published-key forgery is impossible, so
    the claim needed a test of its own.

    Deliberately does not use the client fixture, and restores the environment and the
    module afterwards so test order cannot matter.
    '''
    saved_secret = os.environ.pop('JWT_SECRET', None)
    saved_level = os.environ.get('LOG_LEVEL')
    # This module sets LOG_LEVEL=ERROR to keep the suite quiet, and main's _logger() applies
    # it to the same logger _load_secret warns on. On a reload that level is already in place,
    # so the warning was filtered before caplog could see it and this test failed against
    # correct code. Raising the level for the duration is what makes the assertion meaningful.
    os.environ['LOG_LEVEL'] = 'WARNING'
    caplog.set_level(logging.WARNING, logger=main.__name__)
    try:
        importlib.reload(main)
        first = main.JWT_SECRET
        importlib.reload(main)
        second = main.JWT_SECRET

        assert first != OLD_PUBLISHED_DEFAULT
        assert second != OLD_PUBLISHED_DEFAULT
        # Any fixed fallback, published or not, makes these equal. A per-process key does not.
        assert first != second
        assert len(first) >= 32
        # The fallback has to be loud, or an unset variable in production looks like success.
        assert any('JWT_SECRET is not set' in r.message for r in caplog.records)
    finally:
        os.environ['JWT_SECRET'] = SECRET if saved_secret is None else saved_secret
        if saved_level is None:
            os.environ.pop('LOG_LEVEL', None)
        else:
            os.environ['LOG_LEVEL'] = saved_level
        importlib.reload(main)


def test_health(client):
    response = client.get('/')
    assert response.status_code == 200
    assert response.json == {'message': 'Healthy'}


def test_auth(client):
    body = {'email': EMAIL, 'password': PASSWORD}
    response = client.post('/auth',
                           data=json.dumps(body),
                           content_type='application/json')

    assert response.status_code == 200
    token = response.json['token']
    assert token is not None


def test_auth_rejects_wrong_password(client):
    response = client.post('/auth',
                           data=json.dumps({'email': EMAIL, 'password': 'not-the-password'}),
                           content_type='application/json')
    assert response.status_code == 401
    assert response.json == {'error': 401, 'message': 'Invalid credentials'}


def test_auth_rejects_unknown_email(client):
    response = client.post('/auth',
                           data=json.dumps({'email': 'nobody@example.com',
                                            'password': PASSWORD}),
                           content_type='application/json')
    # Same message as a wrong password, so the endpoint is not an account oracle.
    assert response.status_code == 401
    assert response.json == {'error': 401, 'message': 'Invalid credentials'}


@pytest.mark.parametrize('body,message', [
    ({'password': PASSWORD}, 'Missing parameter: email'),
    ({'email': EMAIL}, 'Missing parameter: password'),
])
def test_auth_missing_parameters_return_400(client, body, message):
    '''These answered HTTP 200 before, with the 400 buried in a JSON array.'''
    response = client.post('/auth', data=json.dumps(body),
                           content_type='application/json')
    assert response.status_code == 400
    assert response.json == {'error': 400, 'message': message}


@pytest.mark.parametrize('data,content_type', [
    ('not json at all', 'text/plain'),
    ('[1, 2, 3]', 'application/json'),
    ('"a string"', 'application/json'),
    ('{bad json', 'application/json'),
])
def test_auth_bad_body_returns_400(client, data, content_type):
    response = client.post('/auth', data=data, content_type=content_type)
    assert response.status_code == 400
    assert response.json['error'] == 400


def test_contents_with_valid_token(client):
    token = _token(client)
    response = client.get('/contents', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200
    assert response.json['email'] == EMAIL
    assert response.json['exp'] > response.json['nbf']


@pytest.mark.parametrize('headers', [
    {},
    {'Authorization': ''},
    {'Authorization': 'Bearer '},
    {'Authorization': 'Bearer not.a.real.token'},
    {'Authorization': 'Basic dXNlcjpwYXNz'},
])
def test_contents_rejects_bad_authorization(client, headers):
    response = client.get('/contents', headers=headers)
    assert response.status_code == 401
    assert response.json == {'error': 401, 'message': 'Unauthorized'}


def test_contents_rejects_a_token_without_the_bearer_scheme(client):
    '''str.replace() accepted a bare token; a prefix strip does not.'''
    token = _token(client)
    response = client.get('/contents', headers={'Authorization': token})
    assert response.status_code == 401


def test_contents_rejects_token_signed_with_the_old_published_default(client):
    '''The forgery that the hardcoded fallback made possible.'''
    forged = _sign(OLD_PUBLISHED_DEFAULT, email='attacker@evil.com')
    response = client.get('/contents', headers={'Authorization': 'Bearer ' + forged})
    assert response.status_code == 401


def test_contents_rejects_an_expired_token(client):
    expired = _sign(SECRET,
                    exp=datetime.datetime.utcnow() - datetime.timedelta(days=1),
                    nbf=datetime.datetime.utcnow() - datetime.timedelta(days=2))
    response = client.get('/contents', headers={'Authorization': 'Bearer ' + expired})
    assert response.status_code == 401


def test_contents_rejects_a_token_not_yet_valid(client):
    future = _sign(SECRET, nbf=datetime.datetime.utcnow() + datetime.timedelta(days=1))
    response = client.get('/contents', headers={'Authorization': 'Bearer ' + future})
    assert response.status_code == 401


def test_contents_tolerates_a_token_missing_claims(client):
    '''claims['email'] raised KeyError and answered 500 for a validly signed token.'''
    token = jwt.encode({'email': EMAIL}, SECRET, algorithm='HS256')
    token = token.decode('utf-8') if isinstance(token, bytes) else token
    response = client.get('/contents', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200
    assert response.json['exp'] is None
    assert response.json['nbf'] is None


def test_bearer_prefix_is_stripped_once_only(client):
    '''Regression guard for str.replace() removing every occurrence.'''
    assert main._bearer_token('Bearer aa.Bearer b.cc') == 'aa.Bearer b.cc'
    assert main._bearer_token('aa.bb.cc') is None
    assert main._bearer_token('bearer aa.bb.cc') is None


def test_errors_are_json(client):
    '''The README promises JSON for errors; abort() served HTML.'''
    assert client.get('/does-not-exist').json == {'error': 404, 'message': 'Not found'}
    assert client.delete('/auth').json == {'error': 405,
                                          'message': 'Method not allowed'}


def test_hash_comparison_is_constant_time():
    '''
    A STATIC check, deliberately, because this property is not observable functionally.

    Replacing hmac.compare_digest(candidate, expected) with == returns exactly the same
    values for every input, so the whole suite still passed when that swap was poisoned in.
    The difference is how early the comparison aborts, and a timing assertion here would be
    flaky on shared CI. So this reads the source instead: cruder, but it fails when the call
    is removed, which is the regression worth catching.
    '''
    source = inspect.getsource(main._check_password)
    assert 'compare_digest' in source, 'password comparison must be constant-time'
    assert not re.search(r'return\s+candidate\s*==\s*expected', source)


def test_password_hashing_round_trips():
    stored = hash_password.hash_password(PASSWORD)
    algorithm, iterations, salt, digest = stored.split('$')
    assert algorithm == 'pbkdf2_sha256'
    assert int(iterations) == main.PBKDF2_ITERATIONS
    assert len(base64.b64decode(salt)) == hash_password.SALT_BYTES
    assert len(base64.b64decode(digest)) == 32
    # A fresh salt each call, so two hashes of the same password differ.
    assert hash_password.hash_password(PASSWORD) != stored


def test_no_users_configured_denies_everyone(client):
    '''Fails closed: the previous version issued a token to any caller.'''
    os.environ['AUTH_USERS'] = ''
    try:
        response = client.post('/auth',
                               data=json.dumps({'email': EMAIL, 'password': PASSWORD}),
                               content_type='application/json')
        assert response.status_code == 401
    finally:
        os.environ['AUTH_USERS'] = json.dumps(
            {EMAIL: hash_password.hash_password(PASSWORD)})


@pytest.mark.parametrize('value', ['not json', '[1,2]', '{"a": ', '"str"'])
def test_malformed_auth_users_denies_everyone(client, value):
    os.environ['AUTH_USERS'] = value
    try:
        response = client.post('/auth',
                               data=json.dumps({'email': EMAIL, 'password': PASSWORD}),
                               content_type='application/json')
        assert response.status_code == 401
    finally:
        os.environ['AUTH_USERS'] = json.dumps(
            {EMAIL: hash_password.hash_password(PASSWORD)})
