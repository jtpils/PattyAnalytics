#!/usr/bin/env python

"""
Segmentation algorithms and utility functions
"""

from __future__ import print_function
import numpy as np
from pcl.boundaries import estimate_boundaries
from shapely.geometry.polygon import LinearRing
from shapely.geometry import asPoint

from patty import utils
from .dbscan import get_largest_dbscan_clusters
from .. import extract_mask, BoundingBox, log, save


def boundary_of_drivemap(drivemap, footprint, height=1.0, edge_width=0.25):
    """
    Construct an object boundary using the manually recorded corner points.
    Do this by finding all the points in the drivemap along the footprint.
    Use the bottom 'height' meters of the drivemap (not trees).
    Resulting pointcloud has the same SRS and offset as the input.

    Arguments:
        drivemap   : pcl.PointCloud
        footprint  : pcl.PointCloud
        height     : Cut-off height, points more than this value above the
                     lowest point of the drivemap are considered trees,
                     and dropped. default 1 m.
        edge_width : Points belong to the boundary when they are within
                     this distance from the footprint. default 0.25

    Returns:
        boundary   : pcl.PointCloud
    """

    # construct basemap as the bottom 'height' meters of the drivemap

    drivemap_array = np.asarray(drivemap)
    bb = BoundingBox(points=drivemap_array)
    basemap = extract_mask(drivemap, drivemap_array[:, 2] < bb.min[2] + height)

    # Cut band between +- edge_width around the footprint
    edge = LinearRing(np.asarray(footprint)).buffer(edge_width)
    boundary = extract_mask(basemap,
                            [edge.contains(asPoint(pnt)) for pnt in basemap])

    utils.force_srs(boundary, same_as=basemap)

    return boundary


def boundary_of_lowest_points(pc, height_fraction=0.01):
    '''
    Construct an object boundary by taking the lowest (ie. min z coordinate)
    fraction of points.
    Resulting pointcloud has the same SRS and offset as the input.

    Arguments:
        pc               : pcl.PointCloud
        height_fraction  : float

    Returns:
        boundary   : pcl.PointCloud
    '''
    array = np.asarray(pc)
    bb = BoundingBox(points=array)
    maxh = bb.min[2] + (bb.max[2] - bb.min[2]) * height_fraction

    newpc = extract_mask(pc, array[:, 2] < maxh)

    utils.force_srs(newpc, same_as=pc)

    return newpc


def boundary_of_center_object(pc,
                              downsample=None,
                              angle_threshold=0.1,
                              search_radius=0.1,
                              normal_search_radius=0.1):
    """
    Find the boundary of the main object.
    First applies dbscan to find the main object,
    then estimates its footprint by taking the pointcloud boundary.
    Resulting pointcloud has the same SRS and offset as the input.

    Arguments:
        pointcloud : pcl.PointCloud

        downsample : If given, reduce the pointcloud to given percentage
                     values should be in [0,1]

        angle_threshold : float defaults to 0.1

        search_radius : float defaults to 0.1

        normal_search_radius : float defaults to 0.1

    Returns:
        boundary : pcl.PointCloud
    """

    if downsample is not None:
        log(' - Random downsampling factor:', downsample)
        pc = utils.downsample_random(pc, downsample)
    else:
        log(' - Not downsampling')

    # Main noise supression step
    # Find largest clusters, accounting for at least 70% of the pointcloud.
    # Presumably, this is the main object.
    log(' - Starting dbscan on downsampled pointcloud')
    mainobject = get_largest_dbscan_clusters(pc, 0.7, .075, 250)
    save(mainobject, 'mainobject.las')

    boundary = estimate_boundaries(mainobject,
                                   angle_threshold=angle_threshold,
                                   search_radius=search_radius,
                                   normal_search_radius=normal_search_radius)

    boundary = extract_mask(mainobject, boundary)

    if len(boundary) == len(mainobject) or len(boundary) == 0:
        log(' - Cannot find boundary')
        return None

    # project on the xy plane, take 2th percentile height
    points = np.asarray(boundary)
    points[:, 2] = np.percentile(points[:, 2], 2)

    # Secondary noise supression step
    # some cases have multiple objects close to eachother, and we need to
    # filter some out. Assume the object is a single item; perform another
    # dbscan to select the footprint of the main item
    log(' - Starting dbscan on boundary')
    mainboundary = get_largest_dbscan_clusters(boundary, 0.5, .1, 10)

    # Evenly space out the points
    mainboundary = utils.downsample_voxel(mainboundary, voxel_size=0.1)

    utils.force_srs(mainboundary, same_as=pc)

    return mainboundary
