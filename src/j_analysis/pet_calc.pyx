# cython: boundscheck=False, wraparound=False
import numpy as np
cimport numpy as np
# from cython.parallel import parallel, prange


cdef extern from "numpy/arrayobject.h":
    void import_array()

# Call it once at module initialization
import_array()

def calculate_PET_grid(np.ndarray[np.float64_t, ndim=2] Ta, np.ndarray[np.float64_t, ndim=2] RH,
                       np.ndarray[np.float64_t, ndim=2] Tmrt, np.ndarray[np.float64_t, ndim=2] va, pet):
    '''
    Cython enabled code to calculate the Physiological Equivalent Temperature (PET) for a 2D grid.

    Parameters:
        Ta      (np.ndarray):	Air temperature in degrees Celsius.
        RH      (np.ndarray):	Relative humidity in percent.
        Tmrt    (np.ndarray):	Mean radiant temperature in degrees Celsius.
        va      (np.ndarray):	Wind speed at 1.1 m in m/s.
        pet     (PETParams):	Object containing physiological parameters like body mass, age, height, activity, clothing, and sex.

    Returns:
        (np.ndarray):       	PET values for each grid cell. Grid cells with invalid input are set to -9999.
    '''
    cdef np.ndarray[np.float64_t, ndim=2] pet_index = np.zeros_like(Tmrt)
    cdef double mbody = pet.mbody
    cdef double age = pet.age
    cdef double height = pet.height
    cdef double activity = pet.activity
    cdef int sex = pet.sex
    cdef double clo = pet.clo

    cdef int index = 0
    cdef double total = 100.0 / (pet_index.shape[0] * pet_index.shape[1])

    for y in range(pet_index.shape[0]):
        for x in range(pet_index.shape[1]):
            if Tmrt[y, x] >= - 10:
                index += 1
                pet_index[y, x] = _PET(
                    Ta[y, x], RH[y, x], Tmrt[y, x], va[y, x],
                    mbody, age, height, activity, clo, sex
                )
            else:
                pet_index[y, x] = -9999

    return pet_index

def calculate_PET_index_vec(double Ta, double RH, double Tmrt, double va,pet):
    '''
    Calculate the Physiological Equivalent Temperature (PET) index using vectorized-compatible inputs.

    Parameters:
    	Ta      (float):	Air temperature in degrees Celsius.
    	RH      (float):	Relative humidity in percent.
    	Tmrt    (float):	Mean radiant temperature in degrees Celsius.
    	va      (float):    Wind speed at 1.1 m in m/s.
    	pet     (PETParams):	Object containing physiological parameters:
    	                            - mbody (float):    body mass in kg
                                    - age (float):      age in years
                                    - height (float):   height in meters
                                    - activity (float): activity level in W/m²
                                    - sex (int):    1 for male, 2 for female
                                    - clo (float): clothing insulation in clo units
    Returns:
    	(float)	PET index for the specified conditions.
    '''
    mbody=pet.mbody
    age=pet.age
    height=pet.height
    activity=pet.activity
    sex=pet.sex
    clo=pet.clo

    pet_index=_PET(Ta, RH,Tmrt,va,mbody,age,height,activity,clo,sex)
    return pet_index

def _PET(double ta, double RH, double tmrt, double v, double mbody, double age, double ht, double work, double icl, int sex):
    '''
   Cython enabled function to compute the Physiological Equivalent Temperature (PET) at a single point.

    Parameters:
    	Ta      (float):		Air temperature in degrees Celsius.
    	RH      (float):	    Relative humidity in percent.
    	Tmrt    (float):	    Mean radiant temperature in degrees Celsius.
    	va      (float):	    Wind speed at 1.1 m in m/s.
    	pet     (PETParams):	Object containing physiological parameters like body mass, age, height, activity, clothing, and sex.


    Returns:
    	(float):                PET value at the specified point.
    '''
    cdef double vps, vpa, po, p, rob, cb, food, emsk, emcl, evap, sigma, cair
    cdef double eta, c_1, c_2, c_3, c_4, c_5, c_6, c_7, c_8, c_9, c_10, c_11
    cdef double metbf, metbm, met, h, rtv, tex, eres, vpex, erel, ere, feff, adu, facl, rcl, y
    cdef double fcl, r2, r1, di, acl, tcore[8], wetsk, hc, he, fec, htcl, aeff
    cdef double rdsk, rdcl, sw, eswphy, eswpot, eswdif, esw, ed, vb1, vb2, vb, enbal, xx, tx
    cdef int count1, count2, count3, j
    # humidity conversion
    vps = 6.107 * (10. ** (7.5 * ta / (238. + ta)))
    vpa = RH * vps / 100  # water vapour presure, kPa

    po = 1013.25  # Pressure
    p = 1013.25  # Pressure
    rob = 1.06
    cb = 3.64 * 1000
    food = 0
    emsk = 0.99
    emcl = 0.95
    evap = 2.42e6
    sigma = 5.67e-8
    cair = 1.01 * 1000

    eta = 0  # No idea what eta is

    c_1 = 0.
    c_2 = 0.
    c_3 = 0.
    c_4 = 0.
    c_5 = 0.
    c_6 = 0.
    c_7 = 0.
    c_8 = 0.
    c_9 = 0.
    c_10 = 0.
    c_11 = 0.

    # INBODY
    metbf = 3.19 * mbody ** (3 / 4) * (1 + 0.004 * (30 - age) + 0.018 * ((ht * 100 / (mbody ** (1 / 3))) - 42.1))
    metbm = 3.45 * mbody ** (3 / 4) * (1 + 0.004 * (30 - age) + 0.010 * ((ht * 100 / (mbody ** (1 / 3))) - 43.4))
    if sex == 1:
        met = metbm + work
    else:
        met = metbf + work

    h = met * (1 - eta)
    rtv = 1.44e-6 * met

    # sensible respiration energy
    tex = 0.47 * ta + 21.0
    eres = cair * (ta - tex) * rtv

    # latent respiration energy
    vpex = 6.11 * 10 ** (7.45 * tex / (235 + tex))
    erel = 0.623 * evap / p * (vpa - vpex) * rtv
    # sum of the results
    ere = eres + erel

    # calcul constants
    feff = 0.725
    adu = 0.203 * mbody ** 0.425 * ht ** 0.725
    facl = (-2.36 + 173.51 * icl - 100.76 * icl * icl + 19.28 * (icl ** 3)) / 100
    if facl > 1:
        facl = 1
    rcl = (icl / 6.45) / facl
    y = 1

    # should these be else if statements?
    if icl < 2:
        y = (ht - 0.2) / ht
    if icl <= 0.6:
        y = 0.5
    if icl <= 0.3:
        y = 0.1

    fcl = 1 + 0.15 * icl
    r2 = adu * (fcl - 1. + facl) / (2 * 3.14 * ht * y)
    r1 = facl * adu / (2 * 3.14 * ht * y)
    di = r2 - r1
    acl = adu * facl + adu * (fcl - 1)

    tcore = [0] * 8

    wetsk = 0
    hc = 2.67 + 6.5 * v ** 0.67
    hc = hc * (p / po) ** 0.55
    c_1 = h + ere
    he = 0.633 * hc / (p * cair)
    fec = 1 / (1 + 0.92 * hc * rcl)
    htcl = 6.28 * ht * y * di / (rcl * np.log(r2 / r1) * acl)
    aeff = adu * feff
    c_2 = adu * rob * cb
    c_5 = 0.0208 * c_2
    c_6 = 0.76075 * c_2
    rdsk = 0.79 * 10 ** 7
    rdcl = 0

    count2 = 0
    j = 1

    while count2 == 0 and j < 7:
        tsk = 34
        count1 = 0
        tcl = (ta + tmrt + tsk) / 3
        count3 = 1
        enbal2 = 0

        while count1 <= 3:
            enbal = 0
            while (enbal * enbal2) >= 0 and count3 < 200:
                enbal2 = enbal
                # 20
                rclo2 = emcl * sigma * ((tcl + 273.2) ** 4 - (tmrt + 273.2) ** 4) * feff
                tsk = 1 / htcl * (hc * (tcl - ta) + rclo2) + tcl

                # radiation balance
                rbare = aeff * (1 - facl) * emsk * sigma * ((tmrt + 273.2) ** 4 - (tsk + 273.2) ** 4)
                rclo = feff * acl * emcl * sigma * ((tmrt + 273.2) ** 4 - (tcl + 273.2) ** 4)
                rsum = rbare + rclo

                # convection
                cbare = hc * (ta - tsk) * adu * (1 - facl)
                cclo = hc * (ta - tcl) * acl
                csum = cbare + cclo

                # core temperature
                c_3 = 18 - 0.5 * tsk
                c_4 = 5.28 * adu * c_3
                c_7 = c_4 - c_6 - tsk * c_5
                c_8 = -c_1 * c_3 - tsk * c_4 + tsk * c_6
                c_9 = c_7 * c_7 - 4. * c_5 * c_8
                c_10 = 5.28 * adu - c_6 - c_5 * tsk
                c_11 = c_10 * c_10 - 4 * c_5 * (c_6 * tsk - c_1 - 5.28 * adu * tsk)
                # tsk[tsk==36]=36.01
                #print(tsk.shape)
                if tsk == 36:
                    tsk = 36.01

                tcore[7] = c_1 / (5.28 * adu + c_2 * 6.3 / 3600) + tsk
                tcore[3] = c_1 / (5.28 * adu + (c_2 * 6.3 / 3600) / (1 + 0.5 * (34 - tsk))) + tsk
                if c_11 >= 0:
                    tcore[6] = (-c_10 - c_11 ** 0.5) / (2 * c_5)
                if c_11 >= 0:
                    tcore[1] = (-c_10 + c_11 ** 0.5) / (2 * c_5)
                if c_9 >= 0:
                    tcore[2] = (-c_7 + abs(c_9) ** 0.5) / (2 * c_5)
                if c_9 >= 0:
                    tcore[5] = (-c_7 - abs(c_9) ** 0.5) / (2 * c_5)
                tcore[4] = c_1 / (5.28 * adu + c_2 * 1 / 40) + tsk

                # transpiration
                tbody = 0.1 * tsk + 0.9 * tcore[j]
                sw = 304.94 * (tbody - 36.6) * adu / 3600000
                vpts = 6.11 * 10 ** (7.45 * tsk / (235. + tsk))
                if tbody <= 36.6:
                    sw = 0
                if sex == 2:
                    sw = 0.7 * sw
                eswphy = -sw * evap

                eswpot = he * (vpa - vpts) * adu * evap * fec
                wetsk = eswphy / eswpot
                if wetsk > 1:
                    wetsk = 1
                eswdif = eswphy - eswpot
                if eswdif <= 0:
                    esw = eswpot
                else:
                    esw = eswphy
                if esw > 0:
                    esw = 0

                # diffusion
                ed = evap / (rdsk + rdcl) * adu * (1 - wetsk) * (vpa - vpts)

                # MAX VB
                vb1 = 34 - tsk
                vb2 = tcore[j] - 36.6
                if vb2 < 0:
                    vb2 = 0
                if vb1 < 0:
                    vb1 = 0
                vb = (6.3 + 75 * vb2) / (1 + 0.5 * vb1)

                # energy balance
                enbal = h + ed + ere + esw + csum + rsum + food

                # clothing's temperature
                if count1 == 0:
                    xx = 1
                if count1 == 1:
                    xx = 0.1
                if count1 == 2:
                    xx = 0.01
                if count1 == 3:
                    xx = 0.001
                if enbal > 0:
                    tcl = tcl + xx
                else:
                    tcl = tcl - xx

                count3 = count3 + 1
            count1 = count1 + 1
            enbal2 = 0

        if j == 2 or j == 5:
            if c_9 >= 0:
                if tcore[j] >= 36.6 and tsk <= 34.050:
                    if (j != 4 and vb >= 91) or (j == 4 and vb < 89):
                        pass
                    else:
                        if vb > 90:
                            vb = 90
                        count2 = 1

        if j == 6 or j == 1:
            if c_11 > 0:
                if tcore[j] >= 36.6 and tsk > 33.850:
                    if (j != 4 and vb >= 91) or (j == 4 and vb < 89):
                        pass
                    else:
                        if vb > 90:
                            vb = 90
                        count2 = 1

        if j == 3:
            if tcore[j] < 36.6 and tsk <= 34.000:
                if (j != 4 and vb >= 91) or (j == 4 and vb < 89):
                    pass
                else:
                    if vb > 90:
                        vb = 90
                    count2 = 1

        if j == 7:
            if tcore[j] < 36.6 and tsk > 34.000:
                if (j != 4 and vb >= 91) or (j == 4 and vb < 89):
                    pass
                else:
                    if vb > 90:
                        vb = 90
                    count2 = 1

        if j == 4:
            if (j != 4 and vb >= 91) or (j == 4 and vb < 89):
                pass
            else:
                if vb > 90:
                    vb = 90
                count2 = 1

        j = j + 1

    # PET_cal
    tx = ta
    enbal2 = 0
    count1 = 0
    enbal = 0

    hc = 2.67 + 6.5 * 0.1 ** 0.67
    hc = hc * (p / po) ** 0.55

    while count1 <= 3:
        while (enbal * enbal2) >= 0:
            enbal2 = enbal

            # radiation balance
            rbare = aeff * (1 - facl) * emsk * sigma * ((tx + 273.2) ** 4 - (tsk + 273.2) ** 4)
            rclo = feff * acl * emcl * sigma * ((tx + 273.2) ** 4 - (tcl + 273.2) ** 4)
            rsum = rbare + rclo

            # convection
            cbare = hc * (tx - tsk) * adu * (1 - facl)
            cclo = hc * (tx - tcl) * acl
            csum = cbare + cclo

            # diffusion
            ed = evap / (rdsk + rdcl) * adu * (1 - wetsk) * (12 - vpts)

            # respiration
            tex = 0.47 * tx + 21
            eres = cair * (tx - tex) * rtv
            vpex = 6.11 * 10 ** (7.45 * tex / (235 + tex))
            erel = 0.623 * evap / p * (12 - vpex) * rtv
            ere = eres + erel

            # energy balance
            enbal = h + ed + ere + esw + csum + rsum

            # iteration concerning Tx
            if count1 == 0:
                xx = 1
            if count1 == 1:
                xx = 0.1
            if count1 == 2:
                xx = 0.01
            if count1 == 3:
                xx = 0.001
            if enbal > 0:
                tx = tx - xx
            if enbal < 0:
                tx = tx + xx
        count1 = count1 + 1
        enbal2 = 0

    return tx