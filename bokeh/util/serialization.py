""" Functions for helping with serialization and deserialization of
Bokeh objects.

"""
from __future__ import absolute_import

import base64

from six import iterkeys

from .dependencies import import_optional
from ..settings import settings

is_numpy = None

try:
    import numpy as np
    is_numpy = True
    array_types = set([
        np.dtype(np.float32),
        np.dtype(np.float64),
        np.dtype(np.uint8),
        np.dtype(np.int8),
        np.dtype(np.uint16),
        np.dtype(np.int16),
        np.dtype(np.uint32),
        np.dtype(np.int32),
    ])
except ImportError:
    is_numpy = False
    array_types = set()

pd = import_optional('pandas')

import logging
log = logging.getLogger(__name__)

_simple_id = 1000

def make_id():
    """ Return a new unique ID for a Bokeh object.

    Normally this function will return UUIDs to use for identifying Bokeh
    objects. This is especally important for Bokeh objects stored on a
    Bokeh server. However, it is convenient to have more human-readable
    IDs during development, so this behavior can be overridden by
    setting the environment variable ``BOKEH_SIMPLE_IDS=yes``.

    """
    global _simple_id

    import uuid
    from ..settings import settings

    if settings.simple_ids(False):
        _simple_id += 1
        new_id = _simple_id
    else:
        new_id = uuid.uuid4()
    return str(new_id)

def transform_series(obj):
    """transforms pandas series into array of values
    """
    vals = obj.values
    return transform_array(vals)

def transform_array_to_list(array):
    if (array.dtype.kind in ('u', 'i', 'f') and (~np.isfinite(array)).any()):
        transformed = array.astype('object')
        transformed[np.isnan(array)] = 'NaN'
        transformed[np.isposinf(array)] = 'Infinity'
        transformed[np.isneginf(array)] = '-Infinity'
        return transformed.tolist()
    return array.tolist()

def encoding_disabled(array):
    """Checks if array should be serialized"""

    # user setting to disable overrides everything else
    if not settings.use_binary_arrays():
        return True

    # disable for non-supported dtypes
    if array.dtype not in array_types:
        return True

    # disable if the array size is less than the settings threshold
    array_samples = np.product(array.shape)
    return array_samples < settings.binary_array_cutoff()

def serialize_array(array, force_list=False):
    """Transforms array into one of two serialization formats
    either a list or a dictionary containing the base64
    encoded data along with the shape and dtype of the data.
    """
    if isinstance(array, np.ma.MaskedArray):
        array = array.filled(np.nan)  # Set masked values to nan
    if (encoding_disabled(array) or force_list):
        return transform_array_to_list(array)
    if not array.flags['C_CONTIGUOUS']:
        array = np.ascontiguousarray(array)
    return encode_base64_dict(array)

def transform_array(obj, force_list=False):
    """Transform arrays to a serializeable format
    Converts unserializeable dtypes and returns json serializeable
    format

    Args:
        obj (np.ndarray) : array to be transformed
        force_list : force a list based representation
    """
    # Check for astype failures (putative Numpy < 1.7)
    try:
        dt2001 = np.datetime64('2001')
        legacy_datetime64 = (dt2001.astype('int64') ==
                             dt2001.astype('datetime64[ms]').astype('int64'))
    ## For compatibility with PyPy that doesn't have datetime64
    except AttributeError as e:
        if e.args == ("'module' object has no attribute 'datetime64'",):
            import sys
            if 'PyPy' in sys.version:
                legacy_datetime64 = False
                pass
            else:
                raise e
        else:
            raise e

    ## not quite correct, truncates to ms..
    if obj.dtype.kind == 'M':
        if legacy_datetime64:
            if obj.dtype == np.dtype('datetime64[ns]'):
                array = obj.astype('int64') / 10**6.0
        else:
            array =  obj.astype('datetime64[us]').astype('int64') / 1000.
    elif obj.dtype.kind == 'm':
        array = obj.astype('timedelta64[us]').astype('int64') / 1000.
    else:
        array = obj
    return serialize_array(array, force_list)

def traverse_data(datum, is_numpy=is_numpy, use_numpy=True):
    """recursively dig until a flat list is found
    if numpy is available convert the flat list to a numpy array
    and send off to transform_array() to handle nan, inf, -inf
    otherwise iterate through items in array converting non-json items

    Args:
        datum (list) : a list of values or lists
        is_numpy: True if numpy is present (see imports)
        use_numpy: toggle numpy as a dependency for testing purposes
    """
    is_numpy = is_numpy and use_numpy
    if is_numpy and all(isinstance(el, np.ndarray) for el in datum):
        return [transform_array(el) for el in datum]
    datum_copy = []
    for item in datum:
        if isinstance(item, (list, tuple)):
            datum_copy.append(traverse_data(item))
        elif isinstance(item, float):
            if np.isnan(item):
                item = 'NaN'
            elif np.isposinf(item):
                item = 'Infinity'
            elif np.isneginf(item):
                item = '-Infinity'
            datum_copy.append(item)
        else:
            datum_copy.append(item)
    return datum_copy

def transform_column_source_data(data):
    """iterate through the data of a ColumnSourceData object replacing
    non-JSON-compliant objects with compliant ones
    """
    data_copy = {}
    for key in iterkeys(data):
        if pd and isinstance(data[key], (pd.Series, pd.Index)):
            data_copy[key] = transform_series(data[key])
        elif isinstance(data[key], np.ndarray):
            data_copy[key] = transform_array(data[key])
        else:
            data_copy[key] = traverse_data(data[key])
    return data_copy

def encode_base64_dict(array):
    return {
        '__ndarray__'  : base64.b64encode(array.data).decode('utf-8'),
        'shape'        : array.shape,
        'dtype'        : array.dtype.name
    }

def decode_base64_dict(data):
    """
    Decode base64 encoded data into numpy array.
    """
    b64 = base64.b64decode(data['__ndarray__'])
    array = np.fromstring(b64, dtype=data['dtype'])
    if len(data['shape']) > 1:
        array = array.reshape(data['shape'])
    return array

def decode_column_data(data):
    """
    Decodes base64 encoded column source data.
    """
    new_data = {}
    for k, v in data.items():
        if isinstance(v, dict) and '__ndarray__' in v:
            new_data[k] = decode_base64_dict(v)
        elif isinstance(v, list):
            new_list = []
            for el in v:
                if isinstance(el, dict) and '__ndarray__' in el:
                    el = decode_base64_dict(el)
                new_list.append(el)
            new_data[k] = new_list
        else:
            new_data[k] = v
    return new_data
