import h5py
import numpy as np
from osgeo import gdal
import os
import re

class TmrtOutput:
    '''
    Handles processing of Tmrt (Mean Radiant Temperature) rasters and computes PET (Physiological Equivalent Temperature) using precomputed lookup tables.

    Attributes:
    	output_folder      (str)		    Path to the folder containing Tmrt raster files.
    	gdal_dataset       (gdal.Dataset)	Internal reference for GDAL dataset (if needed later).
    	valid_mask         (np.ndarray)	    Boolean mask marking valid (non-building, non-water) pixels.
    	tmrt_arrays_by_time (dict)		    Map of time string → Tmrt raster array.
    	time_groups        (dict)		    Tmrt arrays grouped by time of day ('morning', 'afternoon', 'evening').
    	averaged_tmrt      (dict)		    Mean Tmrt array for each time group.
    	pet_arrays_by_time (dict)		    PET arrays computed per Tmrt time.
    	classified_pet_by_time (dict)	    Classified PET categories per time step.
    	averaged_pet       (dict)		    Averaged PET arrays by time group.
    	averaged_class_pet (dict)		    Classified PET categories based on averaged PET arrays.
    	'''
    def __init__(self, output_folder, building_mask=None, water_mask=None):
        self.output_folder = output_folder
        self.gdal_dataset = None

        self.valid_mask = None
        self.init_mask(building_mask, water_mask)

        self.tmrt_arrays_by_time = self.calc_arrays(output_folder)
        self.time_groups = self.group_by_time_of_day()
        self.averaged_tmrt = self.average_time_groups()

        self.pet_arrays_by_time = {}
        self.classified_pet_by_time = {}
        self.averaged_pet = {}
        self.averaged_class_pet = {}

    def init_mask(self, building_mask, water_mask):
        '''
        Initializes the valid mask by excluding building and water areas.

        Parameters:
            building_mask (np.ndarray): Binary mask where non-zero indicates valid (non-building) pixels.
            water_mask    (np.ndarray): Binary mask where non-zero indicates valid (non-water) pixels.
        '''

        # Start with an all-True mask (valid everywhere)
        self.valid_mask = np.ones_like(building_mask, dtype=bool)

        # Apply the building mask (mark buildings as invalid)
        if building_mask is not None:
            self.valid_mask &= (building_mask != 0)

        # Apply the water mask (mark water areas as invalid)
        if water_mask is not None:
            self.valid_mask &= (water_mask != 0)


    def get_pet_raster_from_lookup(self, tmrt_raster, wind_speed, air_temp, rh, body_type, lookup_file="src/databases/pet_lookup.h5",
                               tmrt_min=0, tmrt_max=65, tmrt_step=0.5, wind_speeds=None, rhs=None, temps=None):
        '''
        Returns the PET raster for the given TMRT raster and atmospheric conditions using a lookup table.

        Parameters:
            tmrt_raster (np.ndarray): Input TMRT raster.
            wind_speed  (float)     : Wind speed in m/s.
            air_temp    (float)     : Air temperature in °C.
            rh          (float)     : Relative humidity in %.
            body_type   (str)       : Type of body for lookup ('standard_man', 'elderly_woman', 'standard_woman', 'young_child').
            lookup_file (str)       : Path to HDF5 lookup file.
            tmrt_min    (float)     : Minimum TMRT value in the lookup table.
            tmrt_max    (float)     : Maximum TMRT value in the lookup table.
            tmrt_step   (float)     : Step size for TMRT values in lookup.
            wind_speeds (list)      : Wind speed values in lookup (optional).
            rhs         (list)      : Relative humidity values in lookup (optional).
            temps       (list)      : Air temperature values in lookup (optional).

        Returns:
            np.ndarray: PET raster aligned with the input TMRT raster.
        '''
        if wind_speeds is None:
            wind_speeds = np.array([0.1, 2.0, 6.0])
        if rhs is None:
            rhs = np.arange(100, -1, -10)
        if temps is None:
            temps = np.arange(40.0, -0.1, -0.5)

        with h5py.File(lookup_file, "r") as f:
                pet_dataset = f[body_type]

                def find_nearest_index(array, value):
                    return np.abs(array - value).argmin()

                # Find the closest indices for the environmental conditions
                ws_idx = find_nearest_index(wind_speeds, wind_speed)
                rh_idx = find_nearest_index(rhs, rh)
                ta_idx = find_nearest_index(temps, air_temp)

                # Clip and map tmrt values to lookup indices based on configurable parameters
                tmrt_clipped = np.clip(tmrt_raster, tmrt_min, tmrt_max)

                tmrt_clipped_valid = tmrt_clipped[self.valid_mask]

                # Calculate the indices only for valid TMRT values
                tmrt_indices_valid = np.round((tmrt_clipped_valid - tmrt_min) / tmrt_step).astype(int)

                # Ensure that indices stay within bounds for valid TMRT values
                max_index = int((tmrt_max - tmrt_min) / tmrt_step)
                tmrt_indices_valid = np.clip(tmrt_indices_valid, 0, max_index)
                tmrt_indices_valid = max_index - tmrt_indices_valid

                # Create an array of the same shape as the original TMRT raster and fill it with -1
                tmrt_indices = np.full_like(tmrt_raster, -1, dtype=int)

                # Place the valid indices into the correct positions
                tmrt_indices[self.valid_mask] = tmrt_indices_valid

                # Fetch the PET raster based on these indices
                pet_raster = np.take(pet_dataset[ws_idx, rh_idx, :, ta_idx], tmrt_indices)

                # Handle NaN areas where tmrt was outside the valid range
                pet_raster[~self.valid_mask] = np.nan

                return pet_raster

    def calc_arrays(self, output_folder):
        '''
        Reads Tmrt raster files from the given folder and loads them into a dictionary keyed by time.

        Parameters:
            output_folder (str): Path to folder containing Tmrt_YYYY_DDD_HHMM.tif files.

        Returns:
            dict: Dictionary mapping time keys to Tmrt raster arrays.
        '''
        tmrt_arrays_by_time = {}

        pattern = re.compile(r'^Tmrt_\d{4}_\d{3}_(\d{4})D\.tif$')

        for filename in os.listdir(output_folder):
            match = pattern.match(filename)
            if match:
                time_key = match.group(1)
                file_path = os.path.join(output_folder, filename)

                # Open file using gdal
                dataset = gdal.Open(file_path)
                if dataset is None:
                    print(f"Could not open {file_path}")
                    continue

                band = dataset.GetRasterBand(1)
                array = band.ReadAsArray()

                masked_array = np.where(self.valid_mask == 0, np.nan, array)

                tmrt_arrays_by_time[time_key] = masked_array

        return tmrt_arrays_by_time

    def get_time_group(self, time_str):
        '''
        Assigns a time string to a part of the day: morning, afternoon, or evening.

        Parameters:
            time_str (str): Time string in HHMM format (e.g., "1300").

        Returns:
            str or None: Time group name or None if outside expected ranges.
        '''
        time_val = int(time_str)
        if 600 <= time_val < 1200:
            return 'morning'
        elif 1200 <= time_val < 1800:
            return 'afternoon'
        elif 1800 <= time_val <= 2100:
            return 'evening'
        return None

    def group_by_time_of_day(self):
        '''
        Groups Tmrt arrays by time of day into 'morning', 'afternoon', and 'evening'.

        Returns:
            dict: Dictionary mapping time groups to lists of Tmrt arrays.
        '''
        grouped = {'morning': [], 'afternoon': [], 'evening': []}

        for time_str, array in self.tmrt_arrays_by_time.items():
            group = self.get_time_group(time_str)
            if group:
                grouped[group].append(array)

        return grouped

    def average_time_groups(self):
        '''
        Computes the average Tmrt for each time of day group.

        Returns:
            dict: Dictionary mapping time groups to mean Tmrt raster arrays.
        '''
        avg_by_group = {}
        for group, arrays in self.time_groups.items():
            if arrays:
                stacked = np.stack(arrays)
                avg = np.mean(stacked, axis=0)
                avg_by_group[group] = avg
            else:
                avg_by_group[group] = None
        return avg_by_group

    def calculate_stats_and_bins(self, array, pixel_size=0.5, isTmrt=True):
        '''
        Computes statistics and area coverage by thermal stress bin.

        Parameters:
            array       (np.ndarray): Input Tmrt or PET raster array.
            pixel_size  (float)     : Pixel size in meters (default is 0.5).
            isTmrt      (bool)      : If True, use Tmrt bins; otherwise use PET bins.

        Returns:
            dict: Statistics including mean, median, min, max, and bin area/percentage breakdown.
        '''
        if isTmrt:
            bins = [-np.inf, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, np.inf]
        else:
            bins = [-np.inf, 4, 8, 13, 18, 23, 29, 35, 41, np.inf]

        stats = {
            'mean': np.nanmean(array),
            'median': np.nanmedian(array),
            'min': np.nanmin(array),
            'max': np.nanmax(array),
        }

        hist, bin_edges = np.histogram(array[self.valid_mask], bins=bins)
        pixel_area = pixel_size ** 2
        bin_areas = hist * pixel_area
        total_area = np.sum(bin_areas)
        bin_percentages = (bin_areas / total_area) * 100

        stats['bins'] = [
            {
                'range': (bin_edges[i], bin_edges[i + 1]),
                'area_m2': bin_areas[i],
                'percentage': bin_percentages[i]
            }
            for i in range(len(hist))
        ]

        return stats

    def classify_pet(self, pet_array):
        '''
        Classifies PET values into 9 thermal stress bins.

        Parameters:
            pet_array (np.ndarray): PET raster array.

        Returns:
            np.ndarray: Raster with integer bin class values (NaN where PET is invalid).
        '''
        bins = [-np.inf, 4, 8, 13, 18, 23, 29, 35, 41, np.inf]
        classified = np.digitize(pet_array, bins) - 1
        classified = classified.astype(float)
        classified[np.isnan(pet_array)] = np.nan

        return classified

    def calc_pet(self, Ta, RH, va, body_type="standard_man", lookup_file="src/databases/pet_lookup.h5"):
        '''
        Computes PET and classified PET for each timestep and averaged Tmrt using the lookup table.

        Parameters:
            Ta         (float): Air temperature in °C.
            RH         (float): Relative humidity in %.
            va         (float): Wind speed in m/s.
            body_type  (str)  : Body type ('standard_man', 'elderly_woman', 'standard_woman', 'young_child').
            lookup_file (str) : Path to PET HDF5 lookup file.
        '''
        for time_key, Tmrt in self.tmrt_arrays_by_time.items():
            if Tmrt is None:
                print(f"Skipping {time_key}: Tmrt data is None")
                continue

            try:
                pet_array = self.get_pet_raster_from_lookup(Tmrt, va, Ta, RH, body_type, lookup_file)
                self.pet_arrays_by_time[time_key] = pet_array
                self.classified_pet_by_time[time_key] = self.classify_pet(pet_array)
            except Exception as e:
                print(f"Failed to compute PET for {time_key}: {e}")

        for time_key, Tmrt in self.averaged_tmrt.items():
            if Tmrt is None:
                print(f"Skipping {time_key}: Tmrt data is None")
                continue

            try:
                pet_array = self.get_pet_raster_from_lookup(Tmrt, va, Ta, RH, body_type, lookup_file)
                self.averaged_pet[time_key] = pet_array
                self.averaged_class_pet[time_key] = self.classify_pet(pet_array)
            except Exception as e:
                print(f"Failed to compute PET for {time_key}: {e}")
