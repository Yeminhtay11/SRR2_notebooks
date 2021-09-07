"""
Module for calculating a list of vegetation indices from a datacube containing bands without a user having to implement callback functions
"""

from openeo.rest.datacube import DataCube
from openeo.processes import ProcessBuilder, array_modify, power, sqrt, if_, multiply, divide, arccos, add, subtract
from shapely.geometry import Point
import numpy as np
import netCDF4 as nc
import glob
import seaborn as sns
from matplotlib.dates import DateFormatter
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import earthpy.plot as ep
import pandas as pd
import rasterio

WL_B04 = 0.6646
WL_B08 = 0.8328
WL_B11 = 1.610
one_over_pi = 1. / np.pi

# source: https://git.vito.be/projects/LCLU/repos/satio/browse/satio/rsindices.py
ndvi = lambda B04, B08: (B08 - B04) / (B08 + B04)
ndmi = lambda B08, B11: (B08 - B11) / (B08 + B11)
ndgi = lambda B03, B04: (B03 - B04) / (B03 + B04)


def anir(B04, B08, B11):
    a = sqrt(np.square(WL_B08 - WL_B04) + power(B08 - B04, 2))
    b = sqrt(np.square(WL_B11 - WL_B08) + power(B11 - B08, 2))
    c = sqrt(np.square(WL_B11 - WL_B04) + power(B11 - B04, 2))

    # calculate angle with NIR as reference (ANIR)
    site_length = (power(a, 2) + power(b, 2) - power(c, 2)) / (2 * a * b)
    site_length = if_(site_length.lt(-1), -1, site_length)
    site_length = if_(site_length.gt(1), 1, site_length)

    return multiply(one_over_pi, arccos(site_length))


ndre1 = lambda B05, B08: (B08 - B05) / (B08 + B05)
ndre2 = lambda B06, B08: (B08 - B06) / (B08 + B06)
ndre5 = lambda B05, B07: (B07 - B05) / (B07 + B05)

indices = {
    "NDVI": [ndvi, (0,1)],
    "NDMI": [ndmi, (-1,1)],
    "NDGI": [ndgi, (-1,1)],
    "ANIR": [anir, (0,1)],
    "NDRE1": [ndre1, (-1,1)],
    "NDRE2": [ndre2, (-1,1)],
    "NDRE5": [ndre5, (-1,1)]
}


def _callback(x: ProcessBuilder, index_list: list, datacube: DataCube, scaling_factor: int) -> ProcessBuilder:
    lenx = len(datacube.metadata._band_dimension.bands)
    tot = x
    for idx in index_list:
        if idx not in indices.keys(): raise NotImplementedError("Index " + idx + " has not been implemented.")
        band_indices = [datacube.metadata.get_band_index(band) for band in
                        indices[idx][0].__code__.co_varnames[:indices[idx][0].__code__.co_argcount]]
        lenx += 1
        if scaling_factor == None:
            tot = array_modify(data=tot, values=indices[idx][0](*[tot.array_element(i) for i in band_indices]), index=lenx)
        else:
            tot = array_modify(data=tot, values=lin_scale_range(indices[idx][0](*[tot.array_element(i) for i in band_indices]),*indices[idx][1],0,scaling_factor), index=lenx)
    return tot


def compute_indices(datacube: DataCube, index_list: list, scaling_factor: int = None) -> DataCube:
    """
    Computes a list of indices from a datacube

    param datacube: an instance of openeo.rest.DataCube
    param index_list: a list of indices. The following indices are currently implemented: NDVI, NDMI, NDGI, ANIR, NDRE1, NDRE2 and NDRE5
    return: the datacube with the indices attached as bands

    """
    return datacube.apply_dimension(dimension="bands",
                                    process=lambda x: _callback(x, index_list, datacube, scaling_factor)).rename_labels('bands',
                                                                                                        target=datacube.metadata.band_names + index_list)


def lin_scale_range(x,inputMin,inputMax,outputMin,outputMax):
    return add(multiply(divide(subtract(x,inputMin), subtract(inputMax, inputMin)), subtract(outputMax, outputMin)), outputMin)


def _random_point_in_shp(shp):
    within = False
    while not within:
        x = np.random.uniform(shp.bounds[0], shp.bounds[2])
        y = np.random.uniform(shp.bounds[1], shp.bounds[3])
        within = shp.contains(Point(x, y))
    return Point(x,y)

def point_sample_fields(crop_samples, nr_iterations):
    points = {"name":[], "geometry":[]}
    for name,crop_df in crop_samples.items():
        for num in range(nr_iterations):
            points["name"] += [name]*len(crop_df)
            points["geometry"] += np.asarray(crop_df['geometry'].apply(_random_point_in_shp)).tolist()

    gpd_points = gpd.GeoDataFrame(points, crs="EPSG:4326")
    gpd_points_utm = gpd_points.to_crs("EPSG:32631")
    points_per_type = {}
    for i in set(gpd_points_utm["name"]):
        crop = gpd_points_utm[gpd_points_utm["name"]==i].buffer(1).to_crs("EPSG:4326").to_json()
        points_per_type[i] = crop
    return points_per_type


def _prep_boxplot(year, bands):
    df = pd.DataFrame(columns=["Crop type","Date","Band","Iteration nr","Band value"])
    for file in glob.glob('.\\data\\300_*\\*.nc'):
        ds_orig = nc.Dataset(file)
        dt_rng = pd.date_range("01-01-"+str(year), "01-01-"+str(year+1),freq="MS")
        spl = file.split("\\")
        f_name = spl[-1].split(".")[0]
        crop_type = spl[-2].split("_")[-1]
        for band in bands:
            try:
                ds = ds_orig[band][:]
            except:
                print("File "+file+" is corrupt. Please remove it from your folder.")
            vals = None
            if ds.shape[1:3] == (1,2):
                vals = np.mean(ds,axis=2).flatten().tolist()
            elif ds.shape[1:3] == (2,1):
                vals = np.mean(ds,axis=1).flatten().tolist()
            elif ds.shape[1:3] == (1,1):
                vals = ds.flatten().tolist()
            elif ds.shape[1:3] == (2,2):
                vals = np.mean(np.mean(ds,axis=1),axis=1).tolist()
            else:
                print(file)
            df = df.append(pd.DataFrame({
                                "Crop type": crop_type,
                                "Date": dt_rng,
                                "Band": band,
                                "Iteration nr": [f_name]*13, 
                                "Band value": vals
            }), ignore_index=True)
    return df

def create_boxplots(year=2019):
    bands = ["B08", "B11", "NDVI", "ratio"]
    df = _prep_boxplot(year, bands)
    x_dates = df["Date"].dt.strftime("%m-%d-%y").unique()

    for crop in set(df["Crop type"]):
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2,2,figsize=(18,18))
        fig.suptitle(crop,y=0.91)
        axes = [ax1, ax2, ax3, ax4]
        df_m = df[df["Crop type"]==crop]

        for i in range(4):
            df_m_n = df_m[df_m["Band"]==bands[i]]
            sns.boxplot(ax=axes[i],data=df_m_n, x="Date",y="Band value")
            axes[i].set_xticklabels(labels=x_dates, rotation=45, ha='right')
            axes[i].title.set_text(str(bands[i])+" per month")
        #     axes[i].set_ylim(0,1)
        fig.show()

comb = {
    0: "none",
    1: "corn",
   2: "barley",
    3: "corn barley",
   4: "sugarbeet",
    5: "sugarbeet corn",
    6: "sugarbeet barley",
    7: "sugarbeet barley corn",
   8: "potato",
    9: "potato corn",
    10: "potato barley",
    11: "potato barley corn",
    12: "potato sugarbeet",
    13: "potato sugarbeet corn",
    14: "potato sugarbeet barley",
    15: "potato sugarbeet barley corn",
   16: "soy",
    17: "soy corn",
    18: "soy barley",
    19: "soy barley corn",
    20: "soy sugarbeet",
    21: "soy sugarbeet corn",
    22: "soy sugarbeet barley",
    23: "soy sugarbeet barley corn",
    24: "soy potato",
    25: "soy potato corn",
    26: "soy potato barley",
    27: "soy potato barley corn",
    28: "soy potato sugarbeet",
    29: "soy potato sugarbeet corn",
    30: "soy potato sugarbeet barley",
    31: "soy potato sugarbeet barley corn"
}

col_palette = ['linen',
    'chartreuse',
    'tomato',
    'olivedrab',
    'maroon',
    'whitesmoke',
    'wheat',
    'palevioletred',
    'darkturquoise',
    'tomato',
    'thistle',
    'teal',
    'darkgoldenrod',
    'darkmagenta',
    'darkorange',
    'sienna',
    'black',
    'silver',
    'tan',
    'seagreen',
    'mediumspringgreen',
    'lightseagreen',
    'royalblue',
    'mediumpurple',
    'plum',
    'darkcyan',
    'moccasin',
    'rosybrown',
    'gray',
    'sandybrown',
    'm',
    'navy']

def plot_croptypes(only_unique_classes=True):
    with rasterio.open('./data/total.tif',mode="r+",crs=rasterio.crs.CRS({"init": "epsg:4326"})) as dataset:
        ds = dataset.read(1)
        if only_unique_classes:
            ds = np.where(np.isin(ds, [1,2,4,8,16]), ds, 0)
        keys = np.unique(ds).astype(int)
        height_class_labels = [comb[key] for key in comb.keys() if key in keys]

        colors = col_palette[0:len(height_class_labels)]

        cmap = ListedColormap(colors)

        class_bins = [-0.5]+[i+0.5 for i in keys]
        norm = BoundaryNorm(class_bins, len(colors))

        f, ax = plt.subplots(figsize=(10, 8))  
        im = ax.imshow(ds, cmap=cmap, norm=norm)

        ep.draw_legend(im, titles=height_class_labels)
        ax.set(title="Rule-based crop classification")
        ax.set_axis_off()
        plt.show()
