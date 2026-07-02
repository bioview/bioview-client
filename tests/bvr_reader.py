"""A minimal reader for the client's ``bioview-raw-v2`` (.bvr) files, used by the
tests to verify what ``DataSaver`` writes (header, samples, and the metadata
trailer -- including the ``Annotations`` key)."""
import json
import struct

import numpy as np

from bioview_client.workers import BVR_TRAILER_MAGIC


def read_bvr(path):
    """Parse a .bvr file into (header, samples, trailer).

    ``samples`` is a (num_samples, num_sources) float32 array (time-major, as
    written). ``trailer`` is None if the file has no metadata trailer.
    """
    with open(path, "rb") as f:
        blob = f.read()

    (header_len,) = struct.unpack("!I", blob[:4])
    header = json.loads(blob[4 : 4 + header_len].decode("utf-8"))
    body_start = 4 + header_len

    trailer = None
    sample_end = len(blob)
    magic_len = len(BVR_TRAILER_MAGIC)
    if len(blob) >= body_start + magic_len + 8 and blob[-magic_len:] == BVR_TRAILER_MAGIC:
        (trailer_len,) = struct.unpack("!Q", blob[-magic_len - 8 : -magic_len])
        trailer_start = len(blob) - magic_len - 8 - trailer_len
        trailer = json.loads(blob[trailer_start : trailer_start + trailer_len].decode("utf-8"))
        sample_end = trailer_start

    raw = blob[body_start:sample_end]
    num_sources = int(header.get("num_sources", 0)) or 1
    samples = np.frombuffer(raw, dtype=np.float32)
    if samples.size and num_sources:
        samples = samples.reshape(-1, num_sources)
    return header, samples, trailer
