import hashlib

from bioview_common import SHARED_SECRET


def get_challenge_response(challenge):
    return hashlib.sha256((challenge + SHARED_SECRET).encode()).hexdigest()
