#!/usr/bin/env python
"""
A simple app to create a JWT token.
"""
import os
import base64
import binascii
import hashlib
import hmac
import json
import logging
import datetime
import functools
import secrets
import jwt

# pylint: disable=import-error
from flask import Flask, g, jsonify, request, abort


def _load_secret():
    '''
    Read the JWT signing key from the environment, or mint a throwaway one.

    This used to be `os.environ.get('JWT_SECRET', 'abc123abc1234')`. That default is a
    signing key published in this repository, so anyone could mint a token this API
    accepts for any email. It was not hypothetical: simple_jwt_api.yml had no `env:`
    block at all, so every deployed pod fell back to it while buildspec.yml put the real
    Parameter Store value in the *build* environment only.

    A random per-process key keeps `python main.py` working with no setup while making the
    published-key forgery impossible. It also fails LOUDLY rather than silently if the
    Kubernetes Secret ever stops being injected: the three replicas each mint a different
    key, so a token minted by one pod is rejected by the others and the breakage is
    obvious. The old behaviour was silent and exploitable, which is the worse pair.
    '''
    from_env = os.environ.get('JWT_SECRET')
    if from_env:
        return from_env
    generated = secrets.token_urlsafe(32)
    logging.getLogger(__name__).warning(
        "JWT_SECRET is not set; generated a random per-process key. Tokens will not "
        "survive a restart and will not validate across replicas. Set JWT_SECRET.")
    return generated


LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
JWT_SECRET = _load_secret()

# PBKDF2-SHA256 iteration count. 260,000 is what Django 3.2 used in 2021, so it is the
# period-appropriate figure rather than a number picked here.
PBKDF2_ITERATIONS = 260000

# Upper bound accepted from a STORED hash. A stored count is operator-supplied, and an
# absurd one would occupy the worker for as long as it takes rather than failing, so it is
# rejected instead of run. Ten times the current cost leaves room to raise the real figure.
MAX_PBKDF2_ITERATIONS = PBKDF2_ITERATIONS * 10

# What hash_password.py produces, and what a stored hash must therefore look like.
SALT_MIN_BYTES = 16
DIGEST_BYTES = 32


def _load_users():
    '''
    Parse AUTH_USERS: a JSON object mapping an email to a password hash.

    Hashes are `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`, which hash_password.py
    generates. No plaintext password is read from the environment, and an unset or
    malformed AUTH_USERS yields no users, so /auth answers 401 for everyone. Failing
    closed is the point: the previous version issued a valid token to any caller.
    '''
    raw = os.environ.get('AUTH_USERS')
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logging.getLogger(__name__).error("AUTH_USERS is not valid JSON; no users loaded")
        return {}
    if not isinstance(parsed, dict):
        logging.getLogger(__name__).error("AUTH_USERS must be a JSON object; no users loaded")
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _check_password(email, password):
    '''
    Verify a password against the stored PBKDF2 hash. Returns True only on a match.
    '''
    stored = _load_users().get(email)
    if not stored:
        # Still spend the KDF on a dummy hash so an unknown email and a wrong password
        # take comparable time; returning immediately here leaks which emails exist.
        hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), b'timing', PBKDF2_ITERATIONS)
        return False
    try:
        algorithm, iterations, salt_b64, expected_b64 = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            raise ValueError('unsupported algorithm: %s' % algorithm)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(expected_b64, validate=True)
        rounds = int(iterations)
        # Validated INSIDE the try, so a malformed stored hash is a 401 rather than a 500.
        # All three measured against pbkdf2_hmac, because the guarantees differ:
        #   rounds <= 0            ValueError('iteration value must be greater than 0.')
        #   rounds ~1e11           OverflowError('iteration value is too great.')
        # Both of those calls sit BELOW this block, so `$0$` or `$-1$` in AUTH_USERS
        # answered HTTP 500 before this guard existed. A large-but-representable count
        # (say 5e8) raises nothing at all and simply occupies the worker, which is the
        # reason for an upper bound rather than just a positive check.
        #
        # The salt and digest sizes are different in kind and worth being honest about:
        # they change no status code, because a digest of the wrong length already fails
        # hmac.compare_digest and answers 401. What they change is the LOG, turning a
        # silent 401 into "Stored hash for a user is malformed", which is the difference
        # between an operator seeing a broken AUTH_USERS and assuming a wrong password.
        if not 1 <= rounds <= MAX_PBKDF2_ITERATIONS:
            raise ValueError('iteration count out of range: %d' % rounds)
        if len(salt) < SALT_MIN_BYTES:
            raise ValueError('salt shorter than %d bytes' % SALT_MIN_BYTES)
        if len(expected) != DIGEST_BYTES:
            raise ValueError('digest is not %d bytes, so it is not a sha256 digest'
                             % DIGEST_BYTES)
    except (ValueError, binascii.Error):
        logging.getLogger(__name__).error("Stored hash for a user is malformed")
        return False
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
    # compare_digest, not ==, so the comparison does not short-circuit on the first
    # differing byte and leak how much of the hash matched.
    return hmac.compare_digest(candidate, expected)


def _logger():
    '''
    Setup logger format, level, and handler.

    RETURNS: log object
    '''
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    log = logging.getLogger(__name__)
    log.setLevel(LOG_LEVEL)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    log.addHandler(stream_handler)
    return log


LOG = _logger()
LOG.debug("Starting with log level: %s", LOG_LEVEL)
APP = Flask(__name__)

def _bearer_token(header_value):
    '''
    Strip exactly one leading "Bearer " prefix.

    `str.replace(str(data), 'Bearer ', '')` removed EVERY occurrence anywhere in the
    string, not just the prefix, so a token whose payload happened to contain the
    sequence was silently corrupted:

        header  : Bearer aa.Bearer b.cc
        replace : aa.b.cc            <- two characters of payload gone
        correct : aa.Bearer b.cc

    It also accepted a header with no scheme at all.
    '''
    prefix = 'Bearer '
    if not header_value.startswith(prefix):
        return None
    return header_value[len(prefix):].strip()


def require_jwt(function):
    """
    Decorator to check valid jwt is present. Stores the decoded claims on flask.g.
    """
    @functools.wraps(function)
    def decorated_function(*args, **kws):
        if 'Authorization' not in request.headers:
            abort(401)
        token = _bearer_token(request.headers['Authorization'])
        if not token:
            abort(401)
        try:
            g.jwt_claims = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        except jwt.InvalidTokenError:
            # Was a bare `except:`, which also swallowed KeyboardInterrupt and
            # SystemExit and turned any unrelated internal error into a 401. Every
            # signature, expiry, nbf and malformed-token failure is an InvalidTokenError
            # subclass, so this covers the cases that should genuinely answer 401.
            abort(401)
        return function(*args, **kws)
    return decorated_function


def _error(code, message):
    '''
    Build an error response in the shape the README documents.

    `return jsonify({"message": "..."}, 400)` does NOT set the status code. jsonify
    serialises its arguments, so a second positional argument becomes a second element
    of a JSON array and the response goes out as HTTP 200:

        missing password -> HTTP 200   [{"message":"Missing parameter: password"},400]

    Flask takes the status from the tuple returned by the view, so the code has to sit
    outside the jsonify call. Measured before the fix: all three /auth error paths
    answered 200, so a client checking response.status_code saw every one as success.
    '''
    return jsonify({"error": code, "message": message}), code


# abort() renders HTML by default, which contradicts the README's "Responses (including
# errors) are returned as JSON objects". These make the documented shape true for the
# aborts as well as for the explicit returns above.
@APP.errorhandler(400)
def _bad_request(_error_unused):
    return _error(400, "Bad request")


@APP.errorhandler(401)
def _unauthorized(_error_unused):
    return _error(401, "Unauthorized")


@APP.errorhandler(404)
def _not_found(_error_unused):
    return _error(404, "Not found")


@APP.errorhandler(405)
def _method_not_allowed(_error_unused):
    return _error(405, "Method not allowed")


@APP.route('/', methods=['POST', 'GET'])
def health():
    return jsonify({"message": "Healthy"})


@APP.route('/auth', methods=['POST'])
def auth():
    """
    Create JWT token based on email.
    """
    request_data = request.get_json(silent=True)
    if request_data is None:
        return _error(400, "Bad request")
    if not isinstance(request_data, dict):
        # A valid JSON body need not be an object: `[1,2]` or `"x"` both parse, and
        # .get() on either raises AttributeError, which Flask turns into a 500.
        return _error(400, "Bad request")
    email = request_data.get('email')
    password = request_data.get('password')
    # isinstance, not just truthiness. A truthy non-string reached _check_password and blew
    # up there: measured, a list or object email raised TypeError: unhashable type on the
    # dictionary lookup, and a non-string password raised AttributeError on .encode. All
    # four answered HTTP 500. The README documents both as strings, so this is a 400.
    if not email or not isinstance(email, str):
        LOG.error("No valid email provided")
        return _error(400, "Missing parameter: email")
    if not password or not isinstance(password, str):
        LOG.error("No valid password provided")
        return _error(400, "Missing parameter: password")

    if not _check_password(email, password):
        # Deliberately does not distinguish an unknown email from a wrong password:
        # answering differently makes the endpoint a valid-account oracle.
        LOG.warning("Failed authentication attempt")
        return _error(401, "Invalid credentials")

    return jsonify(token=_get_jwt({'email': email}).decode('utf-8'))


@APP.route('/contents', methods=['GET'])
@require_jwt
def decode_jwt():
    """
    Check user token and return non-secret data
    """
    # This route used to reimplement require_jwt inline, so the decorator above was
    # defined and never applied: two copies of the same check, one of them dead. Fixing
    # the header parsing in the decorator alone would have left this route unchanged.
    claims = g.jwt_claims
    # .get(), not claims['email']: a token signed with the right key but missing a claim
    # raised KeyError and answered 500. Any HS256 token from this key is otherwise valid,
    # so a caller could pick the status code by choosing which claim to leave out.
    return jsonify(email=claims.get('email'),
                   exp=claims.get('exp'),
                   nbf=claims.get('nbf'))


def _get_jwt(user_data):
    exp_time = datetime.datetime.utcnow() + datetime.timedelta(weeks=2)
    payload = {'exp': exp_time,
               'nbf': datetime.datetime.utcnow(),
               'email': user_data['email']}
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

if __name__ == '__main__':
    # debug=True served the Werkzeug interactive debugger, which executes arbitrary
    # Python from the browser on any unhandled exception. It is opt-in now via
    # FLASK_DEBUG=1 rather than the default, so a copy of this file that ends up
    # listening somewhere reachable does not hand out a shell.
    APP.run(host='127.0.0.1', port=8080,
            debug=os.environ.get('FLASK_DEBUG') == '1')
