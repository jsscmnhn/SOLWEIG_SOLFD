import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm

import requests
from io import BytesIO
import gzip
import os
import pandas as pd
import numpy as np
import rasterio
from shapely.geometry import mapping, Polygon, MultiPolygon, LineString, MultiLineString
from rasterio.features import geometry_mask, shapes
from shapely.geometry import shape,box
from scipy.ndimage import label
import uuid
from rtree import index
import ezdxf
import json
from shapely.affinity import translate

class LandCover:
    """
    A class to process and rasterize land cover data from the PDOK Top10NL API, building geometries, and a DTM raster,
     producing a land cover classification raster.

    Attributes:
        bbox (tuple):                           Bounding box (xmin, ymin, xmax, ymax) in EPSG:28992.
        crs (str):                              CRS for requests and raster alignment, default is EPSG:28992.
        resolution (float):                     Output raster resolution in meters.
        main_road (int):                        Default landcover code for hardened roads.
        dtm_dataset (rasterio.open):            DTM as rasterio dataset for alignment and dimensions.
        base_url (str):                         Base URL for the PDOK Top10NL API.
        water_mask, building_mask (ndarray):    Optional binary masks.
        buildings_path (str):                   Optional path to building vector data.
        layer (str):                            Layer name if using GPKG for building data.
        building_data (list):                   Optional preloaded building geometries.
        landcover_path (str):                   Path to JSON file mapping landcover codes.
        landcover_mapping (dict):               Mapping of landcover types to codes.
        buildings, water, roads, terrains (list): Extracted vector features.
        array (ndarray):                        Output raster array of landcover values.
        og_landcover (ndarray):                 Original unmodified landcover array.
        landcover_withoutbuild (ndarray):       Landcover array before building insertion.
        transform:                              Raster transform from DTM dataset.
    """
    def __init__(self, bbox, crs="http://www.opengis.net/def/crs/EPSG/0/28992", main_roadtype=0, resolution=0.5, building_data=None, dataset=None,
                 dataset_path=None, buildings_path=None, layer=None, landcover_path="src/j_dataprep/landcover.json"):
        self.bbox = bbox
        self.transform = None
        self.crs = crs
        self.resolution = resolution
        self.main_road = main_roadtype
        self.dtm_dataset = self.dtm_dataset_prep(dataset_path, dataset)
        self.base_url = "https://api.pdok.nl/brt/top10nl/ogc/v1"

        self.water_mask = None
        self.building_mask = None
        self.buildings_path = buildings_path
        self.layer = layer
        self.building_data = building_data

        self.landcover_path = landcover_path
        self.landcover_mapping = self.load_landcover_mapping()

        self.buildings = None
        self.water = None
        self.roads = None
        self.terrains = None
        self.get_features()

        self.array = self.convert_to_raster()
        self.og_landcover = self.array
        self.landcover_withoutbuild = None


    def dtm_dataset_prep(self, dataset_path, dataset):
        """
        Prepare and return a DTM rasterio dataset object.

        Parameters:
            dataset_path (str or Path, optional): File path to the DTM raster.
            existing_dataset (rasterio.io.DatasetReader, optional): An already-open rasterio dataset.

        Returns:
            rasterio.io.DatasetReader: The prepared DTM dataset.
        """
        if dataset is not None:
            return dataset
        elif dataset_path is not None:
            return rasterio.open(dataset_path)

    def load_landcover_mapping(self):
        """
        Load land cover mappings from a JSON file with explicit UTF-8 encoding.

        Returns:
            dict: A dictionary representing land cover categories and their
            corresponding code mappings. For example:
            {
                "terrain": {"grasland": 5, "akker": 6, ...},
                "road": {"onverhard": 3, "halfverhard": 4, ...}
            }
        """
        with open(self.landcover_path, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)

    def get_landcover_code(self, land_type, isroad=False):
        """
        Retrieve the numeric land cover code for a given land type.

        Parameters:
            land_type (str):            The land type string to look up .
            isroad (bool, optional):    Whether to look up in the "road" category (True) or "terrain" category (False). Defaults to False.

        Returns:
            int:                        The land cover code for the given land type.
                                        Returns -1 if the land type is not found in the specified category.
        """
        category = "road" if isroad else "terrain"
        return self.landcover_mapping.get(category, {}).get(land_type.lower(), -1)

    def get_top10nl(self, item_type):
        """
        Retrieve features from the TOP10NL API for the specified item type within the bounding box.

        Parameters:
            item_type (str): The name of the TOP10NL collection to query (e.g., "waterdeel_vlak", "wegdeel_vlak", "terrein_vlak").

        Returns:
            dict:   A GeoJSON-like dictionary with key "features" containing a list of feature dictionaries as returned by the API.
        """
        features = []
        url = f"{self.base_url}/collections/{item_type}/items?bbox={self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]}&bbox-crs={self.crs}&crs={self.crs}&limit=1000&f=json"

        while url:
            response = requests.get(url)
            if response.status_code != 200:
                print(f"Error: {response.status_code}, {response.text}")
                break

            data = response.json()
            features.extend(data.get("features", []))

            # Look for the "next" link
            next_link = next(
                (link["href"] for link in data.get("links", []) if link.get("rel") == "next"),
                None
            )
            url = next_link  # If None, loop will exit

        return {"features": features}

    def process_water_features(self):
        """
        Process and filter water features from TOP10NL data.
        Filters out features with 'hoogteniveau' == -1 and keeps only Polygon or MultiPolygon geometries (line geometries are buffered).

        Returns:
            list: A list of GeoJSON-like feature dictionaries representing water areas. Each feature has keys "type", "geometry", and "properties".
        """
        waterdata_vlak = self.get_top10nl("waterdeel_vlak")
        waterdata_lijn = self.get_top10nl("waterdeel_lijn")
        water_features = []

        for wat in waterdata_vlak.get("features", []):
            geom = shape(wat['geometry'])
            properties = wat.get("properties", {})
            if properties.get("hoogteniveau") != -1 and isinstance(geom, (Polygon, MultiPolygon)):
                water_features.append({"type": "Feature", "geometry": mapping(geom), "properties": properties})

        for wat in waterdata_lijn.get("features", []):
            geom = shape(wat['geometry']).buffer(0.75)
            properties = wat.get("properties", {})
            if properties.get("hoogteniveau") != -1 and isinstance(geom, (Polygon, MultiPolygon)):
                water_features.append({"type": "Feature", "geometry": mapping(geom), "properties": properties})

        return water_features

    def process_terrain_features(self):
        """
        Process terrain features from TOP10NL data and map land use types to codes.

        Returns:
            list: A list of GeoJSON-like feature dictionaries representing terrain areas. Each feature contains "geometry" and "properties" with a "landuse" code.
        """
        terreindata = self.get_top10nl("terrein_vlak")
        terrain_features = []

        landuse_terrain_mapping = self.landcover_mapping.get("terrain", {})

        for ter in terreindata.get("features", []):
            geom = shape(ter['geometry'])
            properties = ter.get("properties", {})
            landusetype = properties.get("typelandgebruik", "").lower()
            landuse_value = landuse_terrain_mapping.get(landusetype, -1)

            new_properties = {"landuse": landuse_value} if landuse_value != -1 else {}

            if isinstance(geom, (Polygon, MultiPolygon)):
                terrain_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": new_properties
                })
        return terrain_features

    def process_road_features(self):
        """
        Process road features from TOP10NL data and map road surface types to codes.

        Returns:
            list: A list of GeoJSON-like feature dictionaries representing roads. Each feature contains "geometry" and "properties" with a "landuse" code.
        """
        wegdata = self.get_top10nl("wegdeel_vlak")
        road_features = []

        landuse_road_mapping = self.landcover_mapping.get("road", {})

        for road in wegdata.get("features", []):
            geom = shape(road['geometry'])
            properties = road.get("properties", {})

            verhardingstype = properties.get("verhardingstype", "").lower()

            if verhardingstype == "verhard":
                # Use the landuse value mapped from self.roadtype
                landuse_value = self.main_road
            else:
                landuse_value = landuse_road_mapping.get(verhardingstype, -1)

            new_properties = {"landuse": landuse_value} if landuse_value != -1 else {}

            if isinstance(geom, (Polygon, MultiPolygon)):
                road_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": new_properties
                })

        return road_features

    def get_features(self):
        self.terrains = self.process_terrain_features()
        self.roads = self.process_road_features()
        self.water = self.process_water_features()
        self.buildings = self.load_buildings()


    def load_buildings(self):
        """
        Load building features from a GeoPackage or use preloaded data if available.

        Returns:
            list: A list of dictionaries with keys:
                - "geometry": GeoJSON-like geometry dictionary of the building footprint
                - "parcel_id": The building parcel identifier string
            Returns an empty list if no building data or path is provided.
        """
        if self.building_data is not None:
            return self.building_data
        elif not self.buildings_path or not self.layer:
            return []
        buildings_gdf = gpd.read_file(self.buildings_path, layer=self.layer)
        return [{"geometry": mapping(geom), "parcel_id": identificatie} for geom, identificatie in
                zip(buildings_gdf.geometry, buildings_gdf["identificatie"])]

    def visualize_raster(self, raster_array):
        """
        Visualize the landcover raster.

        Parameters:
            raster_array (ndarray):     Array of the landcover raster.
        Returns:
            None, shows a Matplotlib plot for the raster array
        """
        cmap = ListedColormap(["purple", "grey", "black", "brown", "tan", "yellow", "green", "tan", "cyan"])
        categories = [-9999, 0, 1, 2, 3, 4, 5, 6, 7, 8]
        norm = BoundaryNorm(categories, cmap.N)
        plt.figure(figsize=(6, 6))
        img = plt.imshow(raster_array, cmap=cmap, norm=norm, interpolation='nearest')
        cbar = plt.colorbar(img, ticks=categories)
        cbar.set_label("Land Cover Type")
        plt.title("Land Cover")
        plt.show()

    def convert_to_raster(self):
        """
        Rasterize terrain, road, water, and building features onto the DTM grid. Applies land cover codes to a numpy
        array according to feature geometries.

        Returns:
            np.ndarray:         A 2D numpy array with land cover codes assigned per grid cell.
                                The array uses -9999 for nodata values where no features are present.
        """
        array = self.dtm_dataset.read(1)
        transform = self.dtm_dataset.transform
        self.transform = transform

        array.fill(-9999)

        for ter in self.terrains:
            geom = shape(ter['geometry'])
            landuse = ter['properties'].get('landuse', None)
            if landuse is not None:
                landuse_mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                array = np.where(landuse_mask, array, landuse)

        for road in self.roads:
            geom = shape(road['geometry'])
            landuse_road = road['properties'].get('landuse', None)
            if landuse_road is not None:
                road_mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                array = np.where(road_mask, array, landuse_road)

        water_geometries = [shape(wat['geometry']) if isinstance(shape(wat['geometry']),
                                                                             (LineString, MultiLineString)) else shape(
            wat['geometry']) for wat in self.water]
        if not water_geometries:
            print("No valid water geometries found. Skipping water rasterization.")
        else:
            water_mask = geometry_mask(water_geometries, transform=transform, invert=False, out_shape=array.shape)
            self.water_mask = water_mask
            array = np.where(water_mask, array, 7)

        self.landcover_withoutbuild = array

        building_geometries = [shape(building['geometry']) for building in self.buildings]
        if not building_geometries:
            print("No valid building geometries found. Skipping building rasterization.")
        else:
            building_mask = geometry_mask(building_geometries, transform=transform, invert=False, out_shape=array.shape)
            self.building_mask = building_mask
            array = np.where(building_mask, array, 2)

        self.visualize_raster(array)
        return array

    def save_raster(self, name, change_nodata):
        """
        Save the current raster array to a GeoTIFF file.

        Parameters:
            name (str):             The output file path.
            change_nodata (bool):   If True, nodata value is forced to -9999. Otherwise, uses the nodata value from the original dataset if present.

        Returns:
            None
        """
        crs = self.dtm_dataset.crs
        nodata = self.dtm_dataset.nodata

        output_file = name
        output = self.array
        output = np.squeeze(output)
        # Set the nodata value: use -9999 if nodata_value is True or dataset does not have nodata.
        if change_nodata:
            nodata_value = -9999
        else:
            try:
                nodata_value = nodata
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
                           transform=self.transform) as dst:
            dst.write(output, 1)
        print("File written to '%s'" % output_file)

    def update_build_landcover(self, new_building_data):
        """
        Update the raster array by rasterizing new building geometries with landcover code 2.

        Parameters:
            new_building_data (list):   List of building feature dictionaries with "geometry".

        Returns:
            None
        """
        building_geometries = [shape(building['geometry']) for building in new_building_data]
        if not building_geometries:
            print("No valid building geometries found. Skipping building rasterization.")
        else:
            building_mask = geometry_mask(building_geometries, transform=self.transform, invert=False,
                                          out_shape=self.array.shape)
            self.array = np.where(building_mask, self.array, 2)

    def update_landcover(self, land_type, input_array):
        """
        Update the raster array for cells where input_array > -1 with the given land type code.

        Parameters:
            land_type (int):            The land cover code to set.
            input_array (np.ndarray):   Boolean or integer mask array indicating which cells to update.

        Returns:
            None
        """
        to_update = input_array > -1
        self.array[to_update] = land_type


    def export_context(self, file_name, export_format="dxf"):
        """
        Export the current land cover and building context.dxf to a file in specified format.

        Parameters:
            file_name (str): The output file path.
            export_format (str, optional): Format of the export. Options are "json", "csv", or "dxf". Defaults to "dxf".

        Returns:
            None
        """

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
        for building in self.buildings:
            if "geometry" in building:
                geom = shape(building["geometry"])
                shifted_geom = translate(geom, xoff=-xmin, yoff=-ymin)

                transformed_buildings.append({
                    "geometry": mapping(shifted_geom),
                    "parcel_id": building["parcel_id"]
                })

        transformed_water = []
        transformed_roads = []
        transformed_terrain = []

        def transform_features(features, target_list):
            for feature in features:
                geom = shape(feature["geometry"])
                shifted_geom = translate(geom, xoff=-xmin, yoff=-ymin)
                target_list.append({
                    "geometry": mapping(shifted_geom),
                    "properties": feature["properties"]
                })

        transform_features(self.water, transformed_water)
        transform_features(self.roads, transformed_roads)
        transform_features(self.terrains, transformed_terrain)

        data = {
            "dsm_bbox": normalized_bbox,
            "buildings": transformed_buildings,
            "water": transformed_water,
            "roads": transformed_roads,
            "terrain": transformed_terrain
        }

        if export_format == "json":
            with open(file_name, "w") as f:
                json.dump(data, f, indent=4)
            print(f"Exported data to {file_name}")

        elif export_format == "csv":
            import csv
            with open(file_name, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["type", "geometry", "properties"])
                for category, features in [("building", transformed_buildings),
                                           ("water", transformed_water),
                                           ("road", transformed_roads),
                                           ("terrain", transformed_terrain)]:
                    for feature in features:
                        writer.writerow([category, json.dumps(feature["geometry"]), json.dumps(feature["properties"])])
            print(f"Exported data to {file_name}")

        elif export_format == "dxf":
            doc = ezdxf.new()
            msp = doc.modelspace()

            # Add bounding box as a rectangle
            msp.add_lwpolyline([(0, 0), (normalized_bbox["xmax"], 0),
                                (normalized_bbox["xmax"], normalized_bbox["ymax"]), (0, normalized_bbox["ymax"])],
                               close=True, dxfattribs={"color": 7})


            # Add water, roads, and terrain as polylines
            def add_features_to_dxf(features, color):
                for feature in features:
                    poly = shape(feature["geometry"])
                    if poly.geom_type == "Polygon":
                        coords = list(poly.exterior.coords)
                        msp.add_lwpolyline(coords, close=True, dxfattribs={"color": color})

            add_features_to_dxf(transformed_buildings, 1)
            add_features_to_dxf(transformed_water, 5)
            add_features_to_dxf(transformed_roads, 0)
            add_features_to_dxf(transformed_terrain, 3)

            doc.saveas(file_name)
            print(f"Exported data to {file_name}")

        else:
            print("Unsupported export format. Use 'json', 'csv', or 'dxf'.")



class Buildings:
    def __init__(self, bbox, wfs_url="https://data.3dbag.nl/api/BAG3D/wfs", layer_name="BAG3D:lod13", gpkg_name="buildings", output_folder = "output", output_layer_name="buildings"):
        self.bbox = bbox
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
                "BBOX": f"{self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]},urn:ogc:def:crs:EPSG::28992",
                "COUNT": count,
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
        if buildings_gdf is None:
            if buildings_path is not None:
                buildings_gdf = gpd.read_file(buildings_path, layer=layer)
            else: return None

        return [{"geometry": mapping(geom), "parcel_id": identificatie} for geom, identificatie in
                zip(buildings_gdf.geometry, buildings_gdf["identificatie"])]

    def remove_buildings(self, identification):
        self.removed_buildings.append(identification)

    def retrieve_buildings(self, identification):
        self.removed_buildings.remove(identification)

    def insert_user_buildings(self, highest_array, transform, footprint_array=None):
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
        self.removed_user_buildings.append(identification)

    def retrieve_user_buildings(self, identification):
        self.removed_user_buildings.remove(identification)


if __name__ == "__main__":


    # bbox_dict = {
    #     'historisch': [(175905, 317210, 176505, 317810), (84050, 447180, 84650, 447780), (80780, 454550, 81380, 455150),
    #                    (233400, 581500, 234000, 582100), (136600, 455850, 137200, 456450),
    #                    (121500, 487000, 122100, 487600)
    #                    ],
    #     'tuindorp': [(76800, 455000, 78200, 455700), (152600, 463250, 153900, 463800), (139140, 469570, 139860, 470400),
    #                  (190850, 441790, 191750, 442540), (113100, 551600, 113650, 552000), (32050, 391900, 32850, 392500)
    #
    #                  ],
    #     'vinex': [(146100, 486500, 147000, 487400), (153750, 467550, 154650, 468450), (115300, 517400, 116100, 518250),
    #               (102000, 475900, 103100, 476800), (160750, 388450, 161650, 389350), (84350, 449800, 85250, 450700)
    #
    #               ],
    #     'volkswijk': [(104200, 490550, 105100, 491450), (78200, 453900, 79100, 454800), (83500, 447020, 84050, 447900),
    #                   (136200, 456500, 137100, 457300), (182700, 579200, 183800, 579750),
    #                   (233400, 582800, 234300, 583700)
    #
    #                   ],
    #     'bloemkool': [(81700, 427490, 82700, 428200), (84050, 444000, 84950, 444900), (116650, 518700, 117550, 519600),
    #                   (235050, 584950, 235950, 585850), (210500, 473900, 211400, 474800),
    #                   (154700, 381450, 155700, 382150)
    #
    #                   ],
    #
    #     'stedelijk': [
    #         (90300, 436900, 91300, 437600), (91200, 438500, 92100, 439300), (121350, 483750, 122250, 484650),
    #         (118400, 486400, 119340, 487100)
    #     ]
    # }

    # bbox_list = [(120000, 485700, 120126, 485826), (120000, 485700, 120251, 485951), (120000, 485700, 120501, 486201), (120000, 485700, 120751, 486451), (120000, 485700, 121001, 486701), (120000, 485700, 121501, 487201) ]
    # folder_list = ['250', '500', '1000', '1500', '2000', '3000']

    bbox_list = [ (120000, 485700, 121501, 487201)]
    folder_list = ['3000']
    crs = "http://www.opengis.net/def/crs/EPSG/0/28992"

    D = 'D'
    i = 0
    for folder in folder_list:
        dataset_path = f"{D}:/Geomatics/optimization_tests/{folder}/final_dsm_over.tif"
        buildings_path = f"{D}:/Geomatics/optimization_tests/{folder}/buildings.gpkg"
        output = f"{D}:/Geomatics/optimization_tests/{folder}/landcover.tif"
        landcover = LandCover(bbox_list[i], crs, dataset_path=dataset_path, buildings_path=buildings_path, layer="buildings")
        landcover.save_raster(output, False)
        i += 1

    # crs = "http://www.opengis.net/def/crs/EPSG/0/28992"
    # for nbh_type in ['historisch', 'tuindorp', 'vinex', 'volkswijk', 'bloemkool']:
    #     for i in [0, 1, 2, 3, 4, 5]:
    #         output =  f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/landcover_stone.tif"
    #         bbox = bbox_dict[nbh_type][i]
    #         dataset_path = f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/final_dsm_over.tif"
    #         buildings_path = (f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/buildings.gpkg")
    #         landcover = LandCover(bbox, crs, dataset_path=dataset_path, buildings_path=buildings_path, layer="buildings")
    #         landcover.save_raster(output, False)
    #
    # for nbh_type in ['stedelijk']:
    #     for i in [0, 1, 2, 3]:
    #         output =  f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/landcover_stone.tif"
    #         bbox = bbox_dict[nbh_type][i]
    #         dataset_path = f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/final_dsm_over.tif"
    #         buildings_path = (f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/buildings.gpkg")
    #         landcover = LandCover(bbox, crs, dataset_path=dataset_path, buildings_path=buildings_path, layer="buildings")
    #         landcover.save_raster(output, False)


    # crs = "http://www.opengis.net/def/crs/EPSG/0/28992"
    # for nbh_type in ['vinex', 'volkswijk', 'bloemkool']:
    #     for i in [0, 1, 2, 3, 4, 5]:
    #         output =  f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/landcover_stone.tif"
    #         bbox = bbox_dict[nbh_type][i]
    #         dataset_path = f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/final_dsm_over.tif"
    #         buildings_path = (f"E:/Geomatics/thesis/_analysisfinal/{nbh_type}/loc_{i}/buildings.gpkg")
    #         landcover = LandCover(bbox, crs, dataset_path=dataset_path, buildings_path=buildings_path, layer="buildings")
    #         landcover.save_raster(output, False)
    #
