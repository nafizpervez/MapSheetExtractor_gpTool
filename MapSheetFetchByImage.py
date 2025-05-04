# -*- coding: utf-8 -*-
#

# Esri start of added imports
import sys, os, arcpy

# Esri end of added imports

# Esri start of added variables
g_ESRI_variable_1 = os.path.join(arcpy.env.packageWorkspace, "..\\cd\\acd")
# Esri end of added variables

import arcpy
import traceback
import requests
from arcgis.gis import GIS
from arcgis.raster import ImageryLayer
import logging
import os


def error_msgs(log_dir):
    log_file = os.path.join(log_dir, "process_log.txt")
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y%m%dT%H%M%S",
    )
    logger = logging.getLogger()
    return logger


def login_to_gis(logger, portal_url, token):
    logger.info(f"Connecting to GIS with Portal URL: {portal_url}")
    # If you prefer to ignore SSL certificate issues, use verify_cert=False
    gis = GIS(portal_url, token=token, verify_cert=False)
    return gis


def imagery_query(logger, imagery_service_layer, gis, sentinel_image_name):
    imagery_layer = ImageryLayer(imagery_service_layer, gis=gis)
    where_clause = f"Name='{sentinel_image_name}'"
    logger.info(f"Querying imagery layer with where clause: {where_clause}")

    query_result = imagery_layer.query(
        where=where_clause, return_geometry=False, out_fields=["OBJECTID"]
    )

    if "features" in query_result and len(query_result["features"]) > 0:
        object_id = query_result["features"][0]["attributes"]["OBJECTID"]
        return object_id
    else:
        warning_msg = "No results found for the given image name."
        arcpy.AddWarning(warning_msg)
        logger.warning(warning_msg)
        return None


def retrieve_and_build_geometry(logger, imagery_service_layer, object_id, token):
    """
    Retrieves geometry JSON from a service URL, converts it to an ArcPy Polygon,
    and builds the polygon using a known spatial reference (EPSG:3857).
    """
    image_url = f"{imagery_service_layer}/{object_id}?token={token}&f=json"

    logger.info(f"Retrieving image data from URL: {image_url}")

    # Perform the request
    response = requests.get(image_url, verify=False)

    if response.status_code == 200:
        try:
            response_json = response.json()
            geometry = response_json["geometry"]
            rings = geometry.get("rings", [])

            # Basic validation
            if not rings or not isinstance(rings[0], list) or len(rings[0]) == 0:
                arcpy.AddError("Error: Invalid ring geometry format.")
                return None

            # Use a known spatial reference (Web Mercator)
            web_mercator_sr = arcpy.SpatialReference(3857)

            # Build the polygon in EPSG:3857
            polygon = arcpy.Polygon(
                arcpy.Array([arcpy.Point(*coords) for coords in rings[0]]),
                web_mercator_sr,
            )

            return polygon

        except ValueError:
            tb = traceback.format_exc()
            error_msg = (
                f"Failed to parse JSON. Raw response: {response.text}\n"
                f"Traceback details:\n{tb}"
            )
            arcpy.AddError(error_msg)
            logger.error(error_msg)
            return None

    else:
        error_msg = (
            f"Failed to retrieve data from the URL. "
            f"Status code: {response.status_code}"
        )
        arcpy.AddError(error_msg)
        logger.error(error_msg)
        return None


def perform_spatial_selection(logger, mapsheet_layer_url, polygon, gis):
    """
    Fetches all SHEET_NO values from the feature service by paging through results.
    """
    # Get an ArcGIS FeatureLayer object pointing to your map sheet layer.
    from arcgis.features import FeatureLayer

    # Create the FeatureLayer using ArcGIS Python API
    feature_layer = FeatureLayer(mapsheet_layer_url, gis=gis)

    # Convert the ArcPy polygon to a geometry dict for the ArcGIS API
    # (assuming your polygon is in EPSG:3857 / Web Mercator)
    geom_dict = polygon.__geo_interface__  # GeoJSON-like dictionary

    # Now gather SHEET_NO with pagination
    sheet_no_list = []
    page_size = 2000
    offset = 0

    # Keep looping while records are returned
    while True:
        try:
            # Query with geometry filtering (spatial relationship = "esriSpatialRelWithin")
            # resultOffset, resultRecordCount let us get multiple pages
            query_result = feature_layer.query(
                geometry=geom_dict,
                spatial_rel="esriSpatialRelWithin",
                out_fields="sheet_no",
                return_geometry=False,
                result_offset=offset,
                result_record_count=page_size,
            )

            features = query_result.features
            if not features:
                break

            # Collect SHEET_NO values
            for feat in features:
                sheet_no_list.append(feat.attributes["sheet_no"])

            # Increase offset and see if we need another page
            offset += page_size
            if len(features) < page_size:
                break

        except Exception as e:
            tb = traceback.format_exc()
            error_msg = (
                f"Error during paged query for SHEET_NO: {str(e)}\n"
                f"Traceback details:\n{tb}"
            )
            arcpy.AddError(error_msg)
            logger.error(error_msg)
            break

    return sheet_no_list


def print_sheet_no_count(sheet_no_list):
    """
    Prints the unique sheet numbers and their total count to the ArcGIS console
    and sets the derived output parameters (mapsheet_value, mapsheet_count).
    Indices (based on your screenshot):
      5 -> mapsheet_value
      6 -> mapsheet_count
    """
    unique_sheet_no = set(sheet_no_list)

    # Sort the sheet numbers for consistent output (optional)
    sorted_sheet_list = sorted(unique_sheet_no)

    # Build a list-like string: e.g. [T47N, T48N, T49N]
    sheets_str = "[" + ", ".join(sorted_sheet_list) + "]"

    # Set the derived output parameter (index 5 = mapsheet_value)
    arcpy.SetParameterAsText(5, sheets_str)

    # Compute the total count
    total_sheet_no_count = len(unique_sheet_no)

    # Set the derived output parameter (index 6 = mapSheet_Count)
    arcpy.SetParameterAsText(6, str(total_sheet_no_count))


def script_tool(
    imagery_service_layer, sentinel_image_name, mapsheet_layer_url, portal_url, token
):

    # Initialize logger
    log_dir = g_ESRI_variable_1  # Change this to your desired log directory
    logger = error_msgs(log_dir)

    # Validate inputs
    if not portal_url:
        error_msg = "All input parameters are required."
        arcpy.AddError(error_msg)
        logger.error(error_msg)
        return

    try:
        # 1. Login to GIS
        gis = login_to_gis(logger, portal_url, token)

        # 2. Imagery Query
        object_id = imagery_query(
            logger, imagery_service_layer, gis, sentinel_image_name
        )
        if object_id is not None:
            # 3. Retrieve and build geometry
            polygon = retrieve_and_build_geometry(
                logger, imagery_service_layer, object_id, token
            )
            if polygon is not None:
                # 4. Perform spatial selection
                sheet_no_list = perform_spatial_selection(
                    logger, mapsheet_layer_url, polygon, gis
                )

                # 5. Print count and mapsheet no (also sets derived outputs).
                print_sheet_no_count(sheet_no_list)
        else:
            arcpy.AddMessage(
                "No valid OBJECTID found. Exiting without further processing."
            )

    except Exception as e:
        tb = traceback.format_exc()
        error_msg = f"Error during query execution: {str(e)}\nTraceback details:\n{tb}"
        arcpy.AddError(error_msg)
        logger.error(error_msg)


if __name__ == "__main__":
    imagery_service_layer = arcpy.GetParameterAsText(0)
    sentinel_image_name = arcpy.GetParameterAsText(1)
    mapsheet_layer_url = arcpy.GetParameterAsText(2)
    portal_url = arcpy.GetParameterAsText(3)
    token = arcpy.GetParameterAsText(4)
    # Indices 5 and 6 (mapsheet_value, mapsheet_count) are derived outputs

    # Run the script tool with the provided parameters
    script_tool(
        imagery_service_layer,
        sentinel_image_name,
        mapsheet_layer_url,
        portal_url,
        token,
    )
