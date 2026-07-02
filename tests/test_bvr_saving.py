"""Tests for the client-side .bvr recorder (DataSaver), including that event
annotations are stored centrally in the file's ``Annotations`` trailer key
rather than in per-annotation sidecar files."""
import time

import numpy as np

from bioview_client.workers import DataSaver
from bvr_reader import read_bvr


def _make_sources(n):
    return [{"group_id": "DummyDevice", "channel": i, "label": f"Ch{i}"} for i in range(n)]


def _record(save_path, chunks, annotations=None, param_changes=None):
    saver = DataSaver(save_path=str(save_path), sources=_make_sources(chunks[0].shape[0]))
    saver.start_saving()
    for chunk in chunks:
        saver.add(chunk)
    # Give the writer thread a moment to drain the queue.
    time.sleep(0.2)
    for text in annotations or []:
        saver.record_annotation(text)
    for dev, param, value in param_changes or []:
        saver.record_change(dev, param, value)
    saver.stop_saving()
    saver.join(timeout=5)


def test_bvr_header_and_samples(tmp_path):
    path = tmp_path / "rec.bvr"
    chunks = [np.ones((3, 10), dtype=np.float32) * i for i in range(1, 4)]
    _record(path, chunks)

    header, samples, trailer = read_bvr(path)
    assert header["format"] == "bioview-raw-v2"
    assert header["num_sources"] == 3
    # 3 chunks x 10 samples each, stored time-major (num_samples, num_sources)
    assert samples.shape == (30, 3)
    assert trailer is not None
    assert "end_time" in trailer


def test_annotations_stored_centrally_in_trailer(tmp_path):
    path = tmp_path / "rec.bvr"
    chunks = [np.zeros((2, 5), dtype=np.float32)]
    _record(
        path,
        chunks,
        annotations=["stimulus on", "subject moved"],
        param_changes=[("DummyDevice", "amplitude", 2.0)],
    )

    _header, _samples, trailer = read_bvr(path)
    assert "Annotations" in trailer
    texts = [a["text"] for a in trailer["Annotations"]]
    assert texts == ["stimulus on", "subject moved"]
    # Each annotation carries a timestamp and elapsed offset.
    for entry in trailer["Annotations"]:
        assert "timestamp" in entry
        assert "elapsed_seconds" in entry

    # Parameter changes remain in their own key alongside annotations.
    assert trailer["param_changes"][0]["param"] == "amplitude"


def test_no_sidecar_files_created(tmp_path):
    path = tmp_path / "rec.bvr"
    _record(path, [np.zeros((1, 4), dtype=np.float32)], annotations=["note"])

    # The only artifact should be the single .bvr file -- no per-annotation files.
    files = list(tmp_path.iterdir())
    assert files == [path]
