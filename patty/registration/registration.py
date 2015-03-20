#!/usr/bin/env python

"""
Registration algorithms and utility functions
"""

from __future__ import print_function
from pcl.boundaries import estimate_boundaries
import numpy as np
import logging
import json
from .. import copy_registration, is_registered, extract_mask, register, BoundingBox
from ..segmentation import dbscan
from matplotlib import path
from sklearn.decomposition import PCA

logging.basicConfig(level=logging.INFO)


def downsample_voxel(pointcloud, voxel_size=0.01):
    '''Downsample a pointcloud using a voxel grid filter.

    Arguments:
        pointcloud    Original pointcloud
        voxel_size    Grid spacing for the voxel grid
    Returns:
        filtered_pointcloud
    '''
    pc_filter = pointcloud.make_voxel_grid_filter()
    pc_filter.set_leaf_size(voxel_size, voxel_size, voxel_size)
    return pc_filter.filter()


def downsample_random(pc, fraction, random_seed=None):
    """Randomly downsample pointcloud to a fraction of its size.

    Returns a pointcloud of size fraction * len(pc), rounded to the nearest
    integer.

    Use random_seed=k for some integer k to get reproducible results.
    Arguments:
        pc : pcl.PointCloud
            Input pointcloud.
        fraction : float
            Fraction of points to include.
        random_seed : int, optional
            Seed to use in random number generator.

    Returns:
        pcl.Pointcloud
    """
    if not 0 < fraction <= 1:
        raise ValueError("Expected fraction in (0,1], got %r" % fraction)

    rng = np.random.RandomState(random_seed)

    k = max(int(round(fraction * len(pc))), 1)
    sample = rng.choice(len(pc), k, replace=False)
    new_pc = pc.extract(sample)
    if is_registered(pc):
        copy_registration(new_pc, pc)
    return new_pc


def register_offset_scale_from_ref(pc, ref_array, ref_offset=np.zeros(3)):
    '''Returns a 3d-offset and uniform scale value from footprint.

    The scale is immediately applied to the pointcloud. The offset is
    set to the patty_registration.conversions.RegisteredPointCloud.'''
    ref_min = ref_array.min(axis=0)
    ref_max = ref_array.max(axis=0)
    ref_center = (ref_min + ref_max) / 2.0 + ref_offset

    pc_array = np.asarray(pc)
    pc_min = pc_array.min(axis=0)
    pc_max = pc_array.max(axis=0)

    pc_size = pc_max - pc_min
    ref_size = ref_max - ref_min

    # Take the footprint as the real offset, and correct the z-offset
    # The z-offset of the footprint will be ground level, the z-offset of the
    # pointcloud will include the monuments height
    pc_registration_scale = np.mean(ref_size[0:1] / pc_size[0:1])

    pc_array *= pc_registration_scale
    pc_min *= pc_registration_scale
    pc_max *= pc_registration_scale

    register(pc, offset=ref_center - (pc_min + pc_max) / 2.0,
             precision=pc.precision * pc_registration_scale)

    return pc.offset, pc_registration_scale


def get_pointcloud_boundaries(pointcloud, angle_threshold=0.1,
                              search_radius=None, normal_search_radius=None):
    '''Find the boundary of a pointcloud.

    Arguments:
        pointcloud : pcl.PointCloud

        angle_threshold : float

        search_radius : float defaults to 1 percent of pointcloud size as
                        determined by the diagonal of the boundingbox

        normal_search_radius : float defaults to search_radius

    Returns:
        a pointcloud
    '''

    if search_radius == None:
        bb = BoundingBox(points=pointcloud)
        logging.info( bb )
        logging.info( bb.diagonal )
        search_radius = 0.01 * bb.diagonal 

    if normal_search_radius == None:
        normal_search_radius = search_radius

    logging.info("Search radius from bounding box: %f" % search_radius )

    boundary = estimate_boundaries(pointcloud, angle_threshold=angle_threshold,
                                   search_radius=search_radius,
                                   normal_search_radius=normal_search_radius)

    logging.info("Found %d out of %d boundary points" 
                   % (np.count_nonzero(boundary),len(pointcloud)))

    return extract_mask(pointcloud, boundary)


def register_from_footprint(pc, footprint):
    '''Register a pointcloud by placing it in footprint. Applies dbscan first.

    Arguments:
        pc : pcl.PointCloud

        footprint : np.ndarray
            Array of [x,y,z] describing the footprint.

    Returns:
        The original pointcloud, rotated/translated to the footprint.
    '''
    logging.info("Finding largest cluster")
    pc_main = dbscan.largest_dbscan_cluster(pc, .1, 250)

    logging.info("Detecting boundary")
    boundary = get_pointcloud_boundaries(pc_main)

    if len(boundary) == len(pc_main) or len(boundary) == 0:
        log("Boundary information could not be retrieved")
        return None

    logging.info("Finding rotation")
    transform = find_rotation(boundary, footprint)
    boundary.transform(transform)

    logging.info("Registering pointcloud to footprint")
    registered_offset, registered_scale = register_offset_scale_from_ref(
        boundary, footprint)
    copy_registration(pc, boundary)

    # rotate and scale up
    transform[:3, :3] *= registered_scale
    pc.transform(transform)

    return pc


def register_from_reference(pc, pc_ref):
    '''Register a pointcloud by aligning it with a reference pointcloud.

    Applies dbscan first.

    Arguments:
        pc : pcl.PointCloud
            Pointcloud to be registered.
        pc_ref : pcl.PointCloud
            Reference pointcloud.
        footprint : numpy.ndarray
            Array of [x,y,z] describing the footprint.
    Returns:
        pc_trans : pcl.PointCloud
            The original pointcloud, rotated/translated to the footprint.
    '''
    logging.info("Finding largest cluster")
    pc_main = dbscan.largest_dbscan_cluster(pc, .1, 250)

    logging.info("Finding rotation")
    transform = find_rotation( pc_main, pc_ref )
    pc_main.transform(transform)

    logging.info("Registering pointcloud to footprint")
    registered_offset, registered_scale = register_offset_scale_from_ref(
        pc_main, np.asarray(pc_ref), pc_ref.offset)
    copy_registration(pc, pc_main)

    # rotate and scale up
    transform[:3, :3] *= registered_scale
    pc.transform(transform)

    return pc

def _find_rotation_helper(pointcloud):
    pca = PCA(n_components=3)
    pca.fit(np.asarray(pointcloud))

    transform = np.eye(4)
    transform[:3, :3] = np.array(pca.components_)

    # make sure the rotation is a proper rotation, ie det = +1
    if np.linalg.det( transform ) < 0:
        transform[:,1] *= -1.0

    # keep the up direction pointing (mostly) upwards
    if transform[2,2] < 0.0:
        transform[:,1] *= -1.0
        transform[:,2] *= -1.0

    return transform

def find_rotation(pointcloud, ref):
    '''Find the transformation that rotates the principal axis of pointcloud
    onto those of the reference pointcloud. Keep the 3 axis pointing upwards.

    Arguments:
        pointcloud: pcl.PointCloud

        ref: pcl.PointCloud

    Returns:
        numpy array
    '''

    pc_transform  = _find_rotation_helper( pointcloud )
    ref_transform = _find_rotation_helper( ref )

    return np.dot(np.linalg.inv(ref_transform), pc_transform)


def point_in_polygon2d(points, polygon):
    p = path.Path(np.asarray(polygon)[:, :2])
    return np.array([p.contains_point(point[:2]) for point in points],
                    dtype=np.bool)


def intersect_polygon2d(pc, polygon):
    in_polygon = point_in_polygon2d(np.asarray(pc) + pc.offset, polygon)
    return extract_mask(pc, in_polygon)


def scale_points(polygon, factor):
    polygon = np.asarray(polygon)
    offset = polygon.mean(axis=0)
    return ((polygon - offset) * factor) + offset


def is_upside_down(upfilepath, transform):
    '''Decides if a pointcloud is upside down using its relative up
    vector and the transformation (rotation only) matrix.

    Arguments:
        upfilepath path of the json file containing the relative up vector
        transform  2d array describing the rotation matrix
    Returns:
        true  pointcloud is upside down
        false pointcloud is right side up
    '''
    if upfilepath in (None, ''):
        return False

    try:
        with open(upfilepath) as upfile:
            dic = json.load(upfile)
    except:
        return False

    estimated_up = np.array(dic['estimatedUpDirection'])
    real_up = transform[:,2]

    return np.dot( estimated_up, real_up ) < 0
