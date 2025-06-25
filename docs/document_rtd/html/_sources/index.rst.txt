.. SOLFD documentation master file, created by
   sphinx-quickstart on Fri Jun  6 17:15:06 2025.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

SOLFD documentation
===================
.. image:: figs/cover.jpg
   :alt: cover
   :width: 50%
   :align: center

SOLFD
=====

**SOLFD** (SOLweig For Design) is a repository for modelling Mean Radiant Temperature (Tmrt) and the physiological equivalent temperature (PET) in urban settings. SOLFD builds upon `SOLWEIG <https://github.com/UMEP-dev/UMEP-processing>`_ from `UMEP <https://umep-docs.readthedocs.io/en/latest/Introduction.html>`_.

SOLFD is meant to enhance the original SOLWEIG functionalities to better support urban designers in the Netherlands. It introduces an automated data pipeline, allows users to insert their own designs and modify the surrounding context, and significantly speeds up calculations by leveraging GPU acceleration. Additionally, the output is post-processed for improved interpretability, and the model now supports full 3D simulations, including buildings with balconies, underpasses, and overhangs, enabling more accurate assessment of their cooling effects.

.. admonition:: SOLFD allows you to:

   1. Download and create DTMs, DSMs, CHMs, and land cover TIFF files for any location in the Netherlands using just a bounding box.
   2. Export the land cover context to DXF to open in any CAD program.
   3. Insert trees, land cover classes, and 3D buildings into the TIFF files.
   4. Create SVFs and run SOLWEIG on the GPU, and run with 3D datasets.
   5. Check the Tmrt and simplified PET for the designs/locations and get the heat stress indication.

Table of Content
=================

.. toctree::
   :maxdepth: 2

   runningthecode
   howworks
   datasets

.. toctree::
   :maxdepth: 2
   :caption: API

   src

