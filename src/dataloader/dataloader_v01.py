#!/usr/bin/env python2
#****************************************************************
#  File: DataLoaderAPIv01.py
#
# Copyright (c) 2015, Georgia Tech Research Institute
# All rights reserved.
#
# This unpublished material is the property of the Georgia Tech
# Research Institute and is protected under copyright law.
# The methods and techniques described herein are considered
# trade secrets and/or confidential. Reproduction or distribution,
# in whole or in part, is forbidden except by the express written
# permission of the Georgia Tech Research Institute.
#****************************************************************/
from __future__ import print_function

import json
import logging
import os
import re
import shutil
import socket
import string
import sys
import traceback
import urllib2
from datetime import datetime

import pymongo
import requests
from bson.json_util import dumps
from bson.objectid import ObjectId
from flask import (Flask, Response, jsonify, redirect, request,
                   send_from_directory, url_for)
from flask.ext import restful
from flask.ext.restplus import Api, Resource, fields
# raise Exception(utils)
from werkzeug import secure_filename

import utils
from CONSTANTS import DATALOADER_COL_NAME, DATALOADER_DB_NAME, DATALOADER_PATH
from CONSTANTS import INGEST_COL_NAME, RESULTS_PATH, RESULTS_COL_NAME  #, RESPATH
from CONSTANTS import FILTERS_COL_NAME
from core.db import db_connect, drop_id_key, find_matrix
from core.io import write_source_file, write_source_config
from core.models import Source, SourceCreated
# from django.utils.encoding import smart_str, smart_unicode

def explore(cur):
    """yields the matrices combined with their source information for the /dataloader/explorable/ endpoint
    a join on the matrices and sources.
    """
    for src in cur:
        for matrix in src['matrices']:
            exp = {}
            exp['rootdir'] = matrix['rootdir']
            exp['src_id'] = src['src_id']
            exp['id'] = matrix['id']
            exp['outputs'] = matrix['outputs']
            exp['name'] = matrix['name']
            exp['created'] = matrix['created']
            exp['mat_type'] = matrix['mat_type']
            yield exp


def find_source(col, src_id):
    """fing a source from pymongo collection"""
    return col.find({'src_id':src_id})[0]

def find_results(col, mat_id):
    """find the list of results associated with a matrix"""
    return col.find({'src_id':mat_id})[0]['results']

app = Flask(__name__)
app.debug = True

ALLOWED_EXTENSIONS = set(['csv', 'tsv', 'mtx', 'xls', 'xlsx', 'zip','txt'])

api = Api(app, version="0.1", title="DataLoader API",
    description="Analytics-Framework API supporting creation and use of matrices (Copyright &copy 2015, Georgia Tech Research Institute)")

ns = api.namespace('sources')
ns_i = api.namespace('ingest')
ns_f = api.namespace('filters')


@api.model(fields={
                'attrname': fields.String(description='Python variable name', required=True),
                'name': fields.String(description='Name to use for display', required=True),
                'type': fields.String(description='Kind of html input type to display', required=True),
                'value': fields.String(description='Default value to use', required=True),
                })
class Params(fields.Raw):
    def format(self, value):
        return {
                'attrname': value.attrname,
                'name': value.name,
                'type': value.type,
                'value': value.value,
                }

api.model('Filter', {
    'filter_id': fields.String(description='Unique ID for the filter', required=True),
    'classname': fields.String(description='Classname within the python file', required=True),
    'description': fields.String(description='Description for the filter'),
    'name': fields.String(description='Filter name'),
    'input': fields.String(description='Datatype this filter can be applied to'),
    'outputs': fields.List(fields.String, description='List of output files generated by the filter', required=True),
    'parameters': fields.List(Params, description='List of input parameters needed by the filter'),
    'possible_names': fields.List(fields.String, description='List of names that could indicate appropriateness for this filter', required=True),
    'stage': fields.String(description='Stage at which to apply the filter: {before, after}', required=True),
    'type': fields.String(description='Type of filter: {extract, convert, add}', required=True),
})

api.model('Ingest', {
    'ingest_id': fields.String(description='Unique ID for the ingest module', required=True),
    'classname': fields.String(description='Classname within the python file', required=True),
    'description': fields.String(description='Description for the ingest module'),
    'name': fields.String(description='Ingest module name'),
    'parameters': fields.List(Params, description='List of input parameters needed by the ingest module'),
})


@api.model(fields={
                'created': fields.String(description='Timestamp of creation'),
                'id': fields.String(description='Unique ID for the matrix', required=True),
                'src_id': fields.String(description='Unique ID for the source used to generate the matrix', required=True),
                'mat_type': fields.String(description='Matrix type'),
                'name': fields.String(description='Matrix name'),
                'outputs': fields.List(fields.String, description='List of output files associated with the matrix', required=True),
                'rootdir': fields.String(description='Path to the associated directory', required=True),
                })
class Matrix(fields.Raw):
    def format(self, value):
        return {
                'created': value.created,
                'id': value.id,
                'src_id': value.src_id,
                'mat_type': value.mat_type,
                'name': value.name,
                'outputs': value.outputs,
                'rootdir': value.rootdir
                }

api.model('Source', {
    'created': fields.String(description='Timestamp of creation'),
    'src_id': fields.String(description='Unique ID for the source', required=True),
    'name': fields.String(description='Source name'),
    'src_type': fields.String(description='Source type'),
    'matrices_count': fields.Integer(description='Number of matrices generated from this source'),
    'matrices': fields.List(Matrix, description='List of matrices generated from this source', required=True),
})

api.model('Features', {
    'features': fields.List(fields.String, description='List of features for this matrix', required=True),
})

@api.model(fields={
                'examples': fields.List(fields.String, description='Example samples for this field'),
                'key': fields.String(description='Name of this field', required=True),
                'key_usr': fields.String(description='User-edited name of this field', required=True),
                'suggestion': fields.String(description='Top suggested filter for this field'),
                'suggestions': fields.List(fields.String, description='List of other possible filters for this field', required=True),
                'type': fields.List(fields.String, description='Basic type for this field', required=True),
                })
class Schema(fields.Raw):
    def format(self, value):
        return {
                'created': value.created,
                'id': value.id,
                'src_id': value.src_id,
                'mat_type': value.mat_type,
                'name': value.name,
                'outputs': value.outputs,
                'rootdir': value.rootdir
                }

api.model('Schemas', {
    'name_of_source': fields.List(Schema, description='List of schemas for this source', required=True),
})


@ns_i.route('/')
class IngestModules(Resource):
    @api.doc(model='Ingest')
    def get(self):
        '''
        Returns a list of available ingest modules.
        All ingest modules registered in the system will be returned. If you believe there is an ingest module that exists in the system but is not present here, it is probably not registered in the MongoDB database.
        '''
        client, col = db_connect(INGEST_COL_NAME)
        cur = col.find()
        ingest = []
        for c in cur:
            response = drop_id_key(c)
            ingest.append(response)

        return ingest

    @ns_i.route('/<ingest_id>/')
    class Ingest(Resource):
        @api.doc(model='Ingest')
        def get(self, ingest_id):
            '''
            '''
            client, col = db_connect(INGEST_COL_NAME)

            try:
                src = col.find({'ingest_id':ingest_id})[0]

            except IndexError:
                return 'No resource at that URL', 401

            else:
                response = drop_id_key(src)

                return response


@ns_f.route('/')
class Filters(Resource):
    @api.doc(model='Filter')
    def get(self):
        '''
        Returns a list of available filters. All filters registered in the system
        will be returned. If you believe there is a filter that exists in the
        system but is not present here, it is probably not registered in the
        MongoDB database.
        '''
        client, col = db_connect(FILTERS_COL_NAME)
        cur = col.find()
        return map(drop_id_key, cur)


@ns.route('/')
class Sources(Resource):
    @api.doc(model='Source')
    def get(self):
        '''
        Returns a list of available sources.
        All sources registered in the system will be returned.
        '''
        client, col = db_connect(DATALOADER_COL_NAME)
        cur = col.find()
        sources = []
        for src in cur:
            try:
                sources.append({key: value for key, value in src.items() if key != '_id' and key != 'stash'})
            except KeyError:
                print(src)
        return sources

    @api.hide
    def delete(self):
        '''
        Deletes all stored sources.
        This will permanently remove all sources from the system. USE CAREFULLY!
        '''
        client, col = db_connect(DATALOADER_COL_NAME)
        #remove the entries in mongo
        col.remove({})
        #remove the actual files
        for directory in os.listdir(DATALOADER_PATH):
            file_path = os.path.join(DATALOADER_PATH, directory)
            shutil.rmtree(file_path)

        return '', 204

    @ns.route('/download/<src_id>/<matrix_id>/<output_file>/<file_download_name>/')
    class Download(Resource):
        def get(self,src_id,matrix_id,output_file,file_download_name):
            '''
            Downloads the specified matrix file.
            Returns the specific file indicated by the user.
            '''

            client, col = db_connect(DATALOADER_COL_NAME)
            try:
                matrices = col.find({'src_id':src_id})[0]['matrices']
            except IndexError:
                response = {}
                # return ('No resource at that URL.', 404)
            else:
                for matrix in matrices:
                    if matrix['id'] == matrix_id:
                        return send_from_directory(matrix['rootdir'],output_file, as_attachment=True, attachment_filename=file_download_name)

    @ns.route('/<name>/<ingest_id>/<group_name>/')
    class NewSource(Resource):
        @api.doc(model='Source')
        def put(self, name, ingest_id, group_name=""):
            '''
            Saves a new resource with a ID.
            Payload can be either a file or JSON structured configuration data. Returns the metadata for the new source.
            '''
            try:
                src_id = utils.getNewId()
                t = utils.getCurrentTime()
                conn_info = request.get_json()
                # conn_info = request.get_json(force=True)
                if conn_info == None:
                    file = request.files['file']
                    ext = re.split('\.', file.filename)[1]
                    if not ext in ALLOWED_EXTENSIONS:
                        print("WARN: File submitted %s is not of a supported filetype".format(file.filename))
                    #     return ('This filetype is not supported.', 415)

                    if 'zip' in file.filename:
                        src_type = 'zip'
                    else:
                        src_type = 'file'

                    rootpath, filepath = write_source_file(DATALOADER_PATH, src_id, file)

                else:
                    src_type = 'conf'
                    rootpath, filepath = write_source_config(DATALOADER_PATH, src_id, conn_info)

                client, col = db_connect(DATALOADER_COL_NAME)

                rootpath = DATALOADER_PATH  + src_id + '/'

                source = Source(name, rootpath, src_id, src_type, t, ingest_id, group_name)

                col.insert(source.dict())

                response = SourceCreated(source).dict()
            except:
                tb = traceback.format_exc()
                return tb, 406

            return response, 201


    @ns.route('/explorable/')
    class Explorable(Resource):
        @api.doc(model='Matrix')
        def get(self):
            '''
            Returns a list of generated matrices.
            '''
            client, col = db_connect(DATALOADER_COL_NAME)
            cur = col.find()
            explorables = list(explore(cur))
            return explorables

    @ns.route('/group/<group_name>/')
    class Group(Resource):
        @api.doc(model='Matrix')
        def get(self, group_name):
            '''
            Returns a list of sources within a particular group.
            '''

            client, col = db_connect(DATALOADER_COL_NAME)
            sources = col.find({'group_name':group_name})
            response = [drop_id_key(src) for src in sources]

            return response

    @ns.route('/groups/')
    class Groups(Resource):
        @api.doc(model='Matrix')
        def get(self):
            '''
            Returns a list of groups available.
            '''

            client, col = db_connect(DATALOADER_COL_NAME)
            groups = col.aggregate([{"$group":{"_id": "$group_name"}}])
            response = [src["_id"] for src in groups]

            return response

    @ns.route('/<src_id>/')
    class Source(Resource):
        @api.doc(model='Matrix')
        def get(self, src_id):
            '''
            Returns metadata and a list of matrices available for a particular source.
            '''
            client, col = db_connect(DATALOADER_COL_NAME)
            try:
                src = col.find({'src_id':src_id})[0]

            except IndexError:
                return 'No resource at that URL', 401

            else:
                response = drop_id_key(src)

                return response

        def delete(self, src_id):
            '''
            Deletes specified source.
            This will permanently remove this source from the system. USE CAREFULLY!
            '''
            _, col = db_connect(DATALOADER_COL_NAME)
            try:
                src = find_source(col, src_id)

            except IndexError:
                return 'No resource at that URL.', 404

            else:
                try:
                    matrices = src['matrices']
                except KeyError:
                    logging.info('No Matrices for source %s on delete', src_id)
                else:
                    _, rescol = db_connect(RESULTS_COL_NAME)
                    for mat in matrices:
                        mat_id = mat['id']
                        # this subtree is deleted when the DATALOADER_PATH/src_id gets removed later
                        # shutil.rmtree(os.path.join(DATALOADER_PATH, src_id, mat_id))
                        try:
                            # res = find_results(col, mat_id)
                            logging.info('going to remove %s/%s', RESPATH, mat_id)
                            shutil.rmtree(os.path.join(RESPATH, mat_id))
                            rescol.remove({'src_id':mat_id})

                        except Exception as ex:
                            logging.error('could not remove matrix results %s while deleting source %s exception:%s', mat_id, src_id, ex)

                # uses the dataloader opal deletion function
                try:
                    utils.delete(src)
                except Exception as ex:
                    return 'Dataloader opal failed to delete source: %s'%(src), 500
                try:
                    col.remove({'src_id':src_id})
                except:
                    return 'Failed to remove source from database', 500
                try:
                    shutil.rmtree(os.path.join(DATALOADER_PATH, src_id))
                except:
                    return 'Failed to delete source from disk', 500
                return 'Deleted Source: %s'%src_id, 204


        @api.doc(model='Matrix', body='Source')
        def post(self, src_id):
            '''
            Generate a matrix from the source stored at that ID.
            Returns metadata for that matrix.
            '''
            try:
                posted_data = request.get_json(force=True)
                client, col = db_connect(DATALOADER_COL_NAME)

                try:
                    src = col.find({'src_id':src_id})[0]

                except IndexError:
                    return 'No resource at that URL.', 404

                error, matricesNew = utils.ingest(posted_data, src)

                if error:
                    return 'Unable to create matrix.', 406

                matrices = []
                for each in src['matrices']:
                    matrices.append(each)
                matrices.extend(matricesNew)
                col.update({'src_id':src_id}, { '$set': {'matrices': matrices} })
            except:
                tb = traceback.format_exc()
                return tb, 406
            return matricesNew, 201


        @ns.route('/<src_id>/explore/')
        class Explore(Resource):
            @api.doc(model='Schemas')
            def get(self, src_id):
                '''
                Returns a list of schemas for a particular source.
                '''

                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]
                except IndexError:
                    return 'No resource at that URL.', 404

                filepath = src['rootdir'] + '/source/'

                #get filters
                f_client, f_col = db_connect(FILTERS_COL_NAME)
                filters = f_col.find()

                return utils.explore(src['ingest_id'], filepath, filters)

        @ns.route('/<src_id>/custom/<param1>/<param2>/')
        class Custom_2(Resource):
            def get(self, src_id, param1, param2, param3=None):
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]
                except IndexError:
                    return 'No resource at that URL.', 404
                filepath = src['rootdir']
                return utils.custom(src['ingest_id'], filepath, param1=param1, param2=param2, param3=param3, request=request.args)

            def post(self, src_id, param1=None, param2=None, param3=None):
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]
                except IndexError:
                    return 'No resource at that URL.', 404
                filepath = src['rootdir']
                return utils.custom(src['ingest_id'], filepath, param1=param1, param2=param2, param3=param3, payload=request.get_json())


        @ns.route('/<src_id>/custom/<param1>/')
        class Custom_1(Resource):
            def get(self, src_id, param1):
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]
                except IndexError:
                    return 'No resource at that URL.', 404
                filepath = src['rootdir']
                return utils.custom(src['ingest_id'], filepath, param1=param1, request=request.args)

            def post(self, src_id, param1=None, param2=None, param3=None):
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]
                except IndexError:
                    return 'No resource at that URL.', 404
                filepath = src['rootdir']
                return utils.custom(src['ingest_id'], filepath, param1=param1, param2=param2, param3=param3, payload=request.get_json())


        @ns.route('/<src_id>/stream/')
        class Stream(Resource):
            def post(self, src_id):
                '''
                For streaming, start or end the streaming service.
                No payload is sent for this request.
                '''
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]

                except IndexError:
                    return 'No resource at that URL.', 404

                filepath = src['rootdir']

                #get filters
                f_client, f_col = db_connect(FILTERS_COL_NAME)
                filters = f_col.find()
                return utils.stream(src['ingest_id'], filepath)

            def patch(self, src_id):
                '''
                For streaming, toggles streaming on or off.
                This request is used in conjunction with the POST request to this same endpoint.

                '''
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    src = col.find({'src_id':src_id})[0]

                except IndexError:
                    return 'No resource at that URL.', 404

                filepath = src['rootdir']

                #get filters
                f_client, f_col = db_connect(FILTERS_COL_NAME)
                filters = f_col.find()
                utils.update(src['ingest_id'], filepath)
                return


        @ns.route('/<src_id>/<mat_id>/')
        class Matrix(Resource):
            @api.doc(model='Matrix')
            def get(self, src_id, mat_id):
                '''
                Returns metadata for the matrix specified.
                '''
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    matrices = col.find({'src_id':src_id})[0]['matrices']

                except IndexError:
                    return 'No resource at that URL.', 404

                else:
                    for matrix in matrices:
                        if matrix['id'] == mat_id:
                            response = drop_id_key(matrix)

                            return response

                    return 'No resource at that URL.', 404

            def delete(self, src_id, mat_id):
                '''
                Deletes specified matrix.
                This will permanently remove this matrix and any results generated from it from the system. USE CAREFULLY!
                '''
                client, col = db_connect(DATALOADER_COL_NAME)
                try:
                    matrices = col.find({'src_id':src_id})[0]['matrices']

                except IndexError:
                    return 'No resource at that URL.', 404

                else:
                    matrices_new = []
                    found = False
                    for each in matrices:
                        if each['id'] != mat_id:
                            matrices_new.append(each)
                        else:
                            found = True
                    if found:
                        col.update({'src_id':src_id}, { '$set': {'matrices': matrices_new} })
                    else:
                        return 'No resource at that URL.', 404

                    shutil.rmtree(DATALOADER_PATH + src_id + '/' + mat_id)

                    col = client[DATALOADER_DB_NAME][RESULTS_COL_NAME]
                    try:
                        col.remove({'src_id':mat_id})
                        shutil.rmtree(RESPATH + mat_id)

                    except:
                        pass

                    else:
                        return '', 204


            @ns.route('/<src_id>/<mat_id>/output/')
            class Output(Resource):
                def get(self, src_id, mat_id):
                    '''
                    Returns the REAMDME content for the specified matrix.
                    '''
                    client, col = db_connect(DATALOADER_COL_NAME)
                    try:
                        matrix = find_matrix(col, src_id, mat_id)
                    except IndexError:
                        return 'No resource at that URL.', 404
                    except AssertionError:
                        return 'Bad mongo query', 500
                    else:
                        try:
                            output_path = matrix['rootdir'] + 'output.txt'
                            with open(output_path) as output:
                                text = output.read()
                            return text
                        except:
                            return 'No Output document for %s/%s'%(src_id, mat_id), 404

                    return 'No resource at that URL.', 404

            @ns.route('/<src_id>/<mat_id>/features/')
            class Features(Resource):
                @api.doc(model='Features')
                def get(self, src_id, mat_id):
                    '''
                    Returns features for the specified matrix.
                    Features are the names for the columns within the matrix.
                    '''
                    client, col = db_connect(DATALOADER_COL_NAME)
                    try:
                        matrix = find_matrix(col, src_id, mat_id)
                    except IndexError:
                        return 'No resource at that URL.', 404
                    except AssertionError:
                        return 'Bad Mongo Query', 500
                    else:
                        rootdir = matrix['rootdir']
                        features_filepath = rootdir + 'features.txt'
                        try:
                            with open(features_filepath) as features_file:
                                features = features_file.read().split("\n")
                                features.pop()
                            response = features
                        except IOError:
                            response = []

                        return response
