import unittest
import numpy as np
from patty.conversions.conversions import loadLas, loadCsvPolygon
from matplotlib import path

class TestInPoly(unittest.TestCase):
    def testInPoly(self):
        fileLas = 'data/footprints/162.las'
        filePoly = 'data/footprints/162.las_footprint.csv'
        pc = loadLas(fileLas)
        footprint = loadCsvPolygon(filePoly)
        points_in_poly(pc, footprint)

def point_in_poly(point, polyPath):
    return polyPath.contains_point(point[:2])

def points_in_poly(pc, poly):
    polyPath = path.Path(poly[:,:2])
    points = np.asarray(pc)
    return np.array([ point for point in points if point_in_poly(point+pc.offset, polyPath) ])
