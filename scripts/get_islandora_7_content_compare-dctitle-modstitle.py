#!/usr/bin/env python3

'''Script for exporting Islandora 7 content (metadata and OBJ datastreams). See
   https://mjordan.github.io/islandora_workbench_docs/exporting_islandora_7_content/
   for more info.
'''

import os
import re
import requests
import mimetypes
import logging
import csv
import pandas as pd
from requests_futures.sessions import FuturesSession
from progress_bar import InitBar

############################
# Configuration variables. #
############################

# Change to False to only fetch CSV metadata.
fetch_files = True

#starting unique identifier
starting_unique_id = 1

# URLs, paths, etc.
solr_base_url = 'http://128.206.4.208:8080/solr42'
islandora_base_url = 'https://dl.mospace.umsystem.edu/'
csv_output_path = '/home/borwickja/PhpstormProjects/islandoraworkbench/islandora_workbench/input_data/metadata.csv'
# Will be created if it doesn't exist.
#obj_directory = '/tmp/objs'
obj_directory = '/home/borwickja/PhpstormProjects/islandoraworkbench/islandora_workbench/input_data'
log_file_path = 'islandora_content.log'

# Solr filter criteria. 'namespace' allows you to limit the metadata retrieved
# from Solr to be limtited to objects with a specific namespace (or namespaces
# if multiple namespaces start with the same characters). Valid values for the
# 'namespace' variabe are a single namespace, a right-truncated string (e.g., island*),
# or an ansterisk (*).
#namespace = 'testing'
namespace = 'umkclaw'
# 'field_pattern' is a regex pattern that matches Solr field names to include in the
# CSV. For example,  'mods_.*(_s|_ms)$' will include fields that start with mods_ and
# end with _s or _ms.
field_pattern = 'mods_.*(_s|_ms)$'
# 'field_pattern_do_not_want' is a regex pattern that matches Solr field names
# to not include in the CSV. For example, '(SFU_custom_metadata|marcrelator)' will remove
# fieldnames that contain 'SFU_custom_metadata' or the string 'marcrelator.
field_pattern_do_not_want = '(SFU_custom_metadata|marcrelator|isSequenceNumberOf)'
# 'standard_fields' is a list of fieldnames we always want in fields list. They are
# added added to the field list after list is filtered down using 'field_pattern'.
# Columns for these fields will appear at the start of the CSV.
standard_fields = ['PID']

##############
# Functions. #
##############

def addStandardFields(modsfields_list):

   fields_param = standard_fields + modsfields_list
   return fields_param



#function added by Jim Borwick to get around the mongo SOLR query for all fields
def getFieldListFromFile():
    #bypasses part of main program logic, some of which isn't encapsulated in functions
    #might think about rewriting
    if os.path.exists('/home/borwickja/PycharmProjects/parseIslandoraCSV/mods_titlefield.txt'):
      filehandle =  open('/home/borwickja/PycharmProjects/parseIslandoraCSV/mods_titlefield.txt','r')
      modsfields_list = filehandle.readlines()
      stripped_modsfield_list = list(map(str.strip, modsfields_list))
      fields_param = addStandardFields(stripped_modsfield_list)
      return fields_param


def get_extension_from_mimetype(mimetype):
    # mimetypes.add_type() is not working, e.g. mimetypes.add_type('image/jpeg', '.jpg')
    # Maybe related to https://bugs.python.org/issue4963? In the meantime, provide our own
    # MIMETYPE to extension mapping for common types, then let mimetypes guess at others.
    map = {'image/jpeg': '.jpg',
        'image/jp2': '.jp2',
        'image/png': '.png'
    }
    if mimetype in map:
        return map[mimetype]
    else:
        return mimetypes.guess_extension(mimetype)

def get_child_sequence_number(pid):
    '''For a given Islandora 7.x PID, get the object's sequence number in relation
       to its parent from the RELS-EXT datastream. Assumes child objects are only
       children of a single parent.
    '''
    rels_ext_url = islandora_base_url + '/islandora/object/' + pid + '/datastream/RELS-EXT/download'
    try:
        rels_ext_download_response = requests.get(url=rels_ext_url, allow_redirects=True)
        if rels_ext_download_response.status_code == 200:
            rels_ext_xml = rels_ext_download_response.content.decode()
            matches = re.findall('<(islandora:isPageOf|fedora:isConstituentOf)\s+rdf:resource="info:fedora/(.*)">', rels_ext_xml, re.MULTILINE)
            # matches contains tuples, but we only want the values from the second value in each tuple,
            # pids corresponding to the second set of () in the pattern.
            parent_pids = [pids[1] for pids in matches]
            if len(parent_pids) > 0:
                parent_pid = parent_pids[0].replace(':', '_')
                sequence_numbers = re.findall('<islandora:isSequenceNumberOf' + parent_pid + '>(\d+)', rels_ext_xml, re.MULTILINE)
                # Paged content stores sequence values in <islandora:isSequenceNumber>, so we look there
                # if we didn't get any in isSequenceNumberOfxxx.
                if len(sequence_numbers) == 0:
                    sequence_numbers = re.findall('<islandora:isSequenceNumber>(\d+)', rels_ext_xml, re.MULTILINE)
                if len(sequence_numbers) > 0:
                    return sequence_numbers[0]
                else:
                    logging.warning("Can't get sequence number for " + pid)
                    return ''
            else:
                logging.warning("Can't get parent PID for " + pid)
                return ''
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)

def get_percentage(part, whole):
    return 100 * float(part) / float(whole)

def getFieldListFromSolr():
    # This query gets all fields in the index. Does not need to be user-configurable.
    fields_solr_query = '/select?q=*:*&wt=csv&rows=0&fl=*'
    fields_solr_url = solr_base_url + fields_solr_query
    # Get the complete field list from Solr and filter it. The filtered field list is
    # then used in another query to get the populated CSV data.
    try:
        field_list_response = requests.get(url=fields_solr_url, allow_redirects=True)
        raw_field_list = field_list_response.content.decode()
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)

    field_list = raw_field_list.split(',')
    filtered_field_list = [keep for keep in field_list if re.search(field_pattern, keep)]
    filtered_field_list = [discard for discard in filtered_field_list if not re.search(field_pattern_do_not_want, discard)]

    # Add required fieldnames.
    standard_fields.reverse()
    for standard_field in standard_fields:
        filtered_field_list.insert(0, standard_field)
    fields_param = ','.join(filtered_field_list)

    return fields_param

def createSOLRRequest():

    return solr_base_url + '/select?q=PID:' + namespace + '*&wt=csv&rows=1000000&fl=' + fields_param


def runSOLRRequest():

    try:
        metadata_solr_response = requests.get(url=metadata_solr_request, allow_redirects=True)
        print(metadata_solr_response)
        raise SystemExit(e)
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)

def getpidcampusprefix(pid):

    pid_pieces = pid.split(':')
    return pid_pieces[0]


def getcampuscodefrompid(pid):

    campus_prefix = getpidcampusprefix(pid)
    print(campus_prefix)

    returnval = ''
    campus_codes = ['umkc','umkclaw','umsl','mu']
    for code in campus_codes:
        if campus_prefix == code:
            returnval = code
            break

    return returnval



def getRequestStatus(requestObject):

    response_one = requestObject.result()
    print(response_one.status_code)
    if response_one.status_code != 200:
        print(response_one.status_code)
        getRequestStatus(requestObject)
    elif response_one.status_code == 200:
        return 'success'


#######################
# Main program logic. #
#######################

logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S')

fields_param = ','.join(getFieldListFromFile())

# This query gets all fields in the index. Does not need to be user-configurable.
# fields_solr_query = '/select?q=*:*&wt=csv&rows=0&fl=*'
# fields_solr_url = solr_base_url + fields_solr_query



# Get the complete field list from Solr and filter it. The filtered field list is
# then used in another query to get the populated CSV data.
# try:
#     field_list_response = requests.get(url=fields_solr_url, allow_redirects=True)
#     raw_field_list = field_list_response.content.decode()
# except requests.exceptions.RequestException as e:
#     raise SystemExit(e)
#
# field_list = raw_field_list.split(',')
# filtered_field_list = [keep for keep in field_list if re.search(field_pattern, keep)]
# filtered_field_list = [discard for discard in filtered_field_list if not re.search(field_pattern_do_not_want, discard)]

# Add required fieldnames.
# standard_fields.reverse()
# for standard_field in standard_fields:
#     filtered_field_list.insert(0, standard_field)
# fields_param = ','.join(filtered_field_list)

# Get the populated CSV from Solr, with the object namespace and field list filters applied.
#metadata_solr_request = solr_base_url + '/select?q=PID:' + namespace + '*&wt=csv&rows=&fl=' + fields_param
#metadata_solr_request = solr_base_url + '/select?q=PID%3A' + namespace + '*&wt=csv&rows=&fl=' + fields_param
metadata_solr_request = solr_base_url + '/select?q=dc.title%3A+*' + '*&wt=csv&start=530001&rows=10000&fl=' + fields_param



try:
    metadata_solr_response = requests.get(url=metadata_solr_request, allow_redirects=True)
except requests.exceptions.RequestException as e:
    raise SystemExit(e)


    # We add a 'sequence' column to store the Islandora 7.x property "isSequenceNumberOfxxx"/"isSequenceNumber".
    rows[0] = 'id,' + 'file,' + rows[0] + ',sequence'
    header_field_list = rows[0].split(',')


#     for index, field in enumerate(header_field_list):
#         print(index, field)
#         if field == 'mods_titleInfo_title_ms':
#             header_field_list[index] = 'title'
#
#
#     rows[0] = ','.join(header_field_list)
#     rows[0] = rows[0] + "," + 'title_length'

rows = metadata_solr_response.content.decode().splitlines()
filehandle = open('dc_titles_pids.csv','a')
for row in rows:
    if(isinstance(row, str)):
        print(row)
        # field_values = row.split(',') this doesn't work because comma character is also splitting title on comma
        """  title = field_values[1]
         pid = field_values[0]
         if(len(title) > 0): """
        filehandle.write(row + "\n")
