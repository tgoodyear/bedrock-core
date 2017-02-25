"""db.py is a layer for interacting with the database across all of the bedrock apis."""
import pymongo
from bedrock.CONSTANTS import MONGO_HOST, MONGO_PORT, DATALOADER_DB_NAME

def db_client(host=MONGO_HOST, port=MONGO_PORT):
    client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)
    return client

def db_collection(client, db, collection_name):
    collection = client[db][collection_name]
    return collection

def get_db_config(client, config_db="bedrock_config", config_collection="config"):
    col = client[config_db][config_collection]
    try:
        return col.find({})[0]
    except:
        return {}

def drop_id_key(record):
    """returns a copy of record without the key _id """
    return {key: value for key, value in record.items() if key != '_id'}


def find_matrix(col, src_id, mat_id):
    """finds a matrix of source in the collection col"""
    # matrix = col.find({'src_id':src_id, 'matrices.id':mtxid })
    matrices = col.find({'src_id': src_id})[0]['matrices']
    matrix_manual = None
    for matrix in matrices:
        if matrix['id'] == mat_id:
            matrix_manual = matrix
    # assert matrix == matrix_manual
    return matrix_manual