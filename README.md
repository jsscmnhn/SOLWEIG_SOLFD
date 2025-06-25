<p align="center">
  <img src="figs/cover.jpg" alt="cover" width="50%"/>
</p>

# SOLFD
**SOLFD** (SOLweig For Design) is a repository for modelling Mean Radiant Temperature (Tmrt) and the physiological equivalent temperature (PET) in urban settings. SOLFD builds upon [SOLWEIG](https://github.com/UMEP-dev/UMEP-processing) from [UMEP](https://umep-docs.readthedocs.io/en/latest/Introduction.html).

SOLFD is meant to enhance the original SOLWEIG functionalities to better support urban designers in the Netherlands. It introduces an automated data pipeline, allows users to insert their own designs and modify the surrounding context, and significantly speeds up calculations by leveraging GPU acceleration. Additionally, the output is post-processed for improved interpretability, and the model now supports full 3D simulations, including buildings with balconies, underpasses, and overhangs, enabling more accurate assessment of their cooling effects.

> **SOLFD allows you to:**
>
> 1. Download and create DTMs, DSMs, CHMs, and land cover .TIFFs for any location in the Netherlands using just a bbox.  
> 2. Export the land cover context to .DXF to open in any CAD program.
> 3. Insert trees, land cover classes and 3d buildings into the .TIFFS.
> 4. Create SVFs and run SOLWEIG on the GPU, and run with 3D datasets.
> 5. Check the Tmrt and simplified PET for the designs/locations and get the heat stess indication.

# Documentation
https://solweig-solfd.readthedocs.io

More details about the methodology and code can be found in [my thesis](https://resolver.tudelft.nl/uuid:02dbba26-0535-4c05-a39b-e5b09c3d0712).

# Running the code
## Code
A `.py`  file can be added to the repository root, from which the desired functions can be imported from the [`src`](src) directory. However, this repository contains only the framework for a tool, not the complete tool itself. Therefore, no GUI or fully integrated program that combines all functions is provided.

## Examples  
[`main_jupyter.ipynb`](main_jupyter.ipynb) contains examples demonstrating all the functions in a logical, use-case-driven order.

## Requirements  
#### Conda Environment
An environment file is provided for setting up a Conda environment and can be found at [`environment.yml`](environment.yml). The code was developed on a Windows system using a mix of both pip and Conda. Windows-specific dependencies have been removed from the environment file. Conda was used mainly because it simplifies installing GDAL. However, some issues were encountered with the `h5py` package when installed via Conda; these are resolved by installing the pip version instead.

#### Rusterizer_3D
Additionally, it is required to install the following Python wheel via pip to access `rusterizer_3d` — a Rust-based program used to convert `.OBJ` files to layered arrays:  
[`rusterizer_3d-0.1.1-cp312-cp312-win_amd64.whl`](rusterizer_3d/rusterizer_3d-0.1.1-cp312-cp312-win_amd64.whl)  

This wheel was built for Windows. For other operating systems, please build your own version using the [Rusterizer_3d repository](https://github.com/jsscmnhn/rusterizer_3d/tree/main).

#### Cuda
To use this code and the CuPy-accelerated functions, a [Cuda](https://developer.nvidia.com/cuda-downloads)-enabled GPU is required. Leveraging the GPU can significantly speed up computations; however, it also introduces a potential limitation: GPU memory may run out, which can cause SVF and SOLWEIG calculations to fail.
If this issue occurs, consider dividing your study area into smaller tiles or using a coarser spatial resolution to reduce memory usage.

# .OBJ input
The Class [`Building3d_input`](src/j_dataprep/user_input.py) allows you to insert a 3D `.OBJ` into the model. For this, a couple of rules must be followed:

> - Meshes do not need to be closed, but ensure that no geometries intersect with each other.  
> - Make sure the mesh is fully triangulated (for example, use the `TriangulateMesh` command in Rhino).  
> - Use the `.dxf` export from the landcover context to correctly position your design. The bottom-left corner should be placed at the origin.  
> - Avoid vertical triangles positioned exactly over cell centers. (In the exported `.dxf`, 1 unit corresponds to 1 grid cell length, so do not place geometry exactly at 0.5 units.)

You can decide how many void layers to include. The recommendation is to use only one, as it balances maximum effect with minimal computation time. This means only the first void layer from the ground up will be included in the simulation.


# Datasets used
The following datasets were used:
| Dataset                     | Source                                                    | Description                                                                                                                                                                                                                                    | Usage                                          |
|-----------------------------|-----------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------|
| AHN DTM                     | https://service.pdok.nl/rws/ahn/wcs/v1_0                  | Digital Height map of the whole of Netherlands, containing only ground   heights                                                                                                                                                               | Creation of DTM, DSM and CHM                   |
| AHN DSM                     | https://service.pdok.nl/rws/ahn/wcs/v1_0                  | Digital Height map of the whole of Netherlands, containing all measured   heights                                                                                                                                                              | Creation of DSM                                |
| GeoTiles                    | https://geotiles.citg.tudelft.nl/                         | Tiled .LAZ pointclouds of the whole of Netherlands, overlayed with aerial   photographs for RGB                                                                                                                                                | Creation of CHM                                |
| 3DBAG                       | https://data.3dbag.nl/api/BAG3D/wfs                       | Dataset of 3d buildings in the Netherlands. The 2D LoD 1.3 is used                                                                                                                                                                             | Creation of DSM, Land cover                    |
| BGT                         | https://api.pdok.nl/lv/bgt/ogc/v1                         | Basisregistratie   Grootschalige Topografie (BGT), the detailed large-scale digital map of the   whole of the Netherlands                                                                                                                      | Creation of Land cover                         |
| TOP10NL                     | https://api.pdok.nl/brt/top10nl/ogc/v1                    | Object-oriented topographical dataset of the Netherlands, ranging from   1:5,000 to 1:25,000 scale                                                                                                                                             | Creation of Land cover                         |
| Urban   Tree Database       | https://www.fs.usda.gov/rds/archive/Catalog/RDS-2016-0005 | Urban tree   growth data collected over a period of 14 years (1998-2012) in 17 cities from   13 states across the United States                                                                                                                | Creation of Tree database                      |
| Shiny Weather Data          | https://www.shinyweatherdata.com/                         | ERA5 meteorological data processed by shinyweather data  from 2000 to 2024 for the location at   52.25°N latitude and 5.5°E longitude.                                                                                                         | Creation of Meteorology datasets               |
| Amsterdam Climate Bike      | Private correspondence with G.J. Steeneveld, WUR          | Radiation measurements (longwave and shortwave) and their locations   collected with the Climate Bike in Amsterdam on August 23 and September 12,   2023. Air temperature and Relative humidity measurements from this dataset   are also used | SOLFD accuracy evaluation                      |
| MAQ Observations            | https://maq-observations.nl/data-downloads/               | AAMS meteorological dataset with minute-resolution air temperature and   global shortwave radiation measurements                                                                                                                               | SOLFD accuracy evaluation: meteorology dataset |
| Schiphol - uurgegevens weer | https://www.knmi.nl/nederland-nu/klimatologie/uurgegevens | Hourly meteorological data from KNMI the Schiphol Climate Station,   including air pressure and relative humidity                                                                                                                              | SOLFD accuracy evaluation: meteorology dataset |

In UMEP, it is possible to run simulations for any day using the ERA5 plug-in. However, this option is not used in this code because it requires additional setup steps, including the creation of an ERA5 account. Moreover, it could not be made to work due to upstream issues (see: https://github.com/UMEP-dev/UMEP/issues/685#issuecomment-2547039370).
