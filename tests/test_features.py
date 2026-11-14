import time

import numpy as np
import pytest
import soundfile as sf

from app.services.features import FeatureCache, MFCCExtractor, get_extractor
from tests.conftest import SR, tone


def test_mfcc_shape_and_cmvn():
    feats = MFCCExtractor().extract(tone(1.0), SR)
    assert feats.ndim == 2 and feats.shape[1] == 39
    assert feats.dtype == np.float32
    np.testing.assert_allclose(feats.mean(axis=0), 0, atol=1e-4)
    np.testing.assert_allclose(feats.std(axis=0), 1, atol=1e-2)


def test_frame_count_scales_with_length():
    ext = MFCCExtractor()
    short, long = ext.extract(tone(0.5), SR), ext.extract(tone(2.0), SR)
    assert long.shape[0] > 3 * short.shape[0]


def test_very_short_clip_still_works():
    # MIN_CLIP_SEC is 0.3s; make sure deltas don't fail on few frames.
    feats = MFCCExtractor().extract(tone(0.3), SR)
    assert feats.shape[1] == 39 and np.isfinite(feats).all()


def test_no_deltas_variant():
    ext = MFCCExtractor(include_deltas=False)
    assert ext.extract(tone(1.0), SR).shape[1] == 13
    assert ext.name != MFCCExtractor().name


def test_get_extractor():
    assert isinstance(get_extractor("mfcc"), MFCCExtractor)
    assert isinstance(get_extractor("MFCC"), MFCCExtractor)
    with pytest.raises(ValueError):
        get_extractor("nope")


@pytest.fixture
def cache_and_wav(tmp_path):
    wav = tmp_path / "s.wav"
    sf.write(wav, tone(1.0), SR)
    return FeatureCache(tmp_path / "data", MFCCExtractor()), wav


def test_cache_hit_skips_extraction(cache_and_wav, monkeypatch):
    cache, wav = cache_and_wav
    first = cache.get_or_extract("p1", "s1", wav)
    assert cache.path_for("p1", "s1").exists()
    assert "mfcc_13_deltas" in str(cache.path_for("p1", "s1"))

    def boom(*a, **k):
        raise AssertionError("should have used the cache")

    monkeypatch.setattr(cache.extractor, "extract", boom)
    np.testing.assert_array_equal(cache.get_or_extract("p1", "s1", wav), first)


def test_invalidate_forces_reextract(cache_and_wav):
    cache, wav = cache_and_wav
    cache.get_or_extract("p1", "s1", wav)
    cache.invalidate("p1", "s1")
    assert not cache.path_for("p1", "s1").exists()
    cache.get_or_extract("p1", "s1", wav)
    assert cache.path_for("p1", "s1").exists()


def test_invalidate_phrase_covers_all_extractors(cache_and_wav):
    cache, wav = cache_and_wav
    other = FeatureCache(cache.root.parent, MFCCExtractor(include_deltas=False))
    cache.get_or_extract("p1", "s1", wav)
    other.get_or_extract("p1", "s1", wav)
    cache.invalidate_phrase("p1")
    assert not cache.path_for("p1", "s1").exists()
    assert not other.path_for("p1", "s1").exists()


def test_extraction_is_fast():
    ext, audio = MFCCExtractor(), tone(3.0)
    ext.extract(audio, SR)  # warm up
    start = time.perf_counter()
    ext.extract(audio, SR)
    assert time.perf_counter() - start < 0.1
