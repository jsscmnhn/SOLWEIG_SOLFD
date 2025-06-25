src.j\_analysis
=======================

This directory contains the code for the creation of the PET database. This only needs to be used if the user want to include more body types to the PET lookup file.


src.j\_analysis.PET\_database
------------------------------------

.. automodule:: src.j_analysis.PET_database
   :members:
   :show-inheritance:
   :undoc-members:

Function to create the PET database as an h5py file. Included standard body types for this repository are:

- **standard_man**: Pet(mbody=70, age=35, height=175, activity=80.0, sex=1, clo=0.9)
- **standard_woman**: Pet(mbody=60, age=35, height=165, activity=80.0, sex=0, clo=0.9)
- **elderly_woman**: Pet(mbody=55, age=75, height=160, activity=60.0, sex=0, clo=1.0)
- **young_child**: Pet(mbody=20, age=5, height=110, activity=90.0, sex=0, clo=0.7)

src.j\_analysis.pet\_calc
--------------------------------

.. automodule:: src.j_analysis.pet_calc
   :members:
   :show-inheritance:
   :undoc-members:


PET functions migrated to Cython . They are based upon the original `PET functions <https://github.com/UMEP-dev/UMEP-processing/blob/main/functions/SOLWEIGpython/PET_calculations.py>`_ from UMEP, adapted to be used for the PET database creation.

src.j\_analysis.setup
----------------------------

.. automodule:: src.j_analysis.setup
   :members:
   :show-inheritance:
   :undoc-members:

setup file for creating pet_calc.pyx


