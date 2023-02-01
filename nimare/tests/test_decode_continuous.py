"""Test nimare.decode.continuous.

Tests for nimare.decode.continuous.gclda_decode_map are in test_annotate_gclda.
"""
import os

import pandas as pd
import pytest

from nimare.decode import continuous
from nimare.meta import kernel, mkda


def test_CorrelationDecoder_smoke(testdata_laird):
    """Smoke test for continuous.CorrelationDecoder."""
    testdata_laird = testdata_laird.copy()
    features = testdata_laird.get_labels(ids=testdata_laird.ids[0])[:5]
    decoder = continuous.CorrelationDecoder(features=features)
    decoder.fit(testdata_laird)

    # Make an image to decode
    meta = mkda.KDA(null_method="approximate")
    res = meta.fit(testdata_laird)
    img = res.get_map("stat")
    decoded_df = decoder.transform(img)
    assert isinstance(decoded_df, pd.DataFrame)


def test_CorrelationDistributionDecoder_smoke(testdata_laird, tmp_path_factory):
    """Smoke test for continuous.CorrelationDistributionDecoder."""
    tmpdir = tmp_path_factory.mktemp("test_CorrelationDistributionDecoder")

    testdata_laird = testdata_laird.copy()
    dset = testdata_laird.copy()
    features = testdata_laird.get_labels(ids=testdata_laird.ids[0])[:5]

    decoder = continuous.CorrelationDistributionDecoder(features=features)

    # No images of the requested type
    with pytest.raises(ValueError):
        decoder.fit(testdata_laird)

    # Let's add the path
    testdata_laird.update_path(tmpdir)

    # Then let's make some images to decode
    kern = kernel.MKDAKernel(r=10, value=1)
    kern._infer_names()  # Determine MA map filenames

    imgs = kern.transform(testdata_laird, return_type="image")
    for i_img, img in enumerate(imgs):
        id_ = testdata_laird.ids[i_img]
        out_file = os.path.join(testdata_laird.basepath, kern.filename_pattern.format(id=id_))

        # Add file names to dset.images DataFrame
        img.to_filename(out_file)
        dset.images.loc[testdata_laird.images["id"] == id_, kern.image_type] = out_file

    # And now we have images we can use for decoding!
    decoder = continuous.CorrelationDistributionDecoder(
        features=features,
        target_image=kern.image_type,
    )
    decoder.fit(dset)

    # Make an image to decode
    meta = mkda.KDA(null_method="approximate")
    res = meta.fit(testdata_laird)
    img = res.get_map("stat")
    decoded_df = decoder.transform(img)
    assert isinstance(decoded_df, pd.DataFrame)
