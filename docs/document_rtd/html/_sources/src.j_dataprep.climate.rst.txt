src.j\_dataprep.climate
===============================

This directory contains the four different pre-prepared meteorology files.

Daily mean and maximum air temperatures are calculated, and only days with a mean temperature above 15°C are considered. Based on these values, days are categorized into three temperature bands:

- 20–25°C (mild summer days)
- 25–30°C (warm summer days)
- >30°C (hot/tropical days)

For each category, an 'average day' is created by computing the mean values of relevant meteorological variables (air temperature, relative humidity, pressure, global, direct, and diffuse irradiance) across all qualifying days. These averaged conditions are assigned to 1 August 2023, a date representative of midsummer solar angles and daylight duration.

Additionally, an 'extreme day' dataset is created using the 97th percentile of all variables from the >30°C category. This scenario is assigned to 21 June 2023 (summer solstice), when solar elevation is at its peak and shading is minimal.

The four files are:

- ``avgday_2025plus_qgis.txt``
- ``avgday_2530plus_qgis.txt``
- ``avgday_30plus_qgis.txt``
- ``extday_30plus_qgis.txt``

The original ERA5 plugin from UMEP to automatically download any day is not included due to upstream issues
(https://github.com/UMEP-dev/UMEP/issues/685#issuecomment-2547039370). Users can download and prepare their own data from https://www.shinyweatherdata.com/, and convert this with the `METProcessor from UMEP <https://umep-docs.readthedocs.io/en/latest/pre-processor/Meteorological%20Data%20MetPreprocessor.html>`_.