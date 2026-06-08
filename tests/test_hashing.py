"""Tests for the perceptual-hash primitives: dhash + hamming (photo_saver:100,112)."""
from PIL import Image

import photo_saver as ps


def _solid(value=128):
    return Image.new("L", (9, 8), value)


def _decreasing_gradient():
    """Each row strictly decreases left→right, so every dhash bit is 1."""
    img = Image.new("L", (9, 8))
    for y in range(8):
        for x in range(9):
            img.putpixel((x, y), 255 - x * 28)
    return img


def test_dhash_is_16_hex_chars():
    h = ps.dhash(_solid())
    assert len(h) == 16
    int(h, 16)  # parses as hex


def test_dhash_solid_image_is_all_zero():
    # No left>right transitions anywhere → every bit 0.
    assert ps.dhash(_solid()) == "0000000000000000"


def test_dhash_decreasing_gradient_is_all_ones():
    # Every pixel brighter than the one to its right → every bit 1.
    assert ps.dhash(_decreasing_gradient()) == "ffffffffffffffff"


def test_dhash_is_deterministic():
    assert ps.dhash(_solid(200)) == ps.dhash(_solid(200))


def test_dhash_distinguishes_different_images():
    assert ps.dhash(_solid()) != ps.dhash(_decreasing_gradient())


def test_hamming_identical_is_zero():
    assert ps.hamming("0000000000000000", "0000000000000000") == 0
    assert ps.hamming("ffffffffffffffff", "ffffffffffffffff") == 0


def test_hamming_counts_differing_bits():
    assert ps.hamming("0000000000000000", "0000000000000001") == 1
    assert ps.hamming("0000000000000000", "0000000000000003") == 2
    assert ps.hamming("0000000000000000", "ffffffffffffffff") == 64


def test_hamming_is_symmetric():
    a, b = "00000000000000a5", "000000000000005a"
    assert ps.hamming(a, b) == ps.hamming(b, a)
