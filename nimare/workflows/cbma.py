"""Workflow for running an coordinates-based meta-analysis from a NiMARE database."""
import inspect
import itertools
import logging
import os.path as op

from nimare.correct import Corrector, FWECorrector
from nimare.dataset import Dataset
from nimare.diagnostics import Diagnostics, Jackknife
from nimare.meta import ALE
from nimare.meta.cbma.base import CBMAEstimator, PairwiseCBMAEstimator
from nimare.utils import _check_type

LGR = logging.getLogger(__name__)


def cbma_workflow(
    dataset,
    estimator=None,
    corrector=FWECorrector(),
    diagnostics=(Jackknife(),),
    output_dir=None,
):
    """Compose a coordinate-based meta-analysis workflow.

    .. versionadded:: 0.0.14

    This workflow performs a coordinate-based meta-analysis, multiple comparison correction,
    and diagnostics analyses on corrected meta-analytic maps.

    Parameters
    ----------
    dataset : :obj:`~nimare.dataset.Dataset`
        Dataset for which to run meta-analyses to generate maps.
    estimator : :class:`~nimare.base.CBMAEstimator`, :obj:`str` {'ale', 'scale', 'mkdadensity',
    'kda'}, or optional
        Meta-analysis estimator. Default is :class:`~nimare.meta.cbma.ale.ALE`.
    corrector : :class:`~nimare.correct.Corrector`, :obj:`str` {'montecarlo', 'fdr', 'bonferroni'}
    or optional
        Meta-analysis corrector. Default is :class:`~nimare.correct.FWECorrector`.
    diagnostics : :obj:`tuple`, :obj:`str` {'jackknife', 'focusCounter'}, or optional
        Tuple of meta-analysis diagnostic classes.
        Default is :class:`~nimare.diagnostics.FocusCounter`.
    output_dir : :obj:`str`, optional
        Output directory in which to save results. If the directory doesn't
        exist, it will be created. Default is None (the results are not saved).

    Returns
    -------
    :obj:`~nimare.results.MetaResult`
        Results of Estimator and Corrector fitting with cluster and diagnostic tables.
    """
    # Check dataset type
    dataset = _check_type(dataset, Dataset)

    # Options allows for string input
    estm_options = ("ale", "scale", "mkdadensity", "kda")
    corr_options = ("montecarlo", "fdr", "bonferroni")
    diag_options = ("jackknife", "focuscounter")

    # Check estimator type
    if estimator is None:
        estimator = ALE()
    elif inspect.isclass(estimator):
        estimator = _check_type(estimator, CBMAEstimator)
    elif isinstance(estimator, str):
        if estimator not in estm_options:
            raise ValueError(f'"estimator" of kind string must be {", ".join(estm_options)}')
    else:
        raise ValueError(
            f'"estimator" is {type(estimator)}, it must be a kind of CBMAEstimator, or a string.'
        )

    # Check corrector type
    if inspect.isclass(corrector):
        corrector = _check_type(corrector, Corrector)
    elif isinstance(corrector, str):
        if corrector not in corr_options:
            raise ValueError(f'"corrector" of kind string must be {", ".join(corr_options)}')
    else:
        raise ValueError(
            f'"corrector" is {type(corrector)}, it must be a kind of Corrector, or a string.'
        )

    # Check diagnostics type
    if inspect.isclass(diagnostics):
        diagnostics = _check_type(diagnostics, Diagnostics)
    elif isinstance(diagnostics, str):
        if diagnostics not in diag_options:
            raise ValueError(f'"diagnostics" of kind string must be {", ".join(diag_options)}')
    else:
        raise ValueError(
            f'"diagnostics" is {type(diagnostics)}, it must be a kind of Diagnostics, or a string.'
        )

    if isinstance(estimator, PairwiseCBMAEstimator):
        raise AttributeError(
            "The cbma_workflow function does not currently work with pairwise Estimators."
        )

    LGR.info("Performing meta-analysis...")
    results = estimator.fit(dataset)

    LGR.info("Performing correction on meta-analysis...")
    corr_results = corrector.transform(results)

    LGR.info("Generating clusters tables and performing diagnostics on corrected meta-analyses...")
    img_keys = [
        img_key
        for img_key in corr_results.maps.keys()
        if img_key.startswith("z_") and ("_corr-" in img_key)
    ]
    for img_key, diagnostic in itertools.product(img_keys, diagnostics):
        diagnostic.target_image = img_key
        contribution_table, clusters_table, _ = diagnostic.transform(corr_results)

        diag_name = diagnostic.__class__.__name__
        clust_tbl_name = f"{img_key}_clust"
        count_tbl_name = f"{img_key}_{diag_name}"
        if not contribution_table.empty:
            corr_results.tables[clust_tbl_name] = clusters_table
            corr_results.tables[count_tbl_name] = contribution_table
        else:
            LGR.warn(
                f"Key {count_tbl_name} and {clust_tbl_name} will not be stored in "
                "MetaResult.tables dictionary."
            )

    if output_dir is not None:
        LGR.info(f"Saving meta-analytic maps, tables and boilerplate to {output_dir}...")
        corr_results.save_maps(output_dir=output_dir)
        corr_results.save_tables(output_dir=output_dir)

        boilerplate = corr_results.description_
        with open(op.join(output_dir, "boilerplate.txt"), "w") as fo:
            fo.write(boilerplate)

        bibtex = corr_results.bibtex_
        with open(op.join(output_dir, "references.bib"), "w") as fo:
            fo.write(bibtex)

    LGR.info("Workflow completed.")
    return corr_results
