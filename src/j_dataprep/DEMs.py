import requests
from io import BytesIO
import gzip
import os
import pandas as pd
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from scipy.interpolate import NearestNDInterpolator
import random
import startinpy
from rasterio import Affine
from shapely.geometry import shape,box, mapping
from rasterio.crs import CRS
from pathlib import Path
import laspy
from scipy.spatial import cKDTree
from scipy.ndimage import median_filter, label, maximum_filter
import uuid
from rtree import index
import ezdxf
import json
from shapely.affinity import translate
from rasterio.enums import Resampling
from rasterio.warp import reproject

def edit_bounds(bounds, buffer, shrink=False):
    '''
    Expands or shrinks bounding box coordinates by a buffer amount.

    Parameters:
        bounds (tuple): Bounding box as (min_x, min_y, max_x, max_y).
        buffer (float): Amount to expand or shrink the bounding box.
        shrink (bool):  If True, shrink the bounds by buffer; else expand (default False).

    Returns:
        tuple: Modified bounding box as (min_x, min_y, max_x, max_y).
    '''
    min_x, min_y, max_x, max_y = bounds

    if shrink:
        return (
            min_x + buffer,
            min_y + buffer,
            max_x - buffer,
            max_y - buffer
        )
    else:
        return (
            min_x - buffer,
            min_y - buffer,
            max_x + buffer,
            max_y + buffer
        )


def write_output(dataset, crs, output, transform, name, change_nodata=False):
    '''
    Writes a numpy array to a GeoTIFF file using rasterio.

    Parameters:
        dataset        :        Rasterio or laspy dataset (for metadata).
        crs            :        Coordinate Reference System for the output raster.
        output (np.ndarray):    Output numpy array grid to write.
        transform      :        Affine transform mapping pixel to spatial coordinates.
        name (str)     :        Output filename (including path).
        change_nodata (bool):   If True, use nodata value -9999; else use dataset's nodata.

    Returns:
        None
    '''
    output_file = name

    output = np.squeeze(output)
    # Set the nodata value: use -9999 if nodata_value is True or dataset does not have nodata.
    if change_nodata:
        nodata_value = -9999
    else:
        try:
            # TO DO: CHANGE THIS TO JUST INPUTTING A NODATA VALUE, NO NEED FOR THE WHOLE DATASET IN THIS FUNCTION
            nodata_value = dataset.nodata
            if nodata_value is None:
                raise AttributeError("No no data value found in dataset.")
        except AttributeError as e:
            print(f"Warning: {e}. Defaulting to -9999.")
            nodata_value = -9999

    # output the dataset
    with rasterio.open(output_file, 'w',
                       driver='GTiff',
                       height=output.shape[0],  # Assuming output is (rows, cols)
                       width=output.shape[1],
                       count=1,
                       dtype=np.float32,
                       crs=crs,
                       nodata=nodata_value,
                       transform=transform) as dst:
        dst.write(output, 1)
    print("File written to '%s'" % output_file)


class Buildings:
    '''
    Manage 3D building data within a bounding box by downloading, loading,
    and modifying building geometries from a WFS service.

    Attributes:
        bbox (tuple):                   Bounding box (min_x, min_y, max_x, max_y) for the area of interest.
        bufferbbox (tuple):             Buffered bounding box expanded by 2 units.
        wfs_url (str):                  URL of the WFS service to download building data.
        layer_name (str):               WFS layer name to query.
        data (GeoDataFrame):            Downloaded building data.
        building_geometries (list):     List of building geometries with parcel IDs.
        removed_buildings (list):       List of parcel IDs of removed buildings.
        user_buildings (list):          List of user-inserted building geometries.
        user_buildings_higher (list):   List of user buildings with height info.
        removed_user_buildings (list):  List of user building IDs that are removed.
        is3D (bool):                    Flag indicating if 3D building data is used.
    '''

    def __init__(self, bbox, wfs_url="https://data.3dbag.nl/api/BAG3D/wfs", layer_name="BAG3D:lod13", gpkg_name="buildings", output_folder = "output", output_layer_name="buildings"):
        '''
        Initialize the Buildings object by setting bounding boxes, downloading,
        and loading building data.

        Parameters:
            bbox (tuple):               Bounding box (min_x, min_y, max_x, max_y).
            wfs_url (str):              URL for the WFS service. Default is 3dbag.nl API.
            layer_name (str):           Name of the WFS layer to query. Default is "BAG3D:lod13".
            gpkg_name (str):            Name of the GeoPackage output file (without extension).
            output_folder (str):        Folder to save the downloaded data.
            output_layer_name (str):    Layer name to save within the GeoPackage.
        '''
        self.bbox = bbox
        self.bufferbbox = edit_bounds(bbox, 2)
        self.wfs_url = wfs_url
        self.layer_name = layer_name
        self.data = self.download_wfs_data(gpkg_name, output_folder, output_layer_name)
        self.building_geometries = self.load_buildings(self.data)
        self.removed_buildings = []
        self.user_buildings = []
        self.user_buildings_higher = []
        self.removed_user_buildings = []
        self.is3D = False

    def download_wfs_data(self, gpkg_name, output_folder, layer_name):
        '''
        Download building features from the WFS service within the buffered bounding box.
        Saves the data as a GeoPackage file.

        Parameters:
            gpkg_name (str):        Filename for the GeoPackage (without extension).
            output_folder (str):    Folder to save the GeoPackage.
            layer_name (str):       Layer name to use inside the GeoPackage.

        Returns:
            GeoDataFrame:           Downloaded building features concatenated, or None if no features were downloaded.
        '''
        all_features = []
        start_index = 0
        count = 10000

        while True:
            params = {
                "SERVICE": "WFS",
                "REQUEST": "GetFeature",
                "VERSION": "2.0.0",
                "TYPENAMES": self.layer_name,
                "SRSNAME": "urn:ogc:def:crs:EPSG::28992",
                "BBOX": f"{self.bufferbbox[0]},{self.bufferbbox[1]},{self.bufferbbox[2]},{self.bufferbbox[3]},urn:ogc:def:crs:EPSG::28992",
                "COUNT": count,
                "STARTINDEX": start_index
            }
            headers = {"User-Agent": "Mozilla/5.0 QGIS/33411/Windows 11 Version 2009"}
            response = requests.get(self.wfs_url, params=params, headers=headers)

            if response.status_code == 200:
                if response.headers.get('Content-Encoding', '').lower() == 'gzip' and response.content[
                                                                                      :2] == b'\x1f\x8b':
                    data = gzip.decompress(response.content)
                else:
                    data = response.content

                with BytesIO(data) as f:
                    gdf = gpd.read_file(f)
                all_features.append(gdf)
                if len(gdf) < count:
                    break
                start_index += count
            else:
                print(f"Failed to download WFS data. Status code: {response.status_code}")
                print(f"Error message: {response.text}")
                return gpd.GeoDataFrame()

        if all_features:
            full_gdf = gpd.GeoDataFrame(pd.concat(all_features, ignore_index=True))
            os.makedirs(output_folder, exist_ok=True)
            output_gpkg = os.path.join(output_folder, f"{gpkg_name}.gpkg")
            full_gdf.to_file(output_gpkg, layer=layer_name, driver="GPKG")
            print("loaded")
            return full_gdf
        else:
            print("No features were downloaded.")
            return None

    @staticmethod
    def load_buildings(buildings_gdf, buildings_path=None, layer=None):
        '''
        Load building geometries from a GeoDataFrame or from a file.

        Parameters:
            buildings_gdf (GeoDataFrame or None):   Building data GeoDataFrame.
            buildings_path (str or None):           Path to building file to load if GeoDataFrame is None.
            layer (str or None):                    Layer name to read from file if applicable.

        Returns:
            list:   List of dicts with 'geometry' (GeoJSON mapping) and 'parcel_id'. None if no data could be loaded.
        '''
        if buildings_gdf is None:
            if buildings_path is not None:
                buildings_gdf = gpd.read_file(buildings_path, layer=layer)
            else: return None

        return [{"geometry": mapping(geom), "parcel_id": identificatie} for geom, identificatie in
                zip(buildings_gdf.geometry, buildings_gdf["identificatie"])]

    def remove_buildings(self, identification):
        '''
        Mark a building as removed by adding its parcel ID to the removed list.

        Parameters:
            identification (str):   Parcel ID of the building to remove.
        '''
        self.removed_buildings.append(identification)

    def retrieve_buildings(self, identification):
        '''
        Undo the removal of a building by removing its parcel ID from the removed list.

        Parameters:
            identification (str):   Parcel ID of the building to retrieve.
        '''

        self.removed_buildings.remove(identification)

    def insert_user_buildings(self, highest_array, transform, footprint_array=None):
        '''
        Insert user-defined buildings based on arrays of building heights and optional footprints.
        Assigns unique parcel IDs and matches footprint buildings with highest buildings.

        Parameters:
            highest_array (np.ndarray):             Array representing the highest building heights.
            transform (Affine):                     Rasterio affine transform for spatial referencing.
            footprint_array (np.ndarray or None):   Optional array representing building footprints.

        Effects:
            Updates self.user_buildings, self.user_buildings_higher, and self.is3D.
        '''
        self.is3D = footprint_array is not None
        self.removed_user_buildings = []
        self.user_buildings_higher = []

        labeled_array, num_clusters = label(highest_array > 0)

        shapes_highest = shapes(labeled_array.astype(np.uint8), mask=(labeled_array > 0), transform=transform)

        highest_buildings = [
            {"geometry": mapping(shape(geom)), "parcel_id": str(uuid.uuid4())[:8]}
            for geom, value in shapes_highest
        ]

        if footprint_array is not None:
            rtree_index = index.Index()
            for idx, building in enumerate(highest_buildings):
                geom = shape(building['geometry'])
                rtree_index.insert(idx, geom.bounds)

            labeled_footprint_array, num_clusters_fp = label(footprint_array > 0)

            shapes_fp = shapes(labeled_footprint_array.astype(np.uint8), mask=(labeled_footprint_array > 0),
                                   transform=transform)

            footprint_buildings = [
                {"geometry": mapping(shape(geom)), "parcel_id": str(uuid.uuid4())[:8]}
                for geom, value in shapes_fp
            ]

            for footprint_building in footprint_buildings:
                footprint_geom = shape(footprint_building['geometry'])

                possible_matches = list(
                    rtree_index.intersection(footprint_geom.bounds))

                for match_idx in possible_matches:
                    highest_building = highest_buildings[match_idx]
                    highest_geom = shape(highest_building['geometry'])

                    if footprint_geom.intersects(highest_geom) or footprint_geom.within(highest_geom):
                        footprint_building['parcel_id'] = highest_building['parcel_id']
                        break
            self.user_buildings = footprint_buildings
            self.user_buildings_higher = highest_buildings
        else:
            self.user_buildings = highest_buildings

    def remove_user_buildings(self, identification):
        '''
        Mark a user building as removed by adding its parcel ID to the removed list.

        Parameters:
            identification (str): Parcel ID of the user building to remove.
        '''
        self.removed_user_buildings.append(identification)

    def retrieve_user_buildings(self, identification):
        '''
        Undo the removal of a user building by removing its parcel ID from the removed list.

        Parameters:
            identification (str): Parcel ID of the user building to retrieve.
        '''
        self.removed_user_buildings.remove(identification)


class DEMS:
    '''
    Class for handling Digital Elevation Models (DEM) including DTM and DSM,
    fetching AHN data via WCS, filling missing data, resampling, cropping,
    and integrating building footprints for urban terrain modeling.

    Attributes:
        buffer (float):                           Buffer size in meters for bbox expansion.
        bbox (tuple):                             Bounding box coordinates (xmin, ymin, xmax, ymax).
        bufferbbox (tuple):                       Buffered bounding box expanded by buffer.
        building_data (list):                     List of building geometries and attributes.
        resolution (float):                       Desired output raster resolution in meters.
        user_building_data (list):                User-provided building data.
        output_dir (str):                         Directory to save output files.
        bridge (bool):                            Whether to include 'overbruggingsdeel' data in the DSM.
        resampling (rasterio.enums.Resampling):   Resampling method for raster operations.
        crs (CRS):                                Coordinate reference system, default EPSG:28992.
        dtm (np.ndarray):                     Digital Terrain Model raster data.
        dsm (np.ndarray):                     Digital Surface Model raster data.
        transform (Affine):                       Affine transform for the rasters.
        og_dtm (np.ndarray):                  Original DTM before modifications.
        og_dsm (np.ndarray):                  Original DSM before modifications.
        is3D (bool):                              Flag indicating if DSM is 3D.
    '''
    def __init__(self, bbox, building_data, resolution=0.5, bridge=False, resampling=Resampling.cubic_spline, output_dir="output"):
        '''
        Initialize the DEM builder object.

        Parameters:
            bbox (tuple):                           Bounding box coordinates (xmin, ymin, xmax, ymax).
            building_data (list):                   Building geometries and data.
            resolution (float):                     Desired output resolution in meters (default 0.5).
            bridge (bool):                          Whether to include  'overbruggingsdeel' geometries (default False).
            resampling (rasterio.enums.Resampling): Resampling method (default cubic_spline).
            output_dir (str):                       Directory for output files (default "output").

        Returns:
            None
        '''
        self.buffer = 2
        self.bbox = bbox
        self.bufferbbox = edit_bounds(bbox, self.buffer)
        self.building_data = building_data
        self.resolution = resolution
        self.user_building_data = []
        self.output_dir = output_dir
        self.bridge = bridge
        self.resampling = resampling
        self.crs = (CRS.from_epsg(28992))
        self.dtm, self.dsm, self.transform = self.create_dem(bbox)
        self.og_dtm, self.og_dsm = self.dtm, self.dsm
        self.is3D = False

    @staticmethod
    def fetch_ahn_wcs(bufferbbox, output_file, coverage="dtm_05m", wcs_resolution=0.5):
        '''
        Fetch AHN WCS data for a given buffered bounding box and save as GeoTIFF.

        Parameters:
            bufferbbox (tuple):     Buffered bounding box (xmin, ymin, xmax, ymax).
            output_file (str):      Output filepath for the GeoTIFF (default "output/dtm.tif").
            coverage (str):         Coverage layer name, e.g. "dtm_05m" or "dsm_05m" (default "dtm_05m").
            wcs_resolution (float): Resolution of WCS data in meters (default 0.5).

        Returns:
            tuple or None: (rasterio dataset object, numpy array of raster data) if successful, else None.
        '''

        # Calculate width and height from bbox and resolution
        width = int((bufferbbox[2] - bufferbbox[0]) / wcs_resolution)
        height = int((bufferbbox[3] - bufferbbox[1]) / wcs_resolution)

        # WCS Service URL
        WCS_URL = "https://service.pdok.nl/rws/ahn/wcs/v1_0"

        # Construct query parameters
        params = {
            "SERVICE": "WCS",
            "VERSION": "1.0.0",
            "REQUEST": "GetCoverage",
            "FORMAT": "GEOTIFF",
            "COVERAGE": coverage,
            "BBOX": f"{bufferbbox[0]},{bufferbbox[1]},{bufferbbox[2]},{bufferbbox[3]}",
            "CRS": "EPSG:28992",
            "RESPONSE_CRS": "EPSG:28992",
            "WIDTH": str(width),
            "HEIGHT": str(height)
        }

        # Send GET request to fetch the data
        response = requests.get(WCS_URL, params=params, headers={"User-Agent": "Mozilla/5.0"})

        if response.status_code == 200:
            with open("temp.tif", "wb") as f:
                f.write(response.content)

            with rasterio.open("temp.tif", "r") as f:
                # TO DO: test if this is still needed after fixing the libraries
                gdal_translate_command = f"gdal_translate -of GTiff -a_srs EPSG:28992 temp.tif {output_file}"
                os.system(gdal_translate_command)

            try:
                with rasterio.open(output_file) as dataset:
                    array = dataset.read(1)
                    old_nodata = dataset.nodata
                    new_nodata = -9999
                    array[array == old_nodata] = new_nodata

                    with rasterio.open(output_file, 'r+') as dst:
                        dst.write(array, 1)
                        dst.nodata = new_nodata

            except Exception as e:
                print(f"Error reading or modifying raster: {e}")
                return None

            # Delete the temporary file after use
            if os.path.exists("temp.tif"):
                os.remove("temp.tif")

            return dst, array

        else:
            print(f"Failed to fetch AHN data: HTTP {response.status_code}")
            return None

    @staticmethod
    def extract_center_cells(geo_array, no_data=-9999):
        '''
        Extract the values of each cell in the input data and save these with the x and y (row and col)
        indices. Thereby, make sure that the corners of the dataset are filled for a full coverage triangulation
        in the next step.

        Parameters:
            geo_array (np.ndarray):         Raster data array.
            no_data (int):                  No data value to identify invalid cells (default -9999).

        Returns:
            list:                           List of [x, y, z] cell values with corners interpolated if no data.
        '''
        # Get the indices of the rows and columns
        rows, cols = np.indices(geo_array.shape)

        # Identify corner coordinates
        corners = {
            "top_left": (0, 0),
            "top_right": (0, geo_array.shape[1] - 1),
            "bottom_left": (geo_array.shape[0] - 1, 0),
            "bottom_right": (geo_array.shape[0] - 1, geo_array.shape[1] - 1)
        }

        # Mask for valid center cells (non-no_data)
        valid_center_cells = (geo_array != no_data)

        # Extract x, y, z values for valid cells
        x_valid = cols[valid_center_cells]
        y_valid = rows[valid_center_cells]
        z_valid = geo_array[valid_center_cells]

        # Create interpolator from valid points
        interpolator = NearestNDInterpolator(list(zip(x_valid, y_valid)), z_valid)

        # Check each corner for no data and interpolate if necessary
        for corner_name, (row, col) in corners.items():
            if geo_array[row, col] == no_data:
                # Interpolate the nearest valid value
                geo_array[row, col] = interpolator((col, row))

        # Extract non-no_data and center cells again after filling corners
        valid_center_cells = (geo_array != no_data)

        # Extract final x, y, z values after filling corners
        x_filled = cols[valid_center_cells]
        y_filled = rows[valid_center_cells]
        z_filled = geo_array[valid_center_cells]

        # Prepare final list of [x, y, z]
        xyz_filled = []
        for x_i, y_i, z_i in zip(x_filled, y_filled, z_filled):
            xyz_filled.append([x_i, y_i, z_i])

        return xyz_filled

    def crop_to_bbox(self, array, transform):
        '''
        Crop a buffered raster array to the original bounding box.

        Parameters:
            array (np.ndarray): Raster data array with buffer.
            transform (Affine): Affine transform matrix of input array.

        Returns
        -------
        cropped_array (np.ndarray):
            Cropped raster array.
        new_transform (Affine):
            New Affine transform matrix for cropped raster.
        '''

        # Compute the window from the full buffered transform, for the smaller (target) bbox
        crop_pixels = int(self.buffer / self.resolution)

        # Crop array: remove buffer from all sides
        print(crop_pixels)
        cropped_array = array[crop_pixels:-crop_pixels, crop_pixels:-crop_pixels]
        print(cropped_array.shape)

        # Adjust transform: move origin by number of removed pixels
        new_transform = transform * Affine.translation(crop_pixels, crop_pixels)

        return cropped_array, new_transform

    def resample_raster(self, input_array, input_transform, input_crs, output_resolution):
        '''
        Resample a raster to a different resolution.

        Parameters:
            input_array (np.ndarray): Input raster data.
            input_transform (Affine): Affine transform of input raster.
            input_crs (CRS): Coordinate Reference System of input raster.
            output_resolution (float): Desired output resolution in meters.

        Returns
        -------
        resampled_array (np.ndarray):
            Resampled raster array.
        new_transform (Affine):
            New Affine transform matrix for resampled raster.
        '''
        height, width = input_array.shape
        new_width = int((width * input_transform.a) / output_resolution)
        new_height = int((height * -input_transform.e) / output_resolution)

        new_transform = rasterio.transform.from_origin(
            input_transform.c, input_transform.f, output_resolution, output_resolution
        )

        resampled_array = np.empty((new_height, new_width), dtype=input_array.dtype)

        reproject(
            source=input_array,
            destination=resampled_array,
            src_transform=input_transform,
            src_crs=input_crs,
            dst_transform=new_transform,
            dst_crs=input_crs,
            resampling=self.resampling
        )

        return resampled_array, new_transform

    def fill_raster(self, geo_array, nodata_value, transform):
        '''
        Fill no-data values in a raster using Laplace interpolation.

        Parameters:
            geo_array (np.ndarray):     Cropped raster data array.
            nodata_value (int):         No-data value to replace NaNs after interpolation.
            transform (Affine):         Affine transform matrix of the raster.

        Returns:
            new_data(np.ndarray):       Filled raster array with no-data values replaced.
        '''

        # creating delaunay
        points = self.extract_center_cells(geo_array, no_data=nodata_value)
        dt = startinpy.DT()
        dt.insert(points, "BBox")

        # for interpolation, grid of all column and row positions, excluding the first and last rows/cols
        cols, rows = np.meshgrid(
            np.arange(0, geo_array.shape[1]),
            np.arange(0, geo_array.shape[0])
        )

        # flatten the grid to get a list of all (col, row) locations
        locs = np.column_stack((cols.ravel(), rows.ravel()))
        interpolated_values = dt.interpolate({"method": "Laplace"}, locs)

        # reshape interpolated grid back to original
        interpolated_grid = np.reshape(interpolated_values, (geo_array.shape[0], geo_array.shape[1]))

        # fill new_data with interpolated values
        new_data= interpolated_grid
        new_data = np.where(np.isnan(new_data), nodata_value, new_data)

        return new_data

    def replace_buildings(self, filled_dtm, dsm_buildings, buildings_geometries, transform, bridge):
        '''
        Replace filled DTM values with DSM building heights where buildings exist.

        Parameters:
            filled_dtm (np.ndarray):        Filled, cropped DTM array.
            dsm_buildings (np.ndarray):     Filled, cropped DSM array with buildings.
            buildings_geometries (list):    List of building geometries (dict or GeoJSON features).
            transform (Affine):             Affine transform matrix of the rasters.
            bridge (bool):                  Whether to include 'overbrugginsdeel' geometries.

        Returns:
            final_dsm (np.ndarray):         Final DSM array combining ground and building heights.
        '''
        geometries = [shape(building['geometry']) for building in buildings_geometries if 'geometry' in building]
        bridging_geometries = []
        if bridge is True:
            bridge_crs = "http://www.opengis.net/def/crs/EPSG/0/28992"
            url = f"https://api.pdok.nl/lv/bgt/ogc/v1/collections/overbruggingsdeel/items?bbox={self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]}&bbox-crs={bridge_crs}&crs={bridge_crs}&limit=1000&f=json"
            response = requests.get(url)
            if response.status_code == 200:
                bridging_data = response.json()
                if "features" in bridging_data:  # Ensure data contains geometries
                    bridging_geometries = [shape(feature['geometry']) for feature in bridging_data["features"] if
                                           'geometry' in feature]
            else:
                print(f"Error fetching bridges: {response.status_code}, {response.text}")

        # Ensure mask has same shape as filled_dtm
        all_geometries = bridging_geometries + geometries
        building_mask = geometry_mask(all_geometries, transform=transform, invert=False, out_shape=filled_dtm.shape)

        # Get shape differences
        dtm_shape = filled_dtm.shape
        dsm_shape = dsm_buildings.shape

        if dtm_shape != dsm_shape:
            # Compute the cropping offsets
            row_diff = dsm_shape[0] - dtm_shape[0]
            col_diff = dsm_shape[1] - dtm_shape[1]

            # Ensure even cropping from all sides (center alignment)
            row_start = row_diff // 2
            col_start = col_diff // 2
            row_end = row_start + dtm_shape[0]
            col_end = col_start + dtm_shape[1]

            # Crop dsm_buildings to match filled_dtm
            dsm_buildings = dsm_buildings[row_start:row_end, col_start:col_end]

        # Apply the mask
        final_dsm = np.where(building_mask, filled_dtm, dsm_buildings)
        return final_dsm

    def create_dem(self, bbox):
        '''
        Create Digital Elevation Model (DEM) from AHN data with optional building and overbrugginsdeel data.

        Parameters:
            bbox (tuple):       Bounding box coordinates (xmin, ymin, xmax, ymax).

        Returns
        -------
        cropped_dtm (np.ndarray):
            Filled, cropped DTM array.
        cropped_dsm (np.ndarray):
            Cropped DSM array with buildings and building heights, optional output.
        transform (Affine):
            Affine transform matrix of the rasters.
         '''

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        # --- Fetch DTM ---
        dtm_dst, dtm_array = self.fetch_ahn_wcs(
            self.bufferbbox, output_file="output/dtm_fetched.tif", coverage="dtm_05m", wcs_resolution=0.5
        )
        transform = dtm_dst.transform
        filled_dtm  = self.fill_raster(dtm_array, dtm_dst.nodata, transform)

        # --- Fetch DSM if buildings are used ---
        if self.building_data:
            dsm_dst, dsm_array = self.fetch_ahn_wcs(
                self.bufferbbox, output_file="output/dsm_fetched.tif", coverage="dsm_05m", wcs_resolution=0.5
            )
            filled_dsm = self.fill_raster(dsm_array, dsm_dst.nodata, transform)
            final_dsm = self.replace_buildings(
                filled_dtm, filled_dsm, self.building_data, transform, self.bridge
            )
        else:
            final_dsm = filled_dtm
        # --- Resample if needed ---
        if self.resolution != 0.5:
            filled_dtm, resamp_transform = self.resample_raster(
                filled_dtm, transform, dtm_dst.crs, self.resolution
            )

            if final_dsm is not None:
                final_dsm, _ = self.resample_raster(
                    final_dsm, transform, dtm_dst.crs, self.resolution
                )

            transform = resamp_transform

        # --- Crop the arrays to the bounding box after interpolation ---
        cropped_dtm, transform = self.crop_to_bbox(filled_dtm, transform)

        if final_dsm is not None:
            cropped_dsm, _ = self.crop_to_bbox(final_dsm, transform)

        # --- Write outputs ---
        write_output(dtm_dst, self.crs, cropped_dtm, transform, f"{self.output_dir}/final_dtm.tif")

        if final_dsm is not None:
            write_output(dtm_dst, self.crs, cropped_dsm, transform, f"{self.output_dir}/final_dsm.tif")

        return cropped_dtm, cropped_dsm if final_dsm is not None else cropped_dtm, transform

    def update_dsm(self, user_buildings, user_array=None, user_arrays=None, higher_buildings=None):
        '''
        Update the DSM with new user building heights, supporting both 2D and 3D DSM arrays.

        Parameters:
            user_buildings (list):                          List of user building data dictionaries with geometries.
            user_array (np.ndarray, optional):              Single 2D array with building height data.
            user_arrays (list of np.ndarray, optional):     List of arrays representing multiple DSM layers.
            higher_buildings (list, optional):              List of user buildings with additional height layers.

        Returns:
            None
        '''
        self.is3D = user_arrays is not None

        if isinstance(self.dsm, np.ndarray):
            self.dsm = [self.dsm]

        self.dsm = self.dsm + [np.full_like(self.dtm, np.nan) for _ in range(len(self.dsm), len(user_arrays))]

        for building in user_buildings:
            if 'geometry' in building:
                geom = shape(building['geometry'])

                mask = geometry_mask([geom], transform=self.transform, invert=True, out_shape=self.dtm.shape)
                # Find the minimum value within the mask
                min_value = np.min(self.dtm[mask])

                if not self.is3D:
                    self.dsm[mask] = user_array[mask] + min_value
                else:
                    self.dsm[0][mask] = user_arrays[0][mask] + min_value

                    if higher_buildings is not None:
                        new_build = next(
                            (b for b in higher_buildings if b['parcel_id'] == building['parcel_id']),
                            None
                        )

                        if new_build and 'geometry' in new_build:
                            new_geom = shape(new_build["geometry"])
                            new_mask = geometry_mask([new_geom], transform=self.transform, invert=True,
                                                     out_shape=self.dtm.shape)

                            for i in range(1, len(user_arrays)):
                                self.dsm[i][new_mask] = user_arrays[i][new_mask] + min_value

    def remove_buildings(self, remove_list, remove_user_list, building_data, user_building_data, user_buildings_higher=None):
        '''
        Remove specified buildings from DSM by replacing their areas with DTM values.

        Parameters:
            remove_list (list):                         List of parcel IDs to remove from the main building dataset.
            remove_user_list (list):                    List of parcel IDs to remove from the user building dataset.
            building_data (list):                       List of main building data dictionaries.
            user_building_data (list):                  List of user building data dictionaries.
            user_buildings_higher (list, optional):     List of user buildings with higher layers to be removed as well.

        Returns:
            None
        '''

        remove_set = set(remove_list)
        remove_user_set = set(remove_user_list)
        # Find buildings to remove from both datasets
        to_remove = [building for building in building_data if building['parcel_id'] in remove_set]
        print("Parcel IDs being checked (to_remove):",
              [building['parcel_id'] for building in building_data])

        to_remove_user = [building for building in user_building_data if building['parcel_id'] in remove_user_set]
        print("Parcel IDs being checked (to_remove_user):",
              [building['parcel_id'] for building in user_building_data])

        remove_all = to_remove + to_remove_user

        # Extract geometries for mask creation
        geometries = [shape(building['geometry']) for building in remove_all if 'geometry' in building]

        # Create the removal mask if there are geometries
        if geometries:
            remove_building_mask = geometry_mask(geometries, transform=self.transform, invert=False,
                                                 out_shape=self.dtm.shape)
            if not self.is3D:
                self.dsm[...] = np.where(remove_building_mask, self.dsm, self.dtm)
            else:
                self.dsm[0][...] = np.where(remove_building_mask, self.dsm[0], self.dtm)

                if user_buildings_higher:
                    remove_other_layers = [building for building in user_buildings_higher if
                                           building['parcel_id'] in remove_user_set]
                    other_geometries = [shape(building['geometry']) for building in remove_other_layers if
                                        'geometry' in building]

                    if other_geometries:
                        remove_others_mask = geometry_mask(other_geometries, transform=self.transform, invert=False,
                                                           out_shape=self.dtm.shape)

                        for i in range(1, len(self.dsm)):
                            self.dsm[i][...] = np.where(remove_others_mask, self.dsm[i], np.nan)

    def update_building_height(self, raise_height, user_buildings, building_id=None, user_array=None, user_arrays=None, higher_buildings=None):
        '''
        Raise the height of specified user building(s) in the DSM by a given amount.

        Parameters:
            raise_height (float):                           Amount to raise the building height.
            user_buildings (list):                          List of user building data dictionaries.
            building_id (str, optional):                    ID of the building to raise. If None, raise_all should be used.
            user_array (np.ndarray, optional):              Single 2D array with building height data.
            user_arrays (list of np.ndarray, optional):     List of arrays representing multiple DSM layers.
            higher_buildings (list, optional):              List of buildings with additional height layers for 3D DSM.

        Returns:
            None
        '''
        if building_id is not None:
            matching_buildings = [building for building in user_buildings if building['id'] == building_id]
            for building in matching_buildings:
                if 'geometry' in building:
                    geom = shape(building['geometry'])

                    mask = geometry_mask([geom], transform=self.transform, invert=True, out_shape=self.dtm.shape)
                if not self.is3D:
                    self.dsm[mask] += raise_height
                else:
                    self.dsm[0][mask] += raise_height
                    if higher_buildings:
                        new_build = next(
                            (b for b in higher_buildings if b['parcel_id'] == building['parcel_id']),
                            None
                        )

                        if new_build and 'geometry' in new_build:
                            new_geom = shape(new_build["geometry"])
                            new_mask = geometry_mask([new_geom], transform=self.transform, invert=True,
                                                     out_shape=self.dtm.shape)

                            for i in range(2, len(user_arrays), 2):
                                self.dsm[i][new_mask] += raise_height

    def export_context(self, file_name, export_format="dxf"):
        '''
        Export buildings and DSM bounding box to a CAD-compatible file format.

        Parameters:
            file_name (str):                        Path and name of the file to export.
            export_format (str, optional):          Export format. Options: 'json', 'csv', or 'dxf'. Defaults to 'dxf'.

        Returns:
            None
        '''

        bbox = np.array(self.bbox) + np.array([self.resolution, self.resolution, -self.resolution, -self.resolution])
        xmin, ymin, xmax, ymax = bbox

        # Normalize bounding box where (0,0) is at lower-left
        normalized_bbox = {
            "xmin": 0,
            "ymin": 0,
            "xmax": xmax - xmin,
            "ymax": ymax - ymin
        }

        # Normalize building geometries
        transformed_buildings = []
        for building in self.building_data:
            if "geometry" in building:
                geom = shape(building["geometry"])
                shifted_geom = translate(geom, xoff=-xmin, yoff=-ymin)

                transformed_buildings.append({
                    "geometry": mapping(shifted_geom),
                    "parcel_id": building["parcel_id"]
                })

        data = {
            "dsm_bbox": normalized_bbox,
            "buildings": transformed_buildings
        }

        if export_format == "json":
            with open(file_name, "w") as f:
                json.dump(data, f, indent=4)
            print(f"Exported data to {file_name}")

        elif export_format == "csv":
            import csv
            with open(file_name, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["parcel_id", "geometry"])
                for building in transformed_buildings:
                    writer.writerow([building["parcel_id"], json.dumps(building["geometry"])])
            print(f"Exported data to {file_name}")

        elif export_format == "dxf":
            doc = ezdxf.new()
            msp = doc.modelspace()

            # Add bounding box as a rectangle
            msp.add_lwpolyline([(0, 0), (normalized_bbox["xmax"], 0),
                                (normalized_bbox["xmax"], normalized_bbox["ymax"]), (0, normalized_bbox["ymax"])],
                               close=True)

            # Add buildings as polylines
            for building in transformed_buildings:
                poly = shape(building["geometry"])
                if poly.geom_type == "Polygon":
                    coords = list(poly.exterior.coords)
                    msp.add_lwpolyline(coords, close=True)

            doc.saveas(file_name)
            print(f"Exported data to {file_name}")

        else:
            print("Unsupported export format. Use 'json', 'csv', or 'dxf'.")


class CHM:
    '''
    Class for creating and managing Canopy Height Models (CHMs) from LiDAR data.
    This class handles downloading and merging LAS/LAZ tiles, filtering vegetation points
    based on classification and NDVI, rasterizing vegetation using interpolation, applying
    smoothing filters, and generating final CHM raster outputs.

    Attributes:
        bbox (tuple):               Bounding box coordinates (min_x, min_y, max_x, max_y) defining the area of interest.
        bufferedbbox (tuple):       Buffered bounding box extended by a fixed margin.
        crs (rasterio.crs.CRS):                         Coordinate reference system used (default EPSG:28992).
        dtm (numpy.ndarray or rasterio object):         Digital Terrain Model raster data.
        dsm (numpy.ndarray or rasterio object):         Digital Surface Model raster data.
        output_folder_chm (str):                        Folder path where CHM outputs are saved.
        gdf (geopandas.GeoDataFrame):                   GeoDataFrame containing tile lookup information.
        chm (numpy.ndarray):                            Initial Canopy Height Model raster array.
        tree_polygons (geopandas.GeoDataFrame):         Polygons representing tree footprints.
        transform (affine.Affine):                      Affine transform for raster coordinates.
        trunk_array (numpy.ndarray):                    Array representing estimated trunk heights.
        original_chm (numpy.ndarray):                   Copy of the original CHM before processing.
        og_polygons (geopandas.GeoDataFrame):           Copy of original tree polygons.
        original_trunk (numpy.ndarray):                 Copy of original trunk array.
    '''
    def __init__(self, bbox, dtm, dsm, trunk_height, output_folder_chm='output', output_folder_las='temp', resolution=0.5, merged_output='pointcloud.las', ndvi_threshold=0.05):
        '''
        Initialize the CHM class with bounding box, DTM, DSM, trunk height and folder paths.

        Parameters:
            bbox (tuple):                                     Bounding box as (min_x, min_y, max_x, max_y).
            dtm (numpy.ndarray or rasterio dataset):          Digital Terrain Model raster.
            dsm (numpy.ndarray or rasterio dataset):          Digital Surface Model raster.
            trunk_height (float):                             Trunk height as a percentage of total tree height (e.g., 20 for 20%).
            output_folder_las (str):                          Folder path for output LAS files.
            input_folder (str):                               Folder path for input files.
            output_folder_chm (str):                          Folder path for CHM-specific output.
            resolution (float, optional):                     Resolution for raster grid cells. Defaults to 0.5.
            merged_output (str, optional):                    Filename for merged LAS output. Defaults to 'pointcloud.las'.
            ndvi_threshold (float, optional):                 NDVI index to use for vegetation extraction.
        '''
        self.bbox = bbox
        self.bufferedbbox = edit_bounds(bbox, 2)
        self.crs = (CRS.from_epsg(28992))
        self.dtm = dtm
        self.dsm = dsm
        self.tree_mask = None
        self.output_folder_chm = output_folder_chm
        self.gdf = gpd.read_file("src/j_dataprep/geotiles/AHN_lookup.geojson")
        self.chm, self.tree_polygons, self.transform = self.init_chm(bbox, output_folder=output_folder_las, input_folder=output_folder_las, merged_output=merged_output,  ndvi_threshold=ndvi_threshold, resolution=resolution)
        self.trunk_array = self.chm * (trunk_height / 100 )
        self.original_chm, self.og_polygons, self.original_trunk = self.chm, self.tree_polygons, self.trunk_array

    def save_las(self, merged_las, veg_points, output_name="veg_points.las"):
        '''
        Save filtered vegetation points as a LAS file.

        Parameters:
            merged_las (laspy.LasData):     Original merged LAS data with header info.
            veg_points (laspy.LasData):     Filtered LAS points representing vegetation.
            output_name (str, optional):    Output filename. Defaults to "veg_points.las".

        Creates the output folder if it does not exist and writes the LAS file.
        '''
        # Create a new LasData object with the same header and filtered points
        vegetation_las = laspy.LasData(merged_las.header)
        vegetation_las.points = veg_points.points.copy()

        # Save to file
        output_path = os.path.join(self.output_folder_chm, output_name)
        os.makedirs(self.output_folder_chm, exist_ok=True)
        vegetation_las.write(output_path)
        print(f"Saved vegetation points to {output_path}")

    def find_tiles(self, x_min, y_min, x_max, y_max):
        '''
        Find geotile names overlapping the specified bounding box.

        Parameters:
            x_min, y_min, x_max, y_max (float):       Coordinates defining the bounding box.

        Returns:
            List[str]:                           List of geotile names that intersect with the bounding box.
        '''
        query_geom = box(x_min, y_min, x_max, y_max)
        matches = self.gdf.sindex.query(
            query_geom)  # predicate="overlaps": tricky i want to still get something if it is all contained in one
        return self.gdf.iloc[matches]["GT_AHNSUB"].tolist()

    @staticmethod
    def filter_points_within_bounds(las_data, bounds):
        '''
        Filter LAS points that lie within the given bounding box.

        Parameters:
            las_data (laspy.LasData):   Input LAS point cloud.
            bounds (tuple):             Bounding box as (x_min, y_min, x_max, y_max).

        Returns:
            laspy.LasData:              Filtered LAS data containing only points within bounds.
        '''
        x_min, y_min, x_max, y_max = bounds
        mask = (
                (las_data.x >= x_min) & (las_data.x <= x_max) &
                (las_data.y >= y_min) & (las_data.y <= y_max)
        )
        return las_data[mask]

    # @staticmethod
    def extract_vegetation_points(self, LasData, ndvi_threshold=0.1, pre_filter=False):
        '''
        Extract vegetation points based on classification and NDVI threshold.

        Parameters:
        - LasData (laspy.LasData):          Input LAS point cloud data.
        - ndvi_threshold (float, optional): NDVI cutoff for vegetation points. Defaults to 0.1.
        - pre_filter (bool, optional):      If True, filter out vegetation points below 1.5m above lowest vegetation point. Defaults to False.

        Returns:
        - veg_points (laspy.LasData):       LAS data filtered to vegetation points based on NDVI and optional height filtering.
        '''

        # Filter points based on classification (vegetation-related classes), note: vegetation classes are empty in AHN4
        possible_vegetation_points = LasData[(LasData.classification == 1) |  # Unclassified
                                             (LasData.classification == 3) |  # Low vegetation
                                             (LasData.classification == 4) |  # Medium vegetation
                                             (LasData.classification == 5)]  # High vegetation

        # Calculate NDVI
        red = possible_vegetation_points.red
        nir = possible_vegetation_points.nir
        ndvi = (nir.astype(float) - red) / (nir + red)

        # Filter the points whose NDVI is greater than the threshold
        veg_points = possible_vegetation_points[ndvi > ndvi_threshold]

        # Option: already filter away the points with a height below 1.5m from the lowest veg point, introduced because
        # of one very large tile (25GN2_24.LAZ)
        if pre_filter:
            heights = veg_points.z
            min_height = heights.min()

            # Filter out points with heights between the minimum height and 1.5 meters
            filtered_veg_points = veg_points[(heights <= min_height) | (heights > 1.5)]
            return filtered_veg_points

        self.save_las(LasData, veg_points)

        return veg_points

    @staticmethod
    def raster_center_coords(min_x, max_x, min_y, max_y, resolution):
        '''
        Compute center coordinates of each cell in a raster grid.

        Parameters:
            min_x, max_x, min_y, max_y (float):  Bounding box coordinates.
            resolution (float):                 Cell size; assumed square cells.

        Returns
        -------
        grid_center_x (np.ndarray)
            X cell center coordinates.
        grid_center_y (np.ndarray)
            Y cell center coordinates.
        '''
        # create coordinates for the x and y border of every cell.
        x_coords = np.arange(min_x, max_x, resolution)  # x coordinates expand from left to right.
        y_coords = np.arange(max_y, min_y, -resolution)  # y coordinates reduce from top to bottom.

        # create center point coordinates for evey cell.
        grid_x, grid_y = np.meshgrid(x_coords, y_coords)
        grid_center_x = grid_x + resolution / 2
        grid_center_y = grid_y - resolution / 2
        return grid_center_x, grid_center_y

    @staticmethod
    def median_filter_chm(chm_array, nodata_value=-9999, size=3):
        '''
        Apply a median filter to smooth the CHM, preserving NoData areas.

        Parameters:
            chm_array (np.ndarray):         CHM raster array.
            nodata_value (float, optional): NoData value in the array. Defaults to -9999.
            size (int, optional):           Median filter size (window). Defaults to 3.

        Returns:
            smoothed_chm (np.ndarray):      Smoothed CHM array with NoData preserved.
        '''
        # Create a mask for valid data
        valid_mask = chm_array != nodata_value

        # Pad the data with nodata_value
        pad_width = size // 2
        padded_chm = np.pad(chm_array, pad_width, mode='constant', constant_values=nodata_value)

        # Apply median filter to padded data
        filtered_padded = median_filter(padded_chm.astype(np.float32), size=size) # median_filter(padded_chm.astype(np.float32), size=size)

        # Remove padding
        smoothed_chm = filtered_padded[pad_width:-pad_width, pad_width:-pad_width]

        # Only keep valid data in smoothed result
        smoothed_chm[~valid_mask] = nodata_value

        return smoothed_chm

    def interpolation_vegetation(self, veg_points, resolution, no_data_value=-9999):
        '''
        Create a vegetation raster by interpolating vegetation points using Laplace interpolation.

        Parameters:
            veg_points (laspy.LasData):       Vegetation points to interpolate.
            resolution (float):               Desired raster resolution.
            no_data_value (int, optional):    Value to assign NoData cells. Defaults to -9999.

        Returns
        -------
        interpolated_grid (np.ndarray):
            Raster grid with interpolated vegetation heights.
        grid_center_xy (tuple of np.ndarray):
            Grid center coordinates (x, y).
        '''
        # bounding box extents minus 0.5 resolution of AHN dataset
        min_x, min_y, max_x, max_y = self.bbox

        # Define size of the region
        x_length = max_x - min_x
        y_length = max_y - min_y

        # Number of rows and columns
        cols = round(x_length / resolution)
        rows = round(y_length / resolution)

        # Initialize raster grid
        veg_raster = np.full((rows, cols), no_data_value, dtype=np.float32)

        # Calculate center coords for each grid cell
        grid_center_xy = self.raster_center_coords(min_x, max_x, min_y, max_y, resolution)

        if veg_points.x.shape[0] == 0:
            print("There are no vegetation points in the current area.")
            veg_raster = np.full((rows, cols), -200, dtype=np.float32)
            return veg_raster, grid_center_xy

        # create the delaunay triangulation
        dt = startinpy.DT()
        dt.insert(veg_points.xyz, "BBox")

        # Flatten the grid to get a list of all center coords
        locs = np.column_stack((grid_center_xy[0].ravel(), grid_center_xy[1].ravel()))

        vegetation_points = np.column_stack((veg_points.x, veg_points.y))
        tree = cKDTree(vegetation_points)

        # Find the distance to the nearest vegetation point for each grid cell
        distances, _ = tree.query(locs, k=1)

        distance_threshold = 1
        # masking cells that exceed threshold
        within_threshold_mask = distances <= distance_threshold
        # Interpolation only for those near
        valid_locs = locs[within_threshold_mask]

        # laplace interpolation
        interpolated_values = dt.interpolate({"method": "Laplace"}, valid_locs)

        # reshape interpolated grid back to og
        interpolated_grid = np.full_like(veg_raster, no_data_value, dtype=np.float32)  # Start with no_data
        interpolated_grid.ravel()[within_threshold_mask] = interpolated_values

        return interpolated_grid, grid_center_xy

    def download_las_tiles(self, matching_tiles, output_folder):
        '''
        Download AHN5 or AHN4 LAZ tiles based on a list of matching tile names.

        Parameters:
            matching_tiles (list of str):       List of tile identifiers (e.g., '31FN2_01') to be downloaded.
            output_folder (str):                Directory where downloaded LAZ files will be saved.

        Returns:
            None
        '''
        base_url_ahn5 = "https://geotiles.citg.tudelft.nl/AHN5_T"
        base_url_ahn4 = "https://geotiles.citg.tudelft.nl/AHN4_T"
        os.makedirs(output_folder, exist_ok=True)

        for full_tile_name in matching_tiles:
            # Extract tile name and sub-tile number
            if '_' in full_tile_name:
                tile_name, sub_tile = full_tile_name.split('_')
            else:
                print(f"Skipping invalid tile entry: {full_tile_name}")
                continue

            sub_tile_str = f"_{int(sub_tile):02}"
            filename = f"{tile_name}{sub_tile_str}.LAZ"
            file_path = os.path.join(output_folder, filename)

            # Skip if already downloaded
            if os.path.exists(file_path):
                print(f"File {file_path} already exists, skipping download.")
                continue

            # Try AHN5
            url = f"{base_url_ahn5}/{filename}"
            try:
                response = requests.get(url)
                response.raise_for_status()
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"Downloaded from AHN5 and saved {file_path}")
                continue
            except requests.exceptions.RequestException as e:
                print(f"AHN5 download failed for {filename}: {e}")

            # AHN4 fallback
            url = f"{base_url_ahn4}/{filename}"
            try:
                response = requests.get(url)
                response.raise_for_status()
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"Downloaded from AHN4 and saved {file_path}")
            except requests.exceptions.RequestException as e:
                print(f"AHN4 download also failed for {filename}: {e}")

    def merge_las_files(self, laz_files, bounds, merged_output):
        '''
        Merge and crop multiple LAZ files into a single LAS file within the specified bounds.

        Parameters:
            laz_files (list of str):        Paths to the input LAZ files.
            bounds (tuple):                 Bounding box (xmin, ymin, xmax, ymax) to crop point clouds.
            merged_output (str or Path):    File path to write the merged output LAS file.

        Returns:
            laspy.LasData:                  Merged and cropped point cloud data.
        '''

        merged_output = Path(merged_output)

        las_merged = None
        all_points = []
        merged_scales = None
        merged_offset = None

        if merged_output.exists():
            with laspy.open(merged_output) as las:
                las_merged = las.read()
            return las_merged

        for file in laz_files:
            with laspy.open(file) as las:
                las_data = las.read()
                cropped_las = self.filter_points_within_bounds(las_data, bounds)

                if las_merged is None:
                    # Initialize merged LAS file using the first input file
                    las_merged = laspy.LasData(las_data.header)
                    las_merged.points = cropped_las.points

                    merged_scales = las_merged.header.scales
                    merged_offset = las_merged.header.offset
                else:

                    scale = las_data.header.scales
                    offset = las_data.header.offsets
                    # Convert integer coordinates to real-world values & Transform into merged coordinate system
                    new_x = ((cropped_las.X * scale[0] + offset[0]) - merged_offset[0]) / merged_scales[0]
                    new_y = ((cropped_las.Y * scale[1] + offset[1]) - merged_offset[1]) / merged_scales[1]
                    new_z = ((cropped_las.Z * scale[2] + offset[2]) - merged_offset[2]) / merged_scales[2]

                    # Copy points and update X, Y, Z
                    new_points = cropped_las.points
                    new_points["X"] = new_x.astype(np.int32)
                    new_points["Y"] = new_y.astype(np.int32)
                    new_points["Z"] = new_z.astype(np.int32)

                    all_points.append(new_points.array)

                    # Final merge step
        if las_merged is not None:
            if all_points:
                all_points.append(las_merged.points.array)

                merged_array = np.concatenate(all_points, axis=0)
                las_merged.points = laspy.ScaleAwarePointRecord(merged_array, las_merged.header.point_format,
                                                                las_merged.header.scales, las_merged.header.offsets)

            las_merged.write(str(merged_output))

        return las_merged

    @staticmethod
    def chm_finish(chm_array, dtm_array,
                   dsm_array, min_height=2, max_height=40):
        '''
        Finalize CHM by removing terrain and filtering by vegetation height.

        Parameters:
            chm_array (np.ndarray):     Initial canopy height model array.
            dtm_array (np.ndarray):     Digital terrain model array.
            dsm_array (np.ndarray):     Digital surface model array.
            min_height (float):         Minimum height threshold to keep vegetation (default = 2).
            max_height (float):         Maximum height threshold to keep vegetation (default = 40).

        Returns:
            np.ndarray: Processed CHM with invalid or noisy values removed.
        '''

        result_array = chm_array - dtm_array
        result_array[(chm_array - dsm_array) < 0.0] = 0
        result_array[(result_array < min_height) | (result_array > max_height)] = 0
        result_array[np.isnan(result_array)] = 0

        return result_array

    def chm_creation(self, LasData, vegetation_data, output_filename, resolution=0.5, smooth=False, nodata_value=-9999,
                     filter_size=3):
        '''
        Create and optionally smooth a CHM from vegetation data, then save it as a GeoTIFF and extract tree polygons.

        Parameters:
            LasData (laspy.LasData):    LAS metadata for writing the output raster.
            vegetation_data (tuple):    Tuple of (veg_raster, grid_centers) for CHM generation.
            output_filename (str):      Path to save the output CHM raster.
            resolution (float):         Spatial resolution of the raster (default = 0.5).
            smooth (bool):              Whether to apply a median filter to the CHM (default = False).
            nodata_value (float):       Value to assign to NoData cells in the raster (default = -9999).
            filter_size (int):          Size of median filter kernel (default = 3).

        Returns:
            tuple: (chm_array, polygons, transform) where polygons are tree regions as GeoJSON-like dicts.
        '''

        veg_raster = vegetation_data[0]
        grid_centers = vegetation_data[1]
        top_left_x = grid_centers[0][0, 0] - resolution / 2
        top_left_y = grid_centers[1][0, 0] + resolution / 2

        transform = Affine.translation(top_left_x, top_left_y) * Affine.scale(resolution, -resolution)

        if smooth:
            veg_raster = self.median_filter_chm(veg_raster, nodata_value=nodata_value, size=filter_size)
        print(veg_raster.shape)

        veg_raster = self.chm_finish(veg_raster, self.dtm, self.dsm)

        write_output(LasData, self.crs, veg_raster, transform, output_filename, True)

        # create the polygons
        labeled_array, num_clusters = label(veg_raster > 0)
        shapes_gen = shapes(labeled_array.astype(np.uint8), mask=(labeled_array > 0), transform=transform)
        polygons = [
            {"geometry": shape(geom), "polygon_id": int(value)}
            for geom, value in shapes_gen if value > 0
        ]

        return veg_raster, polygons, transform

    def init_chm(self, bbox, output_folder="output", input_folder="temp",  merged_output="output/pointcloud.las",  smooth_chm=True, resolution=0.5, ndvi_threshold=0.05, filter_size=3):
        '''
        Initialize and generate a CHM by downloading, merging, filtering, and interpolating LiDAR data.

        Parameters:
            bbox (tuple):           Bounding box (xmin, ymin, xmax, ymax) for the area of interest.
            output_folder (str):    Directory for saving output files (default = 'output').
            input_folder (str):     Directory where LAZ files are stored or downloaded (default = 'temp').
            merged_output (str):    Path to save the merged LAS point cloud (default = 'output/pointcloud.las').
            smooth_chm (bool):      Whether to smooth the CHM using a median filter (default = True).
            resolution (float):     Output raster resolution (default = 0.5).
            ndvi_threshold (float): NDVI threshold for filtering vegetation points (default = 0.05).
            filter_size (int):      Size of median filter kernel (default = 3).

        Returns:
            tuple: (chm_array, polygons, transform) or (None, None, None) if process fails.
        '''

        matching_tiles = self.find_tiles(*self.bufferedbbox)
        print("Tiles covering the area:", matching_tiles)

        existing_tiles = {
            os.path.splitext(file)[0] for file in os.listdir(input_folder) if file.endswith(".LAZ")
        }

        missing_tiles = [tile for tile in matching_tiles if tile not in existing_tiles]

        if missing_tiles:
            print("Missing tiles:", missing_tiles)
            self.download_las_tiles(missing_tiles, input_folder)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        laz_files = [
            os.path.join(input_folder, file)
            for file in os.listdir(input_folder)
            if file.endswith(".LAZ") and os.path.splitext(file)[0] in matching_tiles
        ]

        if not laz_files:
            print("No relevant LAZ files found in the input folder or its subfolders.")
            return None, None, None
        las_data = self.merge_las_files(laz_files, self.bufferedbbox, merged_output)

        if las_data is None:
            print("No valid points found in the given boundary.")
            return None, None, None

        # Extract vegetation points
        veg_points = self.extract_vegetation_points(las_data, ndvi_threshold=ndvi_threshold, pre_filter=False)

        vegetation_data = self.interpolation_vegetation(veg_points, resolution)
        output_filename = os.path.join(self.output_folder_chm, f"CHM.TIF")

        # Create the CHM and save it
        chm, polygons, transform = self.chm_creation(las_data, vegetation_data, output_filename, resolution=resolution, smooth=smooth_chm, nodata_value=-9999,
                     filter_size=filter_size)

        return chm, polygons, transform

    def remove_trees(self, tree_id):
        '''
        Remove a tree (or cluster of trees) from the CHM and trunk arrays by polygon ID.

        Parameters:
            tree_id (int): Identifier of the tree polygon to remove.

        Returns:
            None
        '''
        target_polygons = [tree["geometry"] for tree in self.tree_polygons if tree["polygon_id"] == tree_id]

        if not target_polygons:
            print(f"No trees found with ID: {tree_id}")
            return

        tree_mask = geometry_mask(
            geometries=target_polygons,
            transform=self.transform,
            invert=True,
            out_shape=self.chm.shape
        )
        self.tree_mask = tree_mask
        self.chm = np.where(tree_mask, 0, self.chm)
        self.trunk_array = np.where(tree_mask, 0, self.trunk_array)
        write_output(None, self.crs, self.chm, self.transform, "output/updated_chm.tif")

    def insert_tree(self, position, height, crown_radius, resolution=0.5, trunk_height=5.0, tree_shape='parabolic', randomness=0.8, canopy_base_height=0.0):
        '''
        Insert a parametric tree model into the CHM and trunk height array at the specified location.

        Parameters:
            position (tuple): (row, col)    indices for tree center insertion.
            height (float):                 Total height of the tree.
            crown_radius (float):           Radius of the crown in real-world units.
            resolution (float):             Real-world size of each pixel (default = 0.5).
            trunk_height (float):           Height of the trunk (default = 0.0).
            tree_shape (str):                     Canopy shape type ('gaussian', 'cone', 'parabolic', 'hemisphere').
            randomness (float):             Standard deviation for random noise applied to canopy (default = 0.8).
            canopy_base_height (float):     Height at which the canopy starts above the trunk (default = 0.0).

        Returns:
            None: Updates CHM and trunk arrays in-place.
        '''
        new_array = np.copy(self.chm)
        new_trunk_array = np.copy(self.trunk_array)

        crown_radius_px = crown_radius / resolution
        size = int(crown_radius_px * 2.5)

        # Calculate the distance from surrounding cells to the tree center
        x = np.arange(-size//2, size//2 +1)
        y = np.arange(-size//2, size//2 + 1)
        X, Y = np.meshgrid(x, y)
        distance = np.sqrt(X**2 + Y**2)

        canopy_start_height = trunk_height + canopy_base_height

        # Create canopy shape
        if tree_shape == 'gaussian':
            canopy = (height - canopy_start_height) * np.exp(
                -distance ** 2 / (2 * (crown_radius_px / 2) ** 2)) + canopy_start_height
        elif tree_shape == 'cone':
            canopy = np.clip((height - canopy_start_height) * (1 - distance / crown_radius_px), 0,
                             height - canopy_start_height) + canopy_start_height
        elif tree_shape == 'parabolic':
            canopy = (height - canopy_start_height) * (1 - (distance / crown_radius_px) ** 2)
            canopy = np.clip(canopy, 0, height - canopy_start_height) + canopy_start_height
        elif tree_shape == 'hemisphere':
            canopy = np.sqrt(np.clip(crown_radius_px ** 2 - distance ** 2, 0, None)) / crown_radius_px * (
                        height - canopy_start_height) + canopy_start_height
        else:
            raise ValueError("Unsupported tree shape.")

        mask = (distance <= crown_radius_px) & (canopy >= canopy_start_height)

        noise = np.random.normal(0, randomness, canopy.shape)
        canopy[mask] += noise[mask]

        canopy[~mask] = 0
        canopy = np.clip(canopy, 0, None)

        # Define insertion window
        row, col = position
        half_size = size // 2
        r_start = max(0, row - half_size)
        r_end = min(self.chm.shape[0], row + half_size)
        c_start = max(0, col - half_size)
        c_end = min(self.chm.shape[1], col + half_size)

        # Calculate actual insertion indices
        canopy_r_start = half_size - (row - r_start)
        canopy_r_end = canopy_r_start + (r_end - r_start)
        canopy_c_start = half_size - (col - c_start)
        canopy_c_end = canopy_c_start + (c_end - c_start)

        # Blend
        new_array[r_start:r_end, c_start:c_end] = np.maximum(
            self.chm[r_start:r_end, c_start:c_end],
            canopy[canopy_r_start:canopy_r_end, canopy_c_start:canopy_c_end]
        )

        existing = self.trunk_array[r_start:r_end, c_start:c_end]

        new_trunk_array[r_start:r_end, c_start:c_end] = np.where(
            mask[canopy_r_start:canopy_r_end, canopy_c_start:canopy_c_end] & (trunk_height != 0),
            trunk_height,
            existing
        )

        tree_mask = (new_array > self.chm)
        shapes_gen = shapes(tree_mask.astype(np.uint8), mask=tree_mask, transform=self.transform)
        tree_polygons = [
            {"geometry": mapping(shape(geom)), "tree_id": str(uuid.uuid4())[:8]}
            for geom, value in shapes_gen if value > 0
        ]

        self.tree_polygons.extend(tree_polygons)
        self.chm, self.trunk_array = new_array, new_trunk_array

    def insert_random_tree(self, position,
                           height_range=(12.0, 18.0),
                           crown_radius_range=(2.0, 5.0),
                           trunk_height_range=(4.0, 12.0),
                           canopy_base_range=(0.0, 0.8),
                           resolution=0.5,
                           min_canopy_height = 3.0,
                           tree_shape='parabolic',
                           randomness=0.8):
        '''
        Insert a tree with randomized dimensions and properties at a specified position. Random values are drawn from
        The specified ranges for height, crown radius, trunk height, and canopy base height. Ensures that the canopy height meets
        a minimum value.

        Parameters:
            position (tuple):             (row, col) position where the tree will be placed.
            height_range (tuple):         Range of tree height in meters.
            crown_radius_range (tuple):   Range of crown radius in meters.
            trunk_height_range (tuple):   Range of trunk height in meters.
            canopy_base_range (tuple):    Range of canopy base height in meters.
            resolution (float):           Spatial resolution of the map.
            min_canopy_height (float):    Minimum allowable canopy height (tree - trunk).
            shape (str):                   Shape type of the canopy (e.g., 'parabolic').
            randomness (float):           Amount of shape noise to apply.

        Returns:
            None
        '''

        tree_height =  random.uniform(*height_range)
        crown_radius = random.uniform(*crown_radius_range)

        trunk_height = random.uniform(*trunk_height_range)
        trunk_height = min(trunk_height, tree_height - min_canopy_height)

        canopy_base_height = random.uniform(*canopy_base_range)

        self.insert_tree(
            position=position,
            height=tree_height,
            crown_radius=crown_radius,
            trunk_height=trunk_height,
            canopy_base_height=canopy_base_height,
            resolution=resolution,
            tree_shape=tree_shape,
            randomness=randomness
        )

    def insert_type_tree(self, age, position, tree_type="fraxinus", resolution=0.5, canopy_base=0.0):
        '''
        Insert a tree of a specific type and age using pre-defined growth parameters.

        Parameters are loaded from a JSON database and used to compute the
        trunk height and crown radius. The canopy type is fixed.

        Parameters:
            age (int, str):               Age of the tree in years or life stage (young, early_mature, mature, late_mature, semi_mature).
            position (tuple):            (row, col) position where the tree will be placed.
            tree_type (str):              Tree species (default is 'fraxinus').
            resolution (float):          Spatial resolution of the map.
            canopy_base (float):         Height of the base of the canopy in meters.

        Returns:
            None

        Raises:
        -   ValueError: If no data exists for the specified tree age.
        '''

        tree_genus = tree_type.lower()
        if tree_genus =="fraxinus" and type(age) == int:
            # Find the tree data for the specified age
            with open("src/databases/fraxinus_excelsior_database.json") as f:
                tree_db = json.load(f)

            tree_data = next((item for item in tree_db if item["age"] == age), None)

            if not tree_data:
                raise ValueError(f"No data available for age {age}")

            # Extract the relevant attributes from the tree data
            tree_height = tree_data["tree ht"]
            crown_height = tree_data["crown ht"]
            crown_dia = tree_data["crown dia"]

            # Calculate derived values
            trunk_height = max(0, tree_height - crown_height)
            crown_radius = crown_dia / 2

        else:
            # Find the tree data for the specified age
            with open("src/databases/obard_trees.json") as f:
                tree_db = json.load(f)

            tree_data = next(
                (item for item in tree_db if item["age"] == age and item["genus"] == tree_genus),
                None
            )

            if not tree_data:
                raise ValueError(f"No data available for age {age}")

            # Extract the relevant attributes from the tree data
            tree_height = tree_data["tree ht"]
            trunk_height = tree_data["trunk ht"]
            crown_dia = tree_data["crown dia"]

            crown_radius = crown_dia / 2

        if tree_type =="fraxinus" or tree_type== "tilia" or tree_type== "salix" or tree_type =="platanus":
            tree_shape = 'parabolic'
        if tree_type == "quercus":
            tree_shape = 'hempisphere'

        # Set defaults for type and randomness

        randomness = 0.8  # Fixed randomness

        # Insert the tree with the calculated values
        self.insert_tree(
            position=position,
            height=tree_height,
            crown_radius=crown_radius,
            trunk_height=trunk_height,
            canopy_base_height=canopy_base,
            resolution=resolution,
            tree_shape=tree_shape,
            randomness=randomness
        )
