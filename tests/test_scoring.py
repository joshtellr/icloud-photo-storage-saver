"""Tests for the keep-the-best ranking + tightness helpers
(photo_saver:251 score_photo, 269 cluster_tightness, 288 tightness_label)."""
import photo_saver as ps


def test_score_is_filesize_by_default(make_photo):
    assert ps.score_photo(make_photo("a", original_filesize=1234)) == 1234


def test_score_handles_missing_filesize(make_photo):
    assert ps.score_photo(make_photo("a", original_filesize=None)) == 0


def test_favorite_outranks_any_filesize(make_photo):
    fav = make_photo("fav", original_filesize=1, favorite=True)
    huge = make_photo("huge", original_filesize=10 ** 11)  # below the 10**12 favorite bonus
    assert ps.score_photo(fav) > ps.score_photo(huge)


def test_local_original_outranks_icloud_only_of_same_size(tmp_path, make_photo):
    local_file = tmp_path / "orig.jpg"
    local_file.write_bytes(b"x")
    local = make_photo("local", original_filesize=500, path=str(local_file))
    cloud = make_photo("cloud", original_filesize=500, path=None)
    assert ps.score_photo(local) > ps.score_photo(cloud)


def test_nonexistent_path_gets_no_local_bonus(make_photo):
    ghost = make_photo("ghost", original_filesize=500, path="/no/such/file.jpg")
    cloud = make_photo("cloud", original_filesize=500, path=None)
    assert ps.score_photo(ghost) == ps.score_photo(cloud)


def test_cluster_tightness_identical_is_zero():
    hashes = {"a": "00000000000000ff", "b": "00000000000000ff"}
    assert ps.cluster_tightness(["a", "b"], hashes) == 0


def test_cluster_tightness_is_max_pairwise_distance():
    hashes = {
        "a": "0000000000000000",
        "b": "0000000000000001",  # 1 from a
        "c": "0000000000000007",  # 3 from a, 2 from b
    }
    assert ps.cluster_tightness(["a", "b", "c"], hashes) == 3


def test_cluster_tightness_none_when_too_few_hashes():
    assert ps.cluster_tightness(["a"], {"a": "0000000000000000"}) is None
    assert ps.cluster_tightness(["a", "b"], {"a": "0000000000000000"}) is None


def test_tightness_label_boundaries():
    assert ps.tightness_label(None) == "similar"
    assert ps.tightness_label(0) == "identical"
    assert ps.tightness_label(4) == "near-identical"
    assert ps.tightness_label(5) == "similar"
    assert ps.tightness_label(8) == "similar"
    assert ps.tightness_label(9) == "loose"
