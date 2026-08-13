#!/usr/bin/env python
"""
Generate an AUTH_USERS entry for main.py.

    python hash_password.py user@example.com
    Password: (typed, not echoed)

    AUTH_USERS='{"user@example.com": "pbkdf2_sha256$260000$...$..."}'

The password is read from a prompt rather than argv, so it does not land in the shell
history or in the process list where `ps` would show it. Nothing here writes to disk.
"""
import base64
import getpass
import hashlib
import json
import os
import sys

# Kept in step with main.py's PBKDF2_ITERATIONS.
ITERATIONS = 260000
SALT_BYTES = 16


def hash_password(password, salt=None, iterations=ITERATIONS):
    '''
    Return `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`, the format main.py parses.
    '''
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2_sha256$%d$%s$%s' % (
        iterations,
        base64.b64encode(salt).decode('ascii'),
        base64.b64encode(digest).decode('ascii'))


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    email = argv[1]
    password = getpass.getpass('Password: ')
    if not password:
        print('error: empty password', file=sys.stderr)
        return 1
    if password != getpass.getpass('Confirm: '):
        print('error: passwords do not match', file=sys.stderr)
        return 1
    entry = {email: hash_password(password)}
    print("\nAUTH_USERS='%s'" % json.dumps(entry))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
