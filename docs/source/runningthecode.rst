Running the code
================

A ``.py`` file can be added to the repository root, from which the desired functions can be imported from the ``src`` directory. However, this repository contains only the framework for a tool, not a complete tool itself. Therefore, no GUI or fully integrated program that combines all functions is provided.

Examples
----------

``main_jupyter.ipynb`` contains examples demonstrating all the functions in a logical, use-case-driven order.

Requirements
-----------

Conda Environment
^^^^^^^^^^^^^

An environment file is provided for setting up a Conda environment and can be found at ``environment.yml``. The code was developed on a Windows system using a mix of both pip and Conda. Windows-specific dependencies have been removed from the environment file. Conda was used mainly because it simplifies installing GDAL. However, some issues were encountered with the ``h5py`` package when installed via Conda; these are resolved by installing the pip version instead.

Rusterizer_3D
^^^^^^^^^^^^^

Additionally, it is required to install the following Python wheel via pip to access ``rusterizer_3d`` — a Rust-based program used to convert ``.OBJ`` files to layered arrays:

.. code-block:: none

   rusterizer_3d/rusterizer_3d-0.1.1-cp312-cp312-win_amd64.whl

This wheel was built for Windows. For other operating systems, please build your own version using the `Rusterizer_3d repository <https://github.com/jsscmnhn/rusterizer_3d/tree/main>`_.

Cuda
^^^^^^^^^^^^^

To use this code and the CuPy-accelerated functions, a CUDA-enabled GPU is required. Leveraging the GPU can significantly speed up computations; however, it also introduces a potential limitation: GPU memory may run out, which can cause SVF and SOLWEIG calculations to fail. If this issue occurs, consider dividing your study area into smaller tiles or using a coarser spatial resolution to reduce memory usage.

.OBJ input
------------

The class ``Building3d_input`` (found in ``src/j_dataprep/user_input.py``) allows you to insert a 3D ``.OBJ`` into the model. For this, a couple of rules must be followed:

- Meshes do not need to be closed, but ensure that no geometries intersect with each other.
- Make sure the mesh is fully triangulated (for example, use the ``TriangulateMesh`` command in Rhino).
- Use the ``.dxf`` export from the landcover context to correctly position your design. The bottom-left corner should be placed at the origin.
- Avoid vertical triangles positioned exactly over cell centers. (In the exported ``.dxf``, 1 unit corresponds to 1 grid cell length, so do not place geometry exactly at 0.5 units.)

You can decide how many void layers to include. The recommendation is to use only one, as it balances maximum effect with minimal computation time. This means only the first void layer from the ground up will be included in the simulation.
