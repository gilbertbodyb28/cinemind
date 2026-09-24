import hashlib
import json

import pytest

from evaluation.labeled_dataset import model_row, validate


def _dataset():
    payload = {"items": [{
        "id": "tmdb:1", "title": "Secret title", "year": 2027,
        "groups": ["upcoming_movie"], "origins": ["watched_history"],
        "synopsis": "A test synopsis", "genres": ["Drama"], "rating": 9,
    }]}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["dataset_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def test_labels_require_frozen_hash_and_known_ids():
    data = _dataset()
    labels = {"dataset_sha256": data["dataset_sha256"], "labels": {
        "tmdb:1": {"score": 3, "source": "gilbert_explicit"}
    }}
    assert validate(data, labels)["labeled_unique"] == 1
    data["items"][0]["synopsis"] = "Changed after rating"
    with pytest.raises(ValueError, match="hash mismatch"):
        validate(data, labels)


def test_model_metadata_excludes_original_id_and_behavior():
    row = model_row(_dataset()["items"][0], "c001", mask_identity=True)
    encoded = json.dumps(row)
    assert row["title"] == "[title withheld]"
    assert "tmdb:1" not in encoded
    assert "watched_history" not in encoded
    assert "rating" not in encoded


def test_personal_rating_cannot_be_added_without_trusted_source():
    data = _dataset()
    labels = {"dataset_sha256": data["dataset_sha256"], "labels": {
        "tmdb:1": {"score": 3, "source": "personal_rating_9plus"}
    }}
    initial = {"dataset_sha256": data["dataset_sha256"], "labels": {}}
    with pytest.raises(ValueError, match="provenance changed"):
        validate(data, labels, initial)
