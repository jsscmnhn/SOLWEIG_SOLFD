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
        main_road (int):                        Default landcover code for hardened roads if TOP10NL is chosen as source.
        dtm_dataset (rasterio.open):            DTM as rasterio dataset for alignment and dimensions.
        base_url_top (str):                     Base URL for the PDOK Top10NL API.
        base_url_bgt (str):                     Base URL for the PDOK Top10NL API.
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
    #src/j_dataprep/
    def __init__(self, bbox, crs="http://www.opengis.net/def/crs/EPSG/0/28992", use_bgt=True, main_roadtype=0, resolution=0.5, building_data=None, dataset=None,
                 dataset_path=None, buildings_path=None, layer=None, nodata_fill=0, roads_on_top=True, landcover_path_bgt="src/databases/landcover_bgt.json",  landcover_top="src/databases/landcover_top.json"):
        self.bbox = bbox
        self.transform = None
        self.crs = crs
        self.resolution = resolution
        self.main_road = main_roadtype
        self.dtm_dataset = self.dtm_dataset_prep(dataset_path, dataset)
        self.base_url_top = "https://api.pdok.nl/brt/top10nl/ogc/v1"
        self.base_url_bgt = "https://api.pdok.nl/lv/bgt/ogc/v1"
        self.water_mask = None
        self.building_mask = None
        self.buildings_path = buildings_path
        self.layer = layer
        self.building_data = building_data

        self.use_bgt = use_bgt
        self.roads_on_top = roads_on_top
        self.nodata_fill = nodata_fill
        self.landcover_path = landcover_path_bgt if use_bgt else landcover_top
        self.landcover_mapping = self.load_landcover_mapping()

        self.buildings = None
        self.water = None
        self.roads = None
        self.terrains = None
        self.brug = None
        self.get_features()

        self.array = self.convert_to_raster_bgt() if use_bgt else self.convert_to_raster_top()
        self.og_landcover = self.array
        self.landcover_withoutbuild = None

    @staticmethod
    def filter_active_bgt_features(features):
        '''
        Filters BGT features where property "eind_registratie" is None (null).

        Parameters:
            features (list): List of GeoJSON feature dicts.

        Returns:
            list: Filtered features with "eind_registratie" == None.
        '''
        return [
            feat for feat in features
            if feat.get("properties", {}).get("eind_registratie") is None
        ]


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
        url = f"{self.base_url_top10nl}/collections/{item_type}/items?bbox={self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]}&bbox-crs={self.crs}&crs={self.crs}&limit=1000&f=json"

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

    def get_bgt_features(self, item_type):
        """
        Retrieve features from the BGT API for the specified item type within the bounding box.

        Parameters:
            item_type (str): The name of the BGT collection to query (e.g., "waterdeel_vlak", "wegdeel_vlak", "terrein_vlak").

        Returns:
            dict:   A GeoJSON-like dictionary with key "features" containing a list of feature dictionaries as returned by the API.
        """
        features = []
        url = f"{self.base_url_bgt}/collections/{item_type}/items?bbox={self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]}&bbox-crs={self.crs}&crs={self.crs}&limit=1000&f=json"

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
            url = next_link

        return {"features": features}

    def process_water_features_top(self):
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

    def process_water_features_bgt(self):
        '''
        Process water features from BGT. Keeps all original properties.
        '''
        waterdata_vlak = self.get_bgt_features("waterdeel")
        water_features = []
        filtered_features = self.filter_active_bgt_features(waterdata_vlak.get("features", []))
        for wat in filtered_features:
            geom = shape(wat['geometry'])
            props = wat.get("properties", {}).copy()
            if props.get("hoogteniveau") != -1 and isinstance(geom, (Polygon, MultiPolygon)):
                water_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })

        return water_features

    def process_overbrugging_bgt(self):
        "Function to process the BGT overbrugging features"
        overbrugging_features = []
        filtered_overbrugging = self.filter_active_bgt_features(self.get_bgt_features("overbruggingsdeel").get("features", []))

        for feature in filtered_overbrugging:
            geom = shape(feature['geometry'])
            props = feature.get("properties", {}).copy()
            if isinstance(geom, (Polygon, MultiPolygon)):
                props["landuse"] = 0
                overbrugging_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })
        return overbrugging_features

    def process_terrain_features_top(self):
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

    def process_terrain_features_bgt(self):
        '''
        Process terrain features from BGT and assign land use codes from the terrain mapping.
        Keeps full original properties and adds "landuse".
        '''
        terreindata1 = self.get_bgt_features("begroeidterreindeel")
        terreindata2 = self.get_bgt_features("onbegroeidterreindeel")
        scheidingdata = self.get_bgt_features("scheiding_vlak")
        oeverdata = self.get_bgt_features("ondersteunendwaterdeel")

        filtered_terreindata1 = self.filter_active_bgt_features(terreindata1.get("features", []))
        filtered_terreindata2 = self.filter_active_bgt_features(terreindata2.get("features", []))
        filtered_oeverdata = self.filter_active_bgt_features(oeverdata.get("features", []))
        filtered_scheidingdata = self.filter_active_bgt_features(scheidingdata.get("features", []))
        terrain_features = []

        terrain_mapping = self.landcover_mapping.get("terrain", {})

        # Mark source for mapping logic
        for feature in filtered_terreindata1:
            feature["_source"] = "begroeid"
        for feature in filtered_terreindata2:
            feature["_source"] = "onbegroeid"

        combined_features = filtered_terreindata1 + filtered_terreindata2

        for feature in combined_features:
            geom = shape(feature['geometry'])
            props = feature.get("properties", {}).copy()  # copy full properties
            source = feature.get("_source", "")
            fysiek = (props.get("fysiek_voorkomen") or "").lower()
            type_ = (props.get("type") or "").lower()

            if source == "begroeid":
                landuse = 6 if fysiek == "zand" else 5
            else:
                landuse = terrain_mapping.get(fysiek, None)
                if landuse is None:
                    landuse = terrain_mapping.get(type_)

            if landuse is not None and isinstance(geom, (Polygon, MultiPolygon)):
                props["landuse"] = landuse
                terrain_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })

        # ondersteunendwaterdeel → grass (landuse = 5)
        for feature in filtered_oeverdata:
            geom = shape(feature['geometry'])
            props = feature.get("properties", {}).copy()
            if isinstance(geom, (Polygon, MultiPolygon)):
                props["landuse"] = 5
                terrain_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })
        for feature in filtered_scheidingdata:
            geom = shape(feature['geometry'])
            props = feature.get("properties", {}).copy()
            if isinstance(geom, (Polygon, MultiPolygon)):
                props["landuse"] = 0
                terrain_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })

        return terrain_features

    def process_road_features_top(self):
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


    def process_road_features_bgt(self):
        '''
        Loads and classifies BGT road features with landuse.

        Returns:
            list: A list of GeoJSON-like feature dictionaries representing roads. Each feature contains "geometry" and "properties" with a "landuse" code.
        '''
        wegdata = self.get_bgt_features("wegdeel")
        o_wegdata = self.get_bgt_features("ondersteunendwegdeel")
        filtered_features = self.filter_active_bgt_features(wegdata.get("features", []) + o_wegdata.get("features", []))

        road_features = []
        road_mapping = self.landcover_mapping.get("road", {})

        for road in filtered_features:
            geom = shape(road['geometry'])
            props = road.get("properties", {}).copy()

            functie = props.get("functie", "").lower()
            hoogte = props.get("relatieve_hoogteligging", "")
            try:
                rel_height = int(hoogte) if hoogte is not None else 0
            except ValueError:
                rel_height = 0

            # remove underground public transport
            if functie in {"spoorbaan", "tram"} and rel_height < 0:
                continue

            props["rel_height"] = rel_height
            fysiek = props.get("fysiek_voorkomen", "").lower()
            landuse = road_mapping.get(fysiek)

            if landuse is not None and isinstance(geom, (Polygon, MultiPolygon)):
                props["landuse"] = landuse
                road_features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": props
                })
        return road_features

    def get_features(self):
        if self.use_bgt:
            self.terrains = self.process_terrain_features_bgt()
            self.roads = self.process_road_features_bgt()
            self.water = self.process_water_features_bgt()
            self.brug = self.process_overbrugging_bgt()
        else:
            self.terrains = self.process_terrain_features_top()
            self.roads = self.process_road_features_top()
            self.water = self.process_water_features_top()

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

    def convert_to_raster_top(self):
        """
        Rasterize the TOP10NL terrain, road, water, and building features onto the DTM-sized grid. Applies land cover codes to a numpy
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

    def convert_to_raster_bgt(self):
        """
        Rasterize terrain, road, water, building, and overbrugging features from BGT onto the DTM sized grid.

        Returns:
            np.ndarray: 2D array with land cover codes assigned per grid cell.
        """
        array = self.dtm_dataset.read(1)
        transform = self.dtm_dataset.transform
        self.transform = transform

        array.fill(self.nodata_fill)

        # Water
        water_geometries = [shape(wat['geometry']) for wat in self.water]
        if water_geometries:
            water_mask = geometry_mask(water_geometries, transform=transform, invert=False, out_shape=array.shape)
            self.water_mask = water_mask
            array = np.where(water_mask, array, 7)

        # Terrain first
        for ter in self.terrains:
            geom = shape(ter['geometry'])
            landuse = ter['properties'].get('landuse', None)
            if landuse is not None:
                landuse_mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                array = np.where(landuse_mask, array, landuse)

        max_brug_height = 0
        if self.brug:
            # Optional: estimate effective level of overbrugging (if you use relative heights there)
            max_brug_height = max(
                (f['properties'].get("rel_height", 0) for f in self.brug), default=0
            )

        if self.roads:
            # Sort roads by rel_height descending
            self.roads.sort(key=lambda r: r["properties"].get("rel_height", 0), reverse=True)

            # Split roads based on overbrugging height
            high_roads = [r for r in self.roads if r["properties"].get("rel_height", 0) > max_brug_height]
            low_roads = [r for r in self.roads if r["properties"].get("rel_height", 0) <= max_brug_height]

            if self.roads_on_top:
                # Raster low roads first
                for road in low_roads:
                    geom = shape(road['geometry'])
                    landuse_road = road['properties'].get('landuse', None)
                    if landuse_road is not None:
                        mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                        array = np.where(mask, array, landuse_road)

                # Insert overbrugging in between
                if self.brug:
                    for feature in self.brug:
                        geom = shape(feature['geometry'])
                        landuse = feature['properties'].get('landuse', 0)
                        mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                        array = np.where(mask, array, landuse)

                # Then raster high roads
                for road in high_roads:
                    geom = shape(road['geometry'])
                    landuse_road = road['properties'].get('landuse', None)
                    if landuse_road is not None:
                        mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                        array = np.where(mask, array, landuse_road)

            else:
                if self.brug:
                    for feature in self.brug:
                        geom = shape(feature['geometry'])
                        landuse = feature['properties'].get('landuse', 0)
                        mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                        array = np.where(mask, array, landuse)

                for road in self.roads:
                    geom = shape(road['geometry'])
                    landuse_road = road['properties'].get('landuse', None)
                    if landuse_road is not None:
                        mask = geometry_mask([geom], transform=transform, invert=False, out_shape=array.shape)
                        array = np.where(mask, array, landuse_road)

        # Buildings
        building_geometries = [shape(building['geometry']) for building in self.buildings]
        if building_geometries:
            building_mask = geometry_mask(building_geometries, transform=transform, invert=False, out_shape=array.shape)
            self.building_mask = building_mask
            array = np.where(building_mask, array, 2)

        self.visualize_raster(array)
        return array

    def save_raster(self, name, change_nodata=True):
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

    def export_features_to_gpkg(self, features, output_path, layer_name="features"):
        '''
        Export a list of GeoJSON-like features to a GeoPackage.

        Parameters:
            features (list): List of features with "geometry" and "properties".
            output_path (str): File path to save the .gpkg.
            layer_name (str): Name of the layer inside the GeoPackage.
        '''
        records = []
        for feature in features:
            geom = shape(feature["geometry"])
            props = feature.get("properties", {})
            records.append({**props, "geometry": geom})

        gdf = gpd.GeoDataFrame(records, crs="EPSG:28992")
        gdf.to_file(output_path, layer=layer_name, driver="GPKG")

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

            # Update self.building_mask
            if hasattr(self, 'building_mask') and self.building_mask is not None:
                self.building_mask &= building_mask
            else:
                self.building_mask = building_mask

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

        if land_type == 7:
            if not hasattr(self, 'water_mask') or self.water_mask is None:
                self.water_mask = np.ones_like(self.array, dtype=bool)

            self.water_mask[to_update] = False

    def export_context(self, file_name, export_format="dxf"):
        """
        Export the current land cover and building context.dxf to a file in specified format.

        Parameters:
            file_name (str):                The output file path.
            export_format (str, optional):  Format of the export. Options are "json", "csv", or "dxf". Defaults to "dxf".

        Returns:
            None
        """

        landuse_color_map = {
            0: 8,  # grey    DXF color index 8 (dark grey)
            1: 7,  # black   DXF color index 7 (black/white depending on background)
            2: 1,  # red     DXF color index 1 (red)
            5: 3,  # green   DXF color index 3 (green)
            6: 33,  # brown   DXF color index 33 (brown)
            7: 5  # blue    DXF color index 5 (blue)
        }

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
                    "properties": {"parcel_id": building["parcel_id"], "landuse": 2}
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

            # Add water, roads, terrain and buildings as polylines
            def add_features_to_dxf(features, default_color, default_layer):
                for feature in features:
                    poly = shape(feature["geometry"])
                    if poly.geom_type != "Polygon":
                        continue

                    coords = list(poly.exterior.coords)

                    # Get landuse and determine color/layer
                    landuse = feature.get("properties", {}).get("landuse", None)
                    layer_name = f"{default_layer}_{landuse}" if landuse is not None else default_layer
                    color = landuse_color_map.get(landuse, default_color)

                    msp.add_lwpolyline(coords, close=True, dxfattribs={
                        "layer": layer_name,
                        "color": color
                    })

            add_features_to_dxf(transformed_buildings, 1, "building")
            add_features_to_dxf(transformed_water, 5, "water")
            add_features_to_dxf(transformed_roads, 0, "road")
            add_features_to_dxf(transformed_terrain, 3, "terrain")

            doc.saveas(file_name)
            print(f"Exported data to {file_name}")

        else:
            print("Unsupported export format. Use 'json', 'csv', or 'dxf'.")
