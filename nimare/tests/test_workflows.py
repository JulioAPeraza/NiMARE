"""Test nimare.workflows."""
import os.path as op

import nimare
from nimare import cli, workflows
from nimare.correct import FWECorrector
from nimare.diagnostics import Jackknife
from nimare.meta.cbma.ale import ALE
from nimare.tests.utils import get_test_data_path


def test_ale_workflow_function_smoke(tmp_path_factory):
    """Run smoke test for Sleuth ALE workflow."""
    tmpdir = tmp_path_factory.mktemp("test_ale_workflow_function_smoke")
    sleuth_file = op.join(get_test_data_path(), "test_sleuth_file.txt")
    prefix = "test"

    # The same test is run with both workflow function and CLI
    workflows.ale_sleuth_workflow(
        sleuth_file, output_dir=tmpdir, prefix=prefix, n_iters=10, n_cores=1
    )
    assert op.isfile(op.join(tmpdir, f"{prefix}_input_coordinates.txt"))


def test_ale_workflow_cli_smoke(tmp_path_factory):
    """Run smoke test for Sleuth ALE workflow."""
    tmpdir = tmp_path_factory.mktemp("test_ale_workflow_cli_smoke")
    sleuth_file = op.join(get_test_data_path(), "test_sleuth_file.txt")
    prefix = "test"

    cli._main(
        [
            "ale",
            "--output_dir",
            str(tmpdir),
            "--prefix",
            prefix,
            "--n_iters",
            "10",
            "--n_cores",
            "1",
            sleuth_file,
        ]
    )
    assert op.isfile(op.join(tmpdir, f"{prefix}_input_coordinates.txt"))


def test_ale_workflow_function_smoke_2(tmp_path_factory):
    """Run smoke test for Sleuth ALE workflow with subtraction analysis."""
    tmpdir = tmp_path_factory.mktemp("test_ale_workflow_function_smoke_2")
    sleuth_file = op.join(get_test_data_path(), "test_sleuth_file.txt")
    prefix = "test"

    # The same test is run with both workflow function and CLI
    workflows.ale_sleuth_workflow(
        sleuth_file,
        sleuth_file2=sleuth_file,
        output_dir=tmpdir,
        prefix=prefix,
        n_iters=10,
        n_cores=1,
    )
    assert op.isfile(op.join(tmpdir, f"{prefix}_group2_input_coordinates.txt"))


def test_ale_workflow_cli_smoke_2(tmp_path_factory):
    """Run smoke test for Sleuth ALE workflow with subtraction analysis."""
    tmpdir = tmp_path_factory.mktemp("test_ale_workflow_cli_smoke_2")
    sleuth_file = op.join(get_test_data_path(), "test_sleuth_file.txt")
    prefix = "test"
    cli._main(
        [
            "ale",
            "--output_dir",
            str(tmpdir),
            "--prefix",
            prefix,
            "--n_iters",
            "10",
            "--n_cores",
            "1",
            "--file2",
            sleuth_file,
            sleuth_file,
        ]
    )
    assert op.isfile(op.join(tmpdir, f"{prefix}_group2_input_coordinates.txt"))


def test_cbma_workflow_function_smoke(tmp_path_factory, testdata_cbma_full):
    """Run smoke test for CBMA workflow."""
    tmpdir = tmp_path_factory.mktemp("test_cbma_workflow_function_smoke")

    # Initialize estimator, corrector and diagnostic classes
    est = ALE(null_method="approximate")
    corr = FWECorrector(method="montecarlo", n_iters=100)
    diag = Jackknife()

    cres = workflows.cbma_workflow(
        testdata_cbma_full,
        meta_estimator=est,
        corrector=corr,
        diagnostics=(diag,),
        output_dir=tmpdir,
    )

    assert isinstance(cres, nimare.results.MetaResult)

    assert "z_desc-mass_level-cluster_corr-FWE_method-montecarlo_clust" in cres.tables.keys()
    assert "z_desc-size_level-cluster_corr-FWE_method-montecarlo_clust" in cres.tables.keys()
    assert "z_level-voxel_corr-FWE_method-montecarlo_clust" in cres.tables.keys()
    assert "z_desc-mass_level-cluster_corr-FWE_method-montecarlo_Jackknife" in cres.tables.keys()
    assert "z_desc-size_level-cluster_corr-FWE_method-montecarlo_Jackknife" in cres.tables.keys()
    assert "z_level-voxel_corr-FWE_method-montecarlo_Jackknife" in cres.tables.keys()

    for imgtype in cres.maps.keys():
        filename = imgtype + ".nii.gz"
        outpath = op.join(tmpdir, filename)
        assert op.isfile(outpath)

    for tabletype in cres.tables.keys():
        filename = tabletype + ".tsv"
        outpath = op.join(tmpdir, filename)
        assert op.isfile(outpath)
