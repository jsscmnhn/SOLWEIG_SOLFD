import json
from rusterizer_3d import rasterize_from_python
import numpy as np
from osgeo import gdal
from src.util.misc import saveraster
from scipy.ndimage import label

class Surface_input:
    '''
    Class to create a new land cover input from user input for the Landcover class.

    Parameters:
          ncols (int):          Number of columns in the raster grid.
          nrows (int):          Number of rows in the raster grid.
          resolution (float):   Resolution of the raster grid cells.

    Attributes:
          dictionary (dict):    Loaded landcover category dictionary from 'landcover_bgt.json'.
          res (float):          Resolution of the raster grid cells.
          ncols (int):           Number of columns in the raster grid.
          nrows (int):           Number of rows in the raster grid.
    '''

    def __init__(self, ncols, nrows, resolution):
        self.dictionary = json.load(open("src/databases/landcover_bgt.json", "r", encoding="utf-8"))
        self.res = resolution
        self.ncols = ncols
        self.nrows = nrows

    def get_value(self, category, key) -> int | None:
        '''
        Retrieves the integer value for a given category and key from the landcover dictionary.

        Parameters:
              category (str):   The category name in the landcover dictionary.
              key (str):        The key within the category to look up.

        Returns:
              int or None:      The integer value corresponding to the category and key, or None if not found.
        '''
        return self.dictionary.get(category, {}).get(key, None)

    def user_input_array(self, obj_path, type):
        '''
        Rasterizes a given input object path and retrieves a type number based on provided type info.

        Parameters:
              obj_path (str):   Path to the input object to rasterize.
              type (list):      List containing two elements [category, key] to fetch a type number.

        Returns:
              tuple:           Tuple containing:
                - np.ndarray:   Rasterized array from the input object.
                - int or None:  Type number corresponding to the category and key.
        '''
        array = rasterize_from_python(obj_path, self.ncols, self.nrows, self.res, [0, 0], -9999)
        typeno = self.get_value(type[0], type[1])
        return array[0], typeno


class Building3d_input:
    '''
    Class to handle 3D building input rasterization and create DSM layers with gaps.

    Attributes:
       cols (int): Number of columns in the raster grid.
       rows (int): Number of rows in the raster grid.
       res (float): Spatial resolution of each cell in the raster grid.
    '''
    def __init__(self, ncols, nrows, resolution):
        '''
        Initialize the Building3d_input instance with grid dimensions and resolution.

        Parameters:
           ncols (int):             Number of columns in the raster grid.
           nrows (int):             Number of rows in the raster grid.
           resolution (float):      Spatial resolution of each cell in the grid.
        '''
        self.res = resolution
        self.cols = ncols
        self.rows = nrows

    @staticmethod
    def buildings_input(arrays, num_gaps):
        '''
        Generate 3D-layered DSM  representing building heights with gaps.

        Parameters:
           arrays (list of np.ndarray):     List of input 2D arrays representing building heights and gap layers, output from Rusterizer.
           num_gaps (int):                  Number of gaps to include in the DSM layering.

        Returns
        -------
        dsms (np.ndarray):
            3D array with shape (layers, rows, cols) of DSM layers with gaps.
        base_array (np.ndarray):
            The base building height array from input for reference.
        '''
        if num_gaps == 0:
            return arrays[0], arrays[0]

        layers = num_gaps * 2 + 1

        if layers > len(arrays) + 1:
            print("Amount of gaps is too high for the given input. ")
            return

        dsms = np.full((layers, arrays[0].shape[0], arrays[0].shape[1]), np.nan)
        arrays = [np.where(arr == -9999, np.nan, arr) for arr in arrays]

        while len(arrays) < 4:
            dummy = np.full_like(arrays[0], np.nan)
            arrays.append(dummy)

        grounded_mask = arrays[1] == 0
        direct_gaps = ~grounded_mask
        gaps = arrays[3] > 0
        both_masks = grounded_mask & gaps

        dsms[0][grounded_mask] = np.nanmin(np.stack([arrays[0][grounded_mask], arrays[2][grounded_mask]]), axis=0)

        if num_gaps == 1:
            dsms[1][direct_gaps] = arrays[1][direct_gaps]
            dsms[1][both_masks] = arrays[3][both_masks]

            dsms[2][direct_gaps] = arrays[0][direct_gaps]
            dsms[2][both_masks] = arrays[0][both_masks]

            return dsms, arrays[0]

        if num_gaps > 1:
            if 4 < len(arrays):
                # layer 1
                dsms[1][direct_gaps] = arrays[1][direct_gaps]
                dsms[1][both_masks] = arrays[3][both_masks]

                direct_gaps_valid = direct_gaps & ~np.isnan(arrays[2])
                direct_gaps_fallback = direct_gaps & np.isnan(arrays[2])

                both_masks_valid = both_masks & ~np.isnan(arrays[4])

                for i in range(1, num_gaps):
                    j = 2 * i
                    new_direct_valid = direct_gaps_valid & ~np.isnan(arrays[j])
                    new_direct_fallback = direct_gaps_valid & np.isnan(arrays[j])

                    dsms[j][new_direct_valid] = arrays[j][new_direct_valid]
                    dsms[j][new_direct_fallback] = arrays[0][new_direct_fallback]

                    if j + 1 < len(arrays):
                        dsms[j + 1][new_direct_valid] = arrays[j + 1][new_direct_valid]

                    # For both_masks
                    if j + 2 < len(arrays):
                        new_both_valid = both_masks_valid & ~np.isnan(arrays[j + 2])
                        new_both_fallback = both_masks_valid & np.isnan(arrays[j + 2])

                        dsms[j][new_both_valid] = arrays[j + 2][new_both_valid]
                        dsms[j][new_both_fallback] = arrays[0][new_both_fallback]

                        if j + 3 < len(arrays):
                            dsms[j + 1][new_both_valid] = arrays[j + 3][new_both_valid]

                    # Update masks for next iteration
                    direct_gaps_valid = new_direct_valid
                    both_masks_valid = new_both_valid if j + 2 < len(arrays) else both_masks_valid

                # final layer
                dsms[layers - 1][direct_gaps_valid] = arrays[0][direct_gaps_valid]
                dsms[layers - 1][both_masks_valid] = arrays[0][both_masks_valid]

                # Post-process to ensure no empty layers before filled ones (starting from DSM 2), this is done bc a mismatch can happen when the amount of grounded gap layers is lower than the amount of direct
                for i in range(3, dsms.shape[0]):
                    j = i
                    while j > 3:
                        empty_below = np.isnan(dsms[j - 1]) & ~np.isnan(dsms[j])
                        if not np.any(empty_below):
                            break
                        dsms[j - 1][empty_below] = dsms[j][empty_below]
                        dsms[j][empty_below] = np.nan
                        j -= 1

                return dsms, arrays[0]
            elif len(arrays) == 4:
                dsms[1][direct_gaps] = arrays[1][direct_gaps]
                dsms[1][both_masks] = arrays[3][both_masks]

                direct_gaps_valid = direct_gaps & ~np.isnan(arrays[2])
                direct_gaps_fallback = direct_gaps & np.isnan(arrays[2])

                dsms[2][direct_gaps_valid] = arrays[2][direct_gaps_valid]
                dsms[2][direct_gaps_fallback] = arrays[0][direct_gaps_fallback]

                dsms[2][both_masks] = arrays[0][both_masks]

                dsms[3][direct_gaps_valid] = arrays[3][direct_gaps_valid]
                dsms[4][direct_gaps_valid] = arrays[0][direct_gaps_valid]

                return dsms, arrays[0]

    def rasterize_3dbuilding(self, obj_path, num_gaps):
        '''
        Rasterize a 3D building OBJ file into DSM layers with gaps.

        Parameters:
           obj_path (str):  File path to the 3D building OBJ file.
           num_gaps (int):  Number of gaps to consider when layering DSMs.

        Returns
        -------
        dsms (np.ndarray):
            3D array of DSM layers with gaps.
        highest_array (np.ndarray):
            The highest building height layer from input.
        input_arrays (list of np.ndarray):
            Raw input rasterized arrays from the OBJ.
        '''
        input_arrays = rasterize_from_python(obj_path, self.cols, self.rows, self.res, [0, 0], -9999)

        dsms, highest_array = self.buildings_input(input_arrays, num_gaps)

        return dsms, highest_array, input_arrays
