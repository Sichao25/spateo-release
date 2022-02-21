"""Functions for use when labeling individual nuclei/cells, after obtaining a
mask.
"""
from typing import Optional, Union

import cv2
import numpy as np
from anndata import AnnData
from scipy.sparse import issparse, spmatrix
from skimage import segmentation, filters

from . import utils
from ...configuration import SKM
from ...errors import PreprocessingError


def _watershed(
    X: np.ndarray,
    mask: np.ndarray,
    markers: np.ndarray,
    k: int,
) -> np.ndarray:
    """Assign individual nuclei/cells using the Watershed algorithm.

    Args:
        X: Data array. This array will be Gaussian blurred and used as the
            input values to Watershed.
        mask: Nucleus/cell mask.
        markers: Numpy array indicating where the Watershed markers are. May
            either be a boolean or integer array. If this is a boolean array,
            the markers are identified by calling `cv2.connectedComponents`.
        k: Size of the kernel to use for Gaussian blur.

    Returns:
        Watershed labels.
    """
    blur = utils.conv2d(X, k, mode="gauss")
    if markers.dtype == np.dtype(bool):
        markers = cv2.connectedComponents(markers.astype(np.uint8))[1]
    watershed = segmentation.watershed(-blur, markers, mask=mask)
    return watershed


def watershed_markers(
    adata: AnnData,
    layer: str = SKM.STAIN_LAYER_KEY,
    k: int = 3,
    square: bool = False,
    min_area: int = 100,
    n_iter: int = -1,
    float_k: int = 5,
    float_threshold: Optional[float] = None,
    out_layer: Optional[str] = None,
):
    """Find markers for used in Watershed.

    Args:
        adata: Input Anndata
        layer: Layer that was used to create scores or masks. If `{layer}_scores`
            is present, that is used. Otherwise if `{layer}_mask` is present,
            that is used. Otherwise, the layer is taken as a literal.
        k: Erosion kernel size
        square: Whether to use a square kernel
        min_area: Minimum area
        n_iter: Number of erosions to perform.
        float_k: Morphological close and open kernel size when `X` is a
            float array.
        float_threshold: Threshold to use to determine connected components
            when `X` is a float array. By default, a threshold is automatically
            determined by using Otsu method.
        out_layer: Layer to save results. By default, this will be `{layer}_markers`.
    """
    _layer1 = SKM.gen_new_layer_key(layer, SKM.SCORES_SUFFIX)
    _layer2 = SKM.gen_new_layer_key(layer, SKM.MASK_SUFFIX)
    if _layer1 not in adata.layers and _layer2 not in adata.layers and layer not in adata.layers:
        raise PreprocessingError(
            f'Neither "{_layer1}", "{_layer2}", nor "{layer}" are present in AnnData. '
            "Please run either `st.pp.segmentation.icell.mask_nuclei_from_stain` "
            "or `st.pp.segmentation.score_and_mask_pixels` first."
        )
    _layer = layer
    if _layer1 in adata.layers:
        _layer = _layer1
    elif _layer2 in adata.layers:
        _layer = _layer2
    X = SKM.select_layer_data(adata, _layer, make_dense=True)
    if np.issubdtype(X.dtype, np.floating) and not float_threshold:
        float_threshold = filters.threshold_otsu(X)
    markers = utils.safe_erode(X, k, square, min_area, n_iter, float_k, float_threshold)
    out_layer = out_layer or SKM.gen_new_layer_key(layer, SKM.MARKERS_SUFFIX)
    SKM.set_layer_data(adata, out_layer, markers)


def watershed(
    adata: AnnData,
    layer: str = SKM.STAIN_LAYER_KEY,
    k: int = 3,
    mask_layer: Optional[str] = None,
    markers_layer: Optional[str] = None,
    out_layer: Optional[str] = None,
):
    """Assign individual nuclei/cells using the Watershed algorithm.

    Args:
        adata: Input AnnData
        layer: Original data layer from which segmentation will derive from.
        k: Size of the kernel to use for Gaussian blur.
        mask_layer: Layer containing mask. This will default to `{layer}_mask`.
        markers_layer: Layer containing Watershed markers. This will default to
            `{layer}_markers`. May either be a boolean or integer array.
            If this is a boolean array, the markers are identified by calling
            `cv2.connectedComponents`.
        out_layer: Layer to save results. Defaults to `{layer}_labels`.
    """
    X = SKM.select_layer_data(adata, layer, make_dense=True)
    mask_layer = mask_layer or SKM.gen_new_layer_key(layer, SKM.MASK_SUFFIX)
    mask = SKM.select_layer_data(adata, mask_layer)
    markers_layer = markers_layer or SKM.gen_new_layer_key(layer, SKM.MARKERS_SUFFIX)
    markers = SKM.select_layer_data(adata, markers_layer)
    labels = _watershed(X, mask, markers, k)
    out_layer = out_layer or SKM.gen_new_layer_key(layer, SKM.LABELS_SUFFIX)
    SKM.set_layer_data(adata, out_layer, labels)


def _expand_labels(labels: np.ndarray, distance: int, max_area: int, mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Expand labels up to a certain distance, while ignoring labels that are
    above a certain size.

    Args:
        labels: Numpy array containing integer labels.
        distance: Distance to expand. Internally, this is used as the number
            of iterations of distance 1 dilations.
        max_area: Maximum area of each label.
        mask: Only expand within the provided mask.

    Returns:
        New label array with expanded labels.
    """
    expanded = labels.copy()
    saved = {}
    for _ in range(distance):
        for label in (np.bincount(expanded.flatten()) >= max_area).nonzero()[0]:
            if label > 0:
                where = np.where(expanded == label)
                saved[label] = where
                # Remove labels that reached max area
                expanded[where] = 0

        # Expand
        expanded = segmentation.expand_labels(expanded, distance=1)
        if mask is not None:
            expanded *= mask

    # Replace with saved labels
    for label, where in saved.items():
        expanded[where] = label

    return expanded


def expand_labels(
    adata: AnnData,
    layer: str,
    distance: int = 5,
    max_area: int = 400,
    mask_layer: Optional[str] = None,
    out_layer: Optional[str] = None,
):
    """Expand labels up to a certain distance.

    Args:
        adata: Input Anndata
        layer: Layer from which the labels were derived. Then, `{layer}_labels`
            is used as the labels. If not present, it is taken as a literal.
        distance: Distance to expand. Internally, this is used as the number
            of iterations of distance 1 dilations.
        max_area: Maximum area of each label.
        out_layer: Layer to save results. By default, uses `{layer}_labels_expanded`.
    """
    label_layer = SKM.gen_new_layer_key(layer, SKM.LABELS_SUFFIX)
    if label_layer not in adata.layers:
        label_layer = layer
    labels = SKM.select_layer_data(adata, label_layer)
    expanded = _expand_labels(labels, distance, max_area)
    out_layer = out_layer or SKM.gen_new_layer_key(label_layer, SKM.EXPANDED_SUFFIX)
    SKM.set_layer_data(adata, out_layer, expanded, replace=True)


def _label_connected_components(
    X: np.ndarray,
    k: int = 3,
    min_area: int = 100,
    n_iter: int = -1,
    distance: int = 5,
    max_area: int = 400,
) -> np.ndarray:
    """Label connected components while splitting components that are too large.

    Args:
        X: Boolean mask to compute connected components from.
        k: Kernel size for erosion.
        min_area: Don't erode labels smaller than this area.
        n_iter: Number of erosion operations. -1 means continue eroding until
            every label is less than `min_area`.
        distance: Distance to expand eroded labels.
        max_area: Only operate on labels greater than this area.

    Returns:
        New label array
    """
    components = cv2.connectedComponentsWithStats(X.astype(np.uint8))
    areas = components[2][:, cv2.CC_STAT_AREA]
    subset = np.zeros(X.shape, dtype=bool)
    subset_labels = []
    for label in np.where(areas > max_area)[0]:
        if label > 0:
            subset += components[1] == label
            subset_labels.append(label)
    max_label = components[1].max()

    eroded = utils.safe_erode(subset, k=k, min_area=min_area, n_iter=n_iter)
    labels = cv2.connectedComponents(eroded.astype(np.uint8))[1]
    expanded = _expand_labels(labels, distance=distance, max_area=max_area, mask=subset)
    # Fix labels
    fixed = expanded.copy()
    for label in np.unique(expanded):
        if label > 0:
            fixed[np.where(expanded == label)] = (
                subset_labels[label - 1] if label <= len(subset_labels) else max_label + label - len(subset_labels)
            )

    where = np.where(~subset)
    fixed[where] = components[1][where]
    return fixed


def label_connected_components(
    adata: AnnData,
    layer: str,
    k: int = 3,
    min_area: int = 100,
    n_iter: int = -1,
    distance: int = 5,
    max_area: int = 400,
    out_layer: Optional[str] = None,
):
    """Label connected components while splitting components that are too large.

    Args:
        adata: Input Anndata
        layer: Data layer that was used to generate the mask. First, will look
            for `{layer}_mask`. Otherwise, this will be use as a literal.
        k: Kernel size for erosion.
        min_area: Don't erode labels smaller than this area.
        n_iter: Number of erosion operations. -1 means continue eroding until
            every label is less than `min_area`.
        distance: Distance to expand eroded labels.
        max_area: Only operate on labels greater than this area.
        out_layer: Layer to save results. Defaults to `{layer}_labels`.

    Returns:
        New label array
    """
    mask_layer = SKM.gen_new_layer_key(layer, SKM.MASK_SUFFIX)
    if mask_layer not in adata.layers:
        mask_layer = layer
    mask = SKM.select_layer_data(adata, mask_layer)
    labels = _label_connected_components(mask, k, min_area, n_iter, distance, max_area)
    out_layer = out_layer or SKM.gen_new_layer_key(layer, SKM.LABELS_SUFFIX)
    SKM.set_layer_data(adata, out_layer, labels)
