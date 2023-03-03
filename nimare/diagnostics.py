"""Methods for diagnosing problems in meta-analytic datasets or analyses."""
import copy
import logging

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from nibabel.funcs import squeeze_image
from nilearn import input_data, reporting
from scipy.spatial.distance import cdist
from tqdm.auto import tqdm

from nimare.base import NiMAREBase
from nimare.utils import (
    _check_ncores,
    _get_clusters_table,
    get_masker,
    mm2vox,
    tqdm_joblib,
)

LGR = logging.getLogger(__name__)


class Jackknife(NiMAREBase):
    """Run a jackknife analysis on a meta-analysis result.

    .. versionchanged:: 0.0.14

        Return clusters table.

    .. versionchanged:: 0.0.13

        Change cluster neighborhood from faces+edges to faces, to match Nilearn.

    .. versionadded:: 0.0.11

    Parameters
    ----------
    target_image : :obj:`str`, optional
        The meta-analytic map for which clusters will be characterized.
        The default is z because log-p will not always have value of zero for non-cluster voxels.
    voxel_thresh : :obj:`float` or None, optional
        An optional voxel-level threshold that may be applied to the ``target_image`` to define
        clusters. This can be None if the ``target_image`` is already thresholded
        (e.g., a cluster-level corrected map).
        Default is None.
    n_cores : :obj:`int`, optional
        Number of cores to use for parallelization.
        If <=0, defaults to using all available cores.
        Default is 1.

    Notes
    -----
    This analysis characterizes the relative contribution of each experiment in a meta-analysis
    to the resulting clusters by looping through experiments, calculating the Estimator's summary
    statistic for all experiments *except* the target experiment, dividing the resulting test
    summary statistics by the summary statistics from the original meta-analysis, and finally
    averaging the resulting proportion values across all voxels in each cluster.

    Warnings
    --------
    Pairwise meta-analyses, like ALESubtraction and MKDAChi2, are not yet supported in this
    method.
    """

    def __init__(
        self,
        target_image="z_desc-size_level-cluster_corr-FWE_method-montecarlo",
        voxel_thresh=None,
        n_cores=1,
    ):
        self.target_image = target_image
        self.voxel_thresh = voxel_thresh
        self.n_cores = _check_ncores(n_cores)

    def transform(self, result):
        """Apply the analysis to a MetaResult.

        Parameters
        ----------
        result : :obj:`~nimare.results.MetaResult`
            A MetaResult produced by a coordinate- or image-based meta-analysis.

        Returns
        -------
        contribution_table : :obj:`pandas.DataFrame`
            A DataFrame with information about relative contributions of each experiment to each
            cluster in the thresholded map.
            There is one row for each cluster, with row names being integers indicating the
            cluster's associated value in the ``labeled_cluster_img`` output.
            There is one column for each experiment.
        clusters_table : :obj:`pandas.DataFrame`
            A DataFrame with information about each cluster.
            There is one row for each cluster.
            The columns in this table include: ``Cluster ID`` (cluster number), ``X``/``Y``/``Z``
            (coordinate for the center of mass), ``Max Stat`` (statistical value of the peak),
            and ``Cluster Size (mm3)`` (the size of the cluster, in cubic millimeters).
        labeled_cluster_img : :obj:`nibabel.nifti1.Nifti1Image`
            The labeled, thresholded map that is used to identify clusters characterized by this
            analysis.
            Each cluster in the map has a single value, which corresponds to the cluster's column
            name in ``contribution_table``.
        """
        if not hasattr(result.estimator, "dataset"):
            raise AttributeError(
                "MetaResult was not generated by an Estimator with a `dataset` attribute. "
                "This may be because the Estimator was a pairwise Estimator. "
                "The Jackknife method does not currently work with pairwise Estimators."
            )
        dset = result.estimator.dataset
        # We need to copy the estimator because it will otherwise overwrite the original version
        # with one missing a study in its inputs.
        estimator = copy.deepcopy(result.estimator)
        original_masker = estimator.masker

        # Collect the thresholded cluster map
        if self.target_image in result.maps:
            target_img = result.get_map(self.target_image, return_type="image")
        else:
            available_maps = [f"'{m}'" for m in result.maps.keys()]
            raise ValueError(
                f"Target image ('{self.target_image}') not present in result. "
                f"Available maps in result are: {', '.join(available_maps)}."
            )

        # CBMAs have "stat" maps, while most IBMAs have "est" maps.
        # Fisher's and Stouffer's only have "z" maps though.
        if "est" in result.maps:
            target_value_map = "est"
        elif "stat" in result.maps:
            target_value_map = "stat"
        else:
            target_value_map = "z"

        stat_values = result.get_map(target_value_map, return_type="array")

        # Use study IDs in inputs_ instead of dataset, because we don't want to try fitting the
        # estimator to a study that might have been filtered out by the estimator's criteria.
        meta_ids = estimator.inputs_["id"]
        rows = list(meta_ids)

        # Get clusters table
        two_sided = (target_img.get_fdata() < 0).any()
        stat_threshold = self.voxel_thresh or 0
        clusters_table, label_maps = _get_clusters_table(
            target_img, stat_threshold, two_sided=two_sided, return_label_maps=True
        )

        if clusters_table.shape[0] == 0:
            LGR.warning("No clusters found")
            contribution_table = pd.DataFrame(columns=["id"], data=rows)
            return contribution_table, clusters_table, label_maps

        contribution_tables = []
        signs = [1, -1] if len(label_maps) == 2 else [1]
        for sign, label_map in zip(signs, label_maps):
            cluster_ids = sorted(list(np.unique(label_map.get_fdata())[1:]))

            # Create contribution table
            col_name = "PosTail" if sign == 1 else "NegTail"
            cols = [f"{col_name} {int(c_id)}" for c_id in cluster_ids]
            contribution_table = pd.DataFrame(index=rows, columns=cols)
            contribution_table.index.name = "id"

            # Mask using a labels masker, so that we can easily get the mean value for each cluster
            cluster_masker = input_data.NiftiLabelsMasker(label_map)
            cluster_masker.fit(label_map)

            with tqdm_joblib(tqdm(total=len(meta_ids))):
                jackknife_results = Parallel(n_jobs=self.n_cores)(
                    delayed(self._transform)(
                        study_id,
                        all_ids=meta_ids,
                        dset=dset,
                        estimator=estimator,
                        target_value_map=target_value_map,
                        stat_values=stat_values,
                        original_masker=original_masker,
                        cluster_masker=cluster_masker,
                    )
                    for study_id in meta_ids
                )

            # Add the results to the table
            for expid, stat_prop_values in jackknife_results:
                contribution_table.loc[expid] = stat_prop_values.flatten()

            contribution_tables.append(contribution_table.reset_index())

        contribution_table = pd.concat(contribution_tables, ignore_index=True, sort=False)

        return contribution_table, clusters_table, label_maps

    def _transform(
        self,
        expid,
        all_ids,
        dset,
        estimator,
        target_value_map,
        stat_values,
        original_masker,
        cluster_masker,
    ):
        estimator = copy.deepcopy(estimator)

        # Fit Estimator to all studies except the target study
        other_ids = [id_ for id_ in all_ids if id_ != expid]
        temp_dset = dset.slice(other_ids)
        temp_result = estimator.fit(temp_dset)

        # Collect the target values (e.g., ALE values) from the N-1 meta-analysis
        temp_stat_img = temp_result.get_map(target_value_map, return_type="image")
        temp_stat_vals = np.squeeze(original_masker.transform(temp_stat_img))

        # Voxelwise proportional reduction of each statistic after removal of the experiment
        with np.errstate(divide="ignore", invalid="ignore"):
            prop_values = np.true_divide(temp_stat_vals, stat_values)
            prop_values = np.nan_to_num(prop_values)

        voxelwise_stat_prop_values = 1 - prop_values

        # Now get the cluster-wise mean of the proportion values
        # pending resolution of https://github.com/nilearn/nilearn/issues/2724
        try:
            stat_prop_img = original_masker.inverse_transform(voxelwise_stat_prop_values)
        except IndexError:
            stat_prop_img = squeeze_image(
                original_masker.inverse_transform([voxelwise_stat_prop_values])
            )

        stat_prop_values = cluster_masker.transform(stat_prop_img)
        return expid, stat_prop_values


class FocusCounter(NiMAREBase):
    """Run a focus-count analysis on a coordinate-based meta-analysis result.

    .. versionchanged:: 0.0.14

        Return clusters table.

    .. versionchanged:: 0.0.13

        Change cluster neighborhood from faces+edges to faces, to match Nilearn.

    .. versionadded:: 0.0.12

    Parameters
    ----------
    target_image : :obj:`str`, optional
        The meta-analytic map for which clusters will be characterized.
        The default is z because log-p will not always have value of zero for non-cluster voxels.
    voxel_thresh : :obj:`float` or None, optional
        An optional voxel-level threshold that may be applied to the ``target_image`` to define
        clusters. This can be None if the ``target_image`` is already thresholded
        (e.g., a cluster-level corrected map).
        Default is None.
    n_cores : :obj:`int`, optional
        Number of cores to use for parallelization.
        If <=0, defaults to using all available cores.
        Default is 1.

    Notes
    -----
    This analysis characterizes the relative contribution of each experiment in a meta-analysis
    to the resulting clusters by counting the number of peaks from each experiment that fall within
    each significant cluster.

    Warnings
    --------
    This method only works for coordinate-based meta-analyses.

    Pairwise meta-analyses, like ALESubtraction and MKDAChi2, are not yet supported in this
    method.
    """

    def __init__(
        self,
        target_image="z_desc-size_level-cluster_corr-FWE_method-montecarlo",
        voxel_thresh=None,
        n_cores=1,
    ):
        self.target_image = target_image
        self.voxel_thresh = voxel_thresh
        self.n_cores = _check_ncores(n_cores)

    def transform(self, result):
        """Apply the analysis to a MetaResult.

        Parameters
        ----------
        result : :obj:`~nimare.results.MetaResult`
            A MetaResult produced by a coordinate- or image-based meta-analysis.

        Returns
        -------
        contribution_table : :obj:`pandas.DataFrame`
            A DataFrame with information about relative contributions of each experiment to each
            cluster in the thresholded map.
            There is one row for each cluster, with row names being integers indicating the
            cluster's associated value in the ``labeled_cluster_img`` output.
            There is one column for each experiment.
        clusters_table : :obj:`pandas.DataFrame`
            A DataFrame with information about each cluster.
            There is one row for each cluster.
            The columns in this table include: ``Cluster ID`` (cluster number), ``X``/``Y``/``Z``
            (coordinate for the center of mass), ``Max Stat`` (statistical value of the peak),
            and ``Cluster Size (mm3)`` (the size of the cluster, in cubic millimeters).
        labeled_cluster_img : :obj:`nibabel.nifti1.Nifti1Image`
            The labeled, thresholded map that is used to identify clusters characterized by this
            analysis.
            Each cluster in the map has a single value, which corresponds to the cluster's column
            name in ``contribution_table``.
        """
        if not hasattr(result.estimator, "dataset"):
            raise AttributeError(
                "MetaResult was not generated by an Estimator with a `dataset` attribute. "
                "This may be because the Estimator was a pairwise Estimator. "
                "The Jackknife method does not currently work with pairwise Estimators."
            )

        # We need to copy the estimator because it will otherwise overwrite the original version
        # with one missing a study in its inputs.
        estimator = copy.deepcopy(result.estimator)

        # Collect the thresholded cluster map
        if self.target_image in result.maps:
            target_img = result.get_map(self.target_image, return_type="image")
        else:
            available_maps = [f"'{m}'" for m in result.maps.keys()]
            raise ValueError(
                f"Target image ('{self.target_image}') not present in result. "
                f"Available maps in result are: {', '.join(available_maps)}."
            )

        # Get clusters table
        stat_threshold = self.voxel_thresh or 0
        clusters_table = reporting.get_clusters_table(target_img, stat_threshold)

        # Use study IDs in inputs_ instead of dataset, because we don't want to try fitting the
        # estimator to a study that might have been filtered out by the estimator's criteria.
        meta_ids = estimator.inputs_["id"]
        rows = list(meta_ids)

        # Get clusters table
        two_sided = (target_img.get_fdata() < 0).any()
        stat_threshold = self.voxel_thresh or 0
        clusters_table, label_maps = _get_clusters_table(
            target_img, stat_threshold, two_sided=two_sided, return_label_maps=True
        )

        if clusters_table.shape[0] == 0:
            LGR.warning("No clusters found")
            contribution_table = pd.DataFrame(columns=["id"], data=rows)
            return contribution_table, clusters_table, label_maps

        contribution_tables = []
        signs = [1, -1] if len(label_maps) == 2 else [1]
        for sign, label_map in zip(signs, label_maps):
            label_arr = label_map.get_fdata()
            cluster_ids = sorted(list(np.unique(label_arr)[1:]))

            # Create contribution table
            col_name = "PosTail" if sign == 1 else "NegTail"
            cols = [f"{col_name} {int(c_id)}" for c_id in cluster_ids]
            contribution_table = pd.DataFrame(index=rows, columns=cols)
            contribution_table.index.name = "id"

            with tqdm_joblib(tqdm(total=len(meta_ids))):
                jackknife_results = Parallel(n_jobs=self.n_cores)(
                    delayed(self._transform)(
                        study_id,
                        coordinates_df=estimator.inputs_["coordinates"],
                        labeled_cluster_map=label_arr,
                        affine=target_img.affine,
                    )
                    for study_id in meta_ids
                )

            # Add the results to the table
            for expid, focus_counts in jackknife_results:
                contribution_table.loc[expid] = focus_counts

            contribution_tables.append(contribution_table.reset_index())

        contribution_table = pd.concat(contribution_tables, ignore_index=True, sort=False)

        return contribution_table, clusters_table, label_maps

    def _transform(self, expid, coordinates_df, labeled_cluster_map, affine):
        coords = coordinates_df.loc[coordinates_df["id"] == expid]
        ijk = mm2vox(coords[["x", "y", "z"]], affine)

        clust_ids = sorted(list(np.unique(labeled_cluster_map)[1:]))
        focus_counts = []

        for c_val in clust_ids:
            cluster_mask = labeled_cluster_map == c_val
            cluster_idx = np.vstack(np.where(cluster_mask))
            distances = cdist(cluster_idx.T, ijk)
            distances = distances < 1
            distances = np.any(distances, axis=0)
            n_included_voxels = np.sum(distances)
            focus_counts.append(n_included_voxels)

        return expid, focus_counts


class FocusFilter(NiMAREBase):
    """Remove coordinates outside of the Dataset's mask from the Dataset.

    .. versionadded:: 0.0.13

    Parameters
    ----------
    mask : :obj:`str`, :class:`~nibabel.nifti1.Nifti1Image`, \
    :class:`~nilearn.maskers.NiftiMasker` or similar, or None, optional
        Mask(er) to use. If None, uses the masker of the Dataset provided in ``transform``.

    Notes
    -----
    This filter removes any coordinates outside of the brain mask.
    It does not remove studies without coordinates in the brain mask, since a Dataset does not
    need to have coordinates for all studies (e.g., some may only have images).
    """

    def __init__(self, mask=None):
        if mask is not None:
            mask = get_masker(mask)

        self.masker = mask

    def transform(self, dataset):
        """Apply the filter to a Dataset.

        Parameters
        ----------
        dataset : :obj:`~nimare.dataset.Dataset`
            The Dataset to filter.

        Returns
        -------
        dataset : :obj:`~nimare.dataset.Dataset`
            The filtered Dataset.
        """
        masker = self.masker or dataset.masker

        # Get matrix indices for in-brain voxels in the mask
        mask_ijk = np.vstack(np.where(masker.mask_img.get_fdata())).T

        # Get matrix indices for Dataset coordinates
        dset_xyz = dataset.coordinates[["x", "y", "z"]].values

        # mm2vox automatically rounds the coordinates
        dset_ijk = mm2vox(dset_xyz, masker.mask_img.affine)

        # Check if each coordinate in Dataset is within the mask
        # If it is, log that coordinate in keep_idx
        keep_idx = [
            i
            for i, coord in enumerate(dset_ijk)
            if len(np.where((mask_ijk == coord).all(axis=1))[0])
        ]
        LGR.info(
            f"{dset_ijk.shape[0] - len(keep_idx)}/{dset_ijk.shape[0]} coordinates fall outside of "
            "the mask. Removing them."
        )

        # Only retain coordinates inside the brain mask
        dataset.coordinates = dataset.coordinates.iloc[keep_idx]

        return dataset
