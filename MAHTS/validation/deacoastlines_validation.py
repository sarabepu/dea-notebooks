#!/usr/bin/env python
# coding: utf-8

import re
import os.path
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from pathlib import Path
from io import StringIO
from pyproj import Transformer
from sklearn.metrics import r2_score
from sklearn.metrics import mean_squared_error
from sklearn.metrics import mean_absolute_error
from shapely.geometry import box, Point, LineString


def dms2dd(s):
    # example: s = "0°51'56.29"
    degrees, minutes, seconds = re.split('[°\'"]+', s)
    if float(degrees) > 0:
        dd = float(degrees) + float(minutes) / 60 + float(seconds) / (60 * 60)
    else:
        dd = float(degrees) - float(minutes) / 60 - float(seconds) / (60 * 60);
    return dd


def dist_angle(lon, lat, dist, angle):
    lon_2 = lon + dist *  np.sin(angle * np.pi / 180)
    lat_2 = lat + dist *  np.cos(angle * np.pi / 180)
    return pd.Series({'y2': lat_2, 'x2': lon_2})


def interp_intercept(x, y1, y2, reverse=True):
    """Find the intercept of two curves, given by the same x data"""
    
    def intercept(point1, point2, point3, point4):
        """find the intersection between two lines
        the first line is defined by the line between point1 and point2
        the first line is defined by the line between point3 and point4
        each point is an (x,y) tuple.

        So, for example, you can find the intersection between
        intercept((0,0), (1,1), (0,1), (1,0)) = (0.5, 0.5)

        Returns: the intercept, in (x,y) format
        """    

        def line(p1, p2):
            A = (p1[1] - p2[1])
            B = (p2[0] - p1[0])
            C = (p1[0] * p2[1] - p2[0] * p1[1])
            return A, B, -C

        def intersection(L1, L2):
            D  = L1[0] * L2[1] - L1[1] * L2[0]
            Dx = L1[2] * L2[1] - L1[1] * L2[2]
            Dy = L1[0] * L2[2] - L1[2] * L2[0]

            x = Dx / D
            y = Dy / D
            return x,y

        L1 = line([point1[0],point1[1]], [point2[0],point2[1]])
        L2 = line([point3[0],point3[1]], [point4[0],point4[1]])

        R = intersection(L1, L2)

        return R

    try:
        
        if isinstance(y2, (int, float)):

            y2 = np.array([y2] * len(x))

        if reverse:

            x = x[::-1]
            y1 = y1[::-1]
            y2 = y2[::-1]

        idx = np.argwhere(np.diff(np.sign(y1 - y2)) != 0)
        xc, yc = intercept((x[idx], y1[idx]),((x[idx + 1], y1[idx + 1])), 
                           ((x[idx], y2[idx])), ((x[idx + 1], y2[idx + 1])))

        return xc[0][0]
    
    except: 
        
        return np.nan
    
    
def dist_along_transect(dist, start_x, start_y, end_x, end_y):    
 
    transect_line = LineString([(start_x, start_y), (end_x, end_y)])
    distance_coords = transect_line.interpolate(dist).coords.xy
    return [coord[0] for coord in distance_coords]


def waterline_intercept(x, 
                        dist_col='distance', 
                        x_col='x', 
                        y_col='y', 
                        z_col='elevation', 
                        z_val=0): 
    
    dist_int = interp_intercept(x[dist_col].values, x[z_col].values, 0)
    x_int = interp_intercept(x[x_col].values, x[z_col].values, 0)
    y_int = interp_intercept(x[y_col].values, x[z_col].values, 0)
    
    return pd.Series({f'{z_val}_dist': dist_int, 
                      f'{z_val}_x': x_int, 
                      f'{z_val}_y': y_int})
    

def preprocess_nswbpd(fname, overwrite=False):   
    
    # Get output filename
    name = Path(fname).stem.split('_')[-1].lower().replace(' ', '')
    fname_out = f'output_data/nswbpd_{name}.csv'
    
    # Test if file exists
    if not os.path.exists(fname_out) or overwrite:  
        
        # Read in data
        print(f'Processing {fname_out}             ', end='\r')            
        profiles_df = pd.read_csv(fname, skiprows=5)
        profiles_df['Year/Date'] = pd.to_datetime(profiles_df['Year/Date'],
                                                  dayfirst=True,
                                                  errors='coerce')

        # Restrict to post 1987
        profiles_df = profiles_df[profiles_df['Year/Date'] > '1987']

        # Convert columns to strings and add unique ID column
        profiles_df['Beach'] = profiles_df['Beach'].str.lower().str.replace(' ', '')
        profiles_df['Block'] = profiles_df['Block'].astype(str).str.lower()
        profiles_df['Profile'] = profiles_df['Profile'].astype(str).str.lower()
        profiles_df['site'] = profiles_df[['Beach', 'Block',
                                           'Profile']].apply('_'.join, 1)

        # Rename columns
        profiles_df.columns = ['beach', 'section', 'profile', 'date', 'chainage', 
                               'elevation', 'easting', 'northing', 'source', 'site']
        
        # Compute origin points for each profile
        start_xy = profiles_df.loc[profiles_df.groupby(['site']).chainage.idxmin(), 
                                    ['site', 'easting', 'northing']]
        start_xy = start_xy.rename({'easting': 'start_x', 
                                    'northing': 'start_y'}, axis=1)
        start_xy = start_xy.reset_index(drop=True)

        # Compute end points for each profile
        end_xy = profiles_df.loc[profiles_df.groupby(['site']).chainage.idxmax(), 
                                 ['site', 'easting', 'northing']]
        end_xy = end_xy.rename({'easting': 'end_x', 
                                'northing': 'end_y'}, axis=1)
        end_xy = end_xy.reset_index(drop=True)

        # Join origin and end points into dataframe
        profiles_df = pd.merge(left=profiles_df, right=start_xy)
        profiles_df = pd.merge(left=profiles_df, right=end_xy)

        # Find location and distance to water for datum height (0 m AHD)
        out = profiles_df.groupby(['site', 'date']).apply(waterline_intercept, 
                                                          dist_col='chainage',
                                                          x_col='easting', 
                                                          y_col='northing', 
                                                          z_col='elevation', 
                                                          z_val=0).dropna()
        
        # If the output contains data
        if len(out.index):

            # Join into dataframe
            shoreline_dist = out.join(
                profiles_df.groupby(['site', 'date']).first())

            # Keep required columns
            shoreline_dist = shoreline_dist[['beach', 'section', 'profile',  
                                             'source', 'start_x', 'start_y', 
                                             'end_x', 'end_y', '0_dist', 
                                             '0_x', '0_y']]

            # Export to file
            shoreline_dist.to_csv(f'{fname_out}.csv')
    
    else:
        print(f'Skipping {fname}             ', end='\r')


def preprocess_narrabeen(fname, fname_out, overwrite=False):

    # Test if file exists
    if not os.path.exists(fname_out) or overwrite:

        #################
        # Location data #
        #################

        # Import data and parse DMS to DD
        print(f'Processing {fname_out}             ', end='\r')
        data = "PF1 -33°42'20.65 151°18'16.30 118.42\n" \
               "PF2 -33°42'33.45 151°18'10.33 113.36\n" \
               "PF4 -33°43'01.55 151°17'58.84 100.26\n" \
               "PF6 -33°43'29.81 151°17'58.65 83.65\n" \
               "PF8 -33°43'55.94 151°18'06.47 60.48"
        coords = pd.read_csv(StringIO(data),
                             sep=' ',
                             names=['profile', 'y', 'x', 'angle'])
        coords['x'] = [dms2dd(i) for i in coords.x]
        coords['y'] = [dms2dd(i) for i in coords.y]

        # Extend survey lines out from start coordinates using supplied angle
        coords_end = coords.apply(
            lambda x: dist_angle(x.x, x.y, 0.005, x.angle), axis=1)
        coords = pd.concat([coords, coords_end], axis=1).drop('angle', axis=1)

        # Rename fields
        coords = coords.rename({'y': 'start_y',
                                'x': 'start_x',
                                'y2': 'end_y',
                                'x2': 'end_x'}, axis=1)

        # Reproject coords to Albers and create geodataframe
        trans = Transformer.from_crs("EPSG:4326", "EPSG:28356", always_xy=True)
        coords['start_x'], coords['start_y'] = trans.transform(
            coords.start_x.values, coords.start_y.values)
        coords['end_x'], coords['end_y'] = trans.transform(
            coords.end_x.values, coords.end_y.values)

        # Add ID column
        coords['profile'] = coords['profile'].astype(str).str.lower()
        coords['beach'] = 'narrabeen'
        coords['section'] = 'all'
        coords['site'] = coords[['beach', 'section',
                                 'profile']].apply('_'.join, 1)

        ###############
        # Survey data #
        ###############

        # Import data
        profiles_df = pd.read_csv(
            fname,
            usecols=[1, 2, 3, 4, 5],
            skiprows=1,
            parse_dates=['date'],
            names=['profile', 'date', 'distance', 'elevation', 'source'])

        # Restrict to post 1987
        profiles_df = profiles_df[(profiles_df.date.dt.year > 1987)]

        # Add transect site and origin/end points into dataframe
        profiles_df['profile'] = profiles_df['profile'].astype(str).str.lower()
        profiles_df = profiles_df.merge(coords, on='profile')

        # Add coordinates for every distance along transects
        profiles_df[['x', 'y']] = profiles_df.apply(lambda x: pd.Series(
            dist_along_transect(x.distance, 
                                x.start_x, x.start_y, 
                                x.end_x, x.end_y)), axis=1)

        # Find location and distance to water for datum height (0 m AHD)
        out = profiles_df.groupby(['site', 'date']).apply(waterline_intercept,
                                                          dist_col='distance',
                                                          x_col='x',
                                                          y_col='y',
                                                          z_col='elevation',
                                                          z_val=0).dropna()

        # If the output contains data
        if len(out.index):

            # Join into dataframe
            shoreline_dist = out.join(
                profiles_df.groupby(['site', 'date']).first())

            # Keep required columns
            shoreline_dist = shoreline_dist[['beach', 'section', 'profile', 
                                             'source', 'start_x', 'start_y',
                                             'end_x', 'end_y', '0_dist', 
                                             '0_x', '0_y']]

            # Export to file
            shoreline_dist.to_csv(fname_out)
            
    else:
        print(f'Skipping {fname}             ', end='\r')
        
        

def deacl_validation(val_path,
                     deacl_path,
                     sat_label='DEA CoastLines beach width (m)',
                     val_label='Validation beach width (m)'):
    
    # Load validation data
    val_df = pd.read_csv(val_path, parse_dates=['date'])
    
    # Get title for plot
    title = val_df.beach.iloc[0].capitalize()

    # Get bounding box to load data for
    minx, maxy = val_df.min().loc[['0_x', '0_y']]
    maxx, miny = val_df.max().loc[['0_x', '0_y']]
    bbox = gpd.GeoSeries(box(minx, miny, maxx, maxy), crs='EPSG:28356')

    # Import corresponding waterline contours
    deacl_gdf = gpd.read_file(deacl_path, bbox=bbox).to_crs(epsg=28356)
    
    if len(deacl_gdf.index) > 0:
    
        # Set year dtype to allow merging
        deacl_gdf['year'] = deacl_gdf.year.astype('int64')

        # Add year column
        val_df['year'] = val_df.date.dt.year

        # Aggregate by year and save count number and source
        source = val_df.groupby(['year', 'site']).source.agg(pd.Series.mode)
        counts = val_df.groupby(['year', 'site']).date.count()
        val_df = val_df.groupby(['year', 'site']).median()
        val_df['n'] = counts
        val_df['source'] = source
        val_df = val_df.reset_index()

        # Convert validation start and end locations to linestrings
        val_geometry = val_df.apply(
            lambda x: LineString([(x.start_x, x.start_y), 
                                  (x.end_x, x.end_y)]), axis=1)

        # Convert geometries to GeoDataFrame
        val_gdf = gpd.GeoDataFrame(data=val_df,
                                         geometry=val_geometry,
                                         crs='EPSG:28356').reset_index()

        # Combine to match each shoreline contour to each date in validation data
        results_df = val_gdf.merge(deacl_gdf,
                                   on='year',
                                   suffixes=('_val', '_deacl'))

        # For each row, compute distance between origin and location where 
        # profile intersects with waterline contour
        results_df[sat_label] = results_df.apply(
            lambda x: x.geometry_val.intersection(x.geometry_deacl)
            .hausdorff_distance(Point(x.start_x, x.start_y)), axis=1)
        results_df = results_df.rename({'0_dist': val_label}, axis=1)
        results_df = results_df[(results_df[sat_label] > 0) & 
                                (results_df[val_label] > 0)]
        
        # Select validation and satellite data
        sat_data = results_df[sat_label]
        val_data = results_df[val_label] 

        # Calculate stats
        rmse = mean_squared_error(val_data, sat_data) ** 0.5
        mae = mean_absolute_error(val_data, sat_data)
        r2 = r2_score(val_data, sat_data)
        cor = results_df[[sat_label, val_label]].corr().iloc[0, 1]

        # Plot image
        fig, ax = plt.subplots(figsize=(8.5, 7))
        results_df.plot.scatter(x=val_label,
                                y=sat_label,
                                c=results_df.year,
                                s=15,
                                cmap='YlOrRd',
                                vmin=1987,
                                vmax=2018,
                                ax=ax)
        ax.plot(np.linspace(0, max(val_data.max(), sat_data.max())),
                np.linspace(0, max(val_data.max(), sat_data.max())),
                color='black',
                linestyle='dashed')
        ax.set_title(title)
        ax.annotate(f'RMSE: {rmse:.2f} m\n' \
                    f'MAE: {mae:.2f} m\n' \
                    f'R-squared: {r2:.2f}\n' \
                    f'Correlation: {cor:.2f}', 
                    xy=(0.05, 0.85),
                    xycoords='axes fraction',
                    fontsize=11)

        # Export to file
        fig.savefig(f'figures/{Path(val_path).stem}.png', 
                    bbox_inches='tight', 
                    dpi=150)
        
        return {'site': Path(val_path).stem, 
                'rmse': rmse, 'mae': mae, 'r2': r2, 'cor': cor}
        
        
    
def main(argv=None):
    
    #########
    # Setup #
    #########
    
    if argv is None:

        argv = sys.argv
        print(sys.argv)

    # If no user arguments provided
    if len(argv) < 2:

        str_usage = "You must specify an analysis name"
        print(str_usage)
        sys.exit()
        
    # Set study area and name for analysis
    output_name = str(argv[1])
        

    ###############################
    # Load DEA CoastLines vectors #
    ###############################
    



if __name__ == "__main__":
    main()