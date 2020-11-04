# -*- coding: utf-8 -*-

# FLEDGE_BEGIN
# See: http://fledge.readthedocs.io/
# FLEDGE_END

""" Module for playback async plugin """

import copy
import csv
import os
import json
import logging
import datetime
import time
import ast
import re
from threading import Event
from queue import Queue
from threading import Thread, Condition

from fledge.common import logger
from fledge.plugins.common import utils
import async_ingest


__author__ = "Amarendra Kumar Sinha"
__copyright__ = "Copyright (c) 2018 Dianomic Systems"
__license__ = "Apache 2.0"
__version__ = "${VERSION}"


_DEFAULT_CONFIG = {
    'plugin': {
        'description': 'Test Sample',
        'type': 'string',
        'default': 'playback',
        'readonly': 'true'
    },
    'assetName': {
        'description': 'Name of Asset',
        'type': 'string',
        'default': 'sample',
        'displayName': 'Asset Name',
        'order': '1',
        'mandatory': 'true'
    },
    'csvFilename': {
        'description': 'CSV File name',
        'type': 'string',
        'default': 'some.csv',
        'displayName': 'CSV file name with extension',
        'order': '2'
    },
    'headerRow': {
        'description': 'If header row is preset as first row of CSV file',
        'type': 'boolean',
        'default': 'true',
        'displayName': 'Header Row',
        'order': '3'
    },
    'fieldNames': {
        'description': 'Comma separated list of column names, mandatory if headerRow is false',
        'type': 'string',
        'default': 'None',
        'displayName': 'Header columns',
        'order': '4'
    },
    'readingCols': {
        'description': 'Cherry pick data columns with the same/new name',
        'type': 'JSON',
        'default': '{}',
        'displayName': 'Cherry pick column with same/new name',
        'order': '5'
    },
    'timestampFromFile': {
        'description': 'Time Delta to be chosen from a column in the CSV',
        'type': 'boolean',
        'default': 'false',
        'displayName': 'Pick timestamp delta from file',
        'order': '7'
    },
    'historicTimestamps': {
        'description': 'Use original timestamps rather than ingest in real time',
        'type': 'boolean',
        'default': 'false',
        'displayName': 'Historic timestamps',
        'order': '6'
    },
    'timestampCol': {
        'description': 'Timestamp header column, mandatory if timestampFromFile is true',
        'type': 'string',
        'default': 'ts',
        'displayName': 'Timestamp column name',
        'order': '8'
    },
    'timestampFormat': {
        'description': 'Timestamp format in File, mandatory if timestampFromFile is true',
        'type': 'string',
        'default': '%Y-%m-%d %H:%M:%S.%f',
        'displayName': 'Timestamp format',
        'order': '9'
    },
    'ingestMode': {
        'description': 'Mode of data ingest - burst/batch',
        'type': 'enumeration',
        'default': 'batch',
        'options': ['batch', 'burst'],
        'displayName': 'Ingest mode',
        'order': '10'
    },
    'sampleRate': {
        'description': 'No. of readings per sec',
        'type': 'integer',
        'default': '100',
        'displayName': 'Sample Rate',
        'minimum': '1',
        'maximum': '1000000',
        'order': '11'
    },
    'burstInterval': {
        'description': 'Time interval between consecutive bursts in milliseconds',
        'type': 'integer',
        'default': '1000',
        'displayName': 'Burst Interval (ms)',
        'minimum': '1',
        'order': '12'
    },
    'burstSize': {
        'description': 'No. of data points in one burst',
        'type': 'integer',
        'default': '1',
        'displayName': 'Burst size',
        'minimum': '1',
        'order': '13'
    },
    'repeatLoop': {
        'description': 'Read CSV in a loop i.e. on reaching EOF, again go back to beginning of the file',
        'type': 'boolean',
        'default': 'false',
        'displayName': 'Read file in a loop',
        'order': '14'
    },
}

_FLEDGE_ROOT = os.getenv("FLEDGE_ROOT", default='/usr/local/fledge')
_FLEDGE_DATA = os.path.expanduser(_FLEDGE_ROOT + '/data')
_LOGGER = logger.setup(__name__, level=logging.INFO)
producer = None
consumer = None
bucket = None
condition = None
BUCKET_SIZE = 1
wait_event = Event()
c_callback = None
c_ingest_ref = None


def plugin_info():
    """ Returns information about the plugin.
    Args:
    Returns:
        dict: plugin information
    Raises:
    """
    return {
        'name': 'Playback',
        'version': '1.8.2',
        'mode': 'async',
        'type': 'south',
        'interface': '1.0',
        'config': _DEFAULT_CONFIG
    }


def plugin_init(config):
    """ Initialise the plugin.
    Args:
        config: JSON configuration document for the South plugin configuration category
    Returns:
        data: JSON object to be used in future calls to the plugin
    Raises:
    """
    data = copy.deepcopy(config)
    try:
        errors = False
        csv_file_name = "{}/{}".format(_FLEDGE_DATA, data['csvFilename']['value'])
        if not os.path.isfile(csv_file_name):
            _LOGGER.exception('csv filename "{}" not found'.format(csv_file_name))
            errors = True
        if not data['csvFilename']['value']:
            _LOGGER.exception("csv filename cannot be empty")
            errors = True
        if int(data['sampleRate']['value']) < 1 or int(data['sampleRate']['value']) > 1000000:
            _LOGGER.exception("sampleRate should be in range 1-1000000")
            errors = True
        if int(data['burstSize']['value']) < 1:
            _LOGGER.exception("burstSize should not be less than 1")
            errors = True
        if int(data['burstInterval']['value']) < 1:
            _LOGGER.exception("burstInterval should not be less than 1")
            errors = True
        if data['ingestMode']['value'] not in ['burst', 'realtime', 'batch']:
            _LOGGER.exception("ingestMode should be one of ('burst', 'realtime', 'batch')")
            errors = True
        if errors:
            raise RuntimeError
    except KeyError:
        raise
    except RuntimeError:
        raise
    return data


def plugin_start(handle):
    """ Extracts data from the playback and returns it in a JSON document as a Python dict.
    Available for async mode only.

    Args:
        handle: handle returned by the plugin initialisation call
    Returns:
        a playback reading in a JSON document, as a Python dict
    """
    global producer, consumer, bucket, condition, BUCKET_SIZE, wait_event

    if handle['timestampFromFile']['value'] == 'false':
        BUCKET_SIZE = int(handle['sampleRate']['value'])

    condition = Condition()
    bucket = Queue(BUCKET_SIZE)
    producer = Producer(bucket, condition, handle)
    consumer = Consumer(bucket, condition, handle)

    wait_event.clear()

    producer.start()
    consumer.start()


def plugin_reconfigure(handle, new_config):
    """ Reconfigures the plugin

    Args:
        handle: handle returned by the plugin initialisation call
        new_config: JSON object representing the new configuration category for the category
    Returns:
        new_handle: new handle to be used in the future calls
    """
    _LOGGER.info("Old config for playback plugin {} \n new config {}".format(handle, new_config))

    plugin_shutdown(handle)

    new_handle = plugin_init(new_config)
    plugin_start(new_handle)

    return new_handle


def plugin_shutdown(handle):
    """ Shutdowns the plugin doing required cleanup, to be called prior to the South plugin service being shut down.

    Args:
        handle: handle returned by the plugin initialisation call
    Returns:
        plugin shutdown
    """
    global producer, consumer, wait_event, bucket, condition

    wait_event.set()
    if producer is not None:
        producer._tstate_lock = None
        producer._stop()
        producer = None
    if consumer is not None:
        consumer._tstate_lock = None
        consumer._stop()
        consumer = None
    if bucket is not None:
        bucket.unfinished_tasks = 0
        bucket = None
    condition = None
    _LOGGER.info('playback plugin shut down.')


def plugin_register_ingest(handle, callback, ingest_ref):
    """Required plugin interface component to communicate to South C server

    Args:
        handle: handle returned by the plugin initialisation call
        callback: C opaque object required to passed back to C->ingest method
        ingest_ref: C opaque object required to passed back to C->ingest method
    """
    global c_callback, c_ingest_ref
    c_callback = callback
    c_ingest_ref = ingest_ref


class Producer(Thread):
    def __init__(self, queue, cond, handle):
        super(Producer, self).__init__()
        self.queue = queue
        self.condition = cond
        self.handle = handle

        try:
            if self.handle['ingestMode']['value'] == 'burst':
                burst_interval = int(self.handle['burstInterval']['value'])
                self.period = round(burst_interval / 1000.0, len(str(burst_interval)) + 1)
            else:
                recs = int(self.handle['sampleRate']['value'])
                self.period = round(1.0 / recs, len(str(recs)) + 1)
        except ZeroDivisionError:
            _LOGGER.warning('sampleRate must be greater than 0, defaulting to 1')
            self.period = 1.0

        self.csv_file_name = "{}/{}".format(_FLEDGE_DATA, self.handle['csvFilename']['value'])
        self.has_header = True if self.handle['headerRow']['value'] == 'true' else False
        with open(self.csv_file_name, 'r') as data_file:
            headr = data_file.readline()
            headr = headr.strip().replace('\n', '').replace('\r', '').replace(' ', '')
            self.field_names = headr.split(',') if self.has_header else \
                None if self.handle['fieldNames']['value'] == 'None' else self.handle['fieldNames']['value'].split(",")

        # Cherry pick columns from readings and if desired, with
        rc = self.handle['readingCols']['value']
        self.reading_cols = rc if isinstance(rc, dict) else json.loads(rc)
        if self.handle['historicTimestamps']['value'] == 'false' and self.handle['timestampFromFile']['value'] == 'true':
            self.exclude_ts_column_from_reading_columns()

        self.iter_sensor_data = iter(self.get_data())

        self.prv_readings_ts = None
        self.timestamp_interval = None

    def exclude_ts_column_from_reading_columns(self):
        ts_col = self.handle['timestampCol']['value']
        new_reading_cols = self.reading_cols.copy()
        if len(self.reading_cols) > 0:
            for k, v in self.reading_cols.items():
                if ts_col != k:
                    new_reading_cols.update({k: v})
        else:
            for i in self.field_names:
                if ts_col != i:
                    new_reading_cols.update({i: i})
        self.reading_cols = new_reading_cols.copy()

    def get_data(self):
        with open(self.csv_file_name, 'r') as data_file:
            reader = csv.DictReader(data_file, fieldnames=self.field_names)
            # Skip Header
            if self.has_header:
                next(reader)
            regex = re.compile('[ `~!@#$%^&*()_=}{\]\[|;:"<>,?/\\\'ABCDFGHIJKLMNOPQRSTUVWXYZabcdfghijklmnopqrstuvwxyz]')
            regex_num = re.compile('[+-0123456789]')
            regex_float = re.compile('[.eE]')
            for line in reader:
                new_line = {}
                for k, v in line.items():
                    try:
                        contains_numbers = regex_num.search(v) is not None
                        contains_float = regex_float.search(v) is not None
                        contains_string = regex.search(v) is not None
                        if contains_string:
                            nv = v
                        elif contains_numbers:
                            if contains_float:
                                nv = float(v) if isinstance(ast.literal_eval(v), float) else v
                            else:
                                nv = int(v) if isinstance(ast.literal_eval(v), int) else v
                        else:
                            nv = v
                    except ValueError:
                        nv = v
                    new_line.update({k: nv})
                yield new_line

    def get_time_stamp_diff(self, readings):
        # The option to have the timestamp come from a column in the CSV file. The first timestamp should
        # be treated as a base time for all the readings and the current time substituted for that time stamp.
        # Successive readings should be sent with the same time delta as the times in the file. I.e. if the
        # file contains a timestamp series 14:01:23, 14:01:53, 14:02:23,.. and the time we sent the first
        # row of data is 18:15:45 then the second row should be sent at 18:16:15, preserving the same time
        # difference between the rows.
        c = 0
        try:
            ts_col = self.handle['timestampCol']['value']
            ts_format = self.handle['timestampFormat']['value']
            readings_ts = datetime.datetime.strptime(readings[ts_col], ts_format)
            if self.prv_readings_ts is not None:
                c = readings_ts - self.prv_readings_ts
                c = c.total_seconds()
            self.prv_readings_ts = readings_ts
        except Exception as ex:
            raise RuntimeError(str(ex))
        return c

    def run(self):
        eof_reached = False
        while True:
            time_start = time.time()
            sensor_data = {}
            try:
                if self.handle['ingestMode']['value'] == 'burst':
                    # Support for burst of data. Allow a burst size to be defined in the configuration, default 1.
                    # If a size of greater than 1 is set then that number of input values should be sent as an
                    # array of value. E.g. with a burst size of 10, which data point in the reading will be an
                    # array of 10 elements.
                    burst_data_points = []
                    for i in range(int(self.handle['burstSize']['value'])):
                        readings = next(self.iter_sensor_data)
                        # If we need to cherry pick cols, and possibly with a different name
                        if len(self.reading_cols) > 0:
                            new_dict = {}
                            for k, v in self.reading_cols.items():
                                if k in readings:
                                    new_dict.update({v: readings[k]})
                            burst_data_points.append(new_dict)
                        else:
                            burst_data_points.append(readings)
                    sensor_data.update({"data": burst_data_points})
                    next_iteration_secs = self.period
                else:
                    readings = next(self.iter_sensor_data)
                    # If we need to cherry pick cols, and possibly with a different name
                    if len(self.reading_cols) > 0:
                        for k, v in self.reading_cols.items():
                            if k in readings:
                                sensor_data.update({v: readings[k]})
                    else:
                        sensor_data.update(readings)
                    if self.handle['historicTimestamps']['value'] == 'true':
                        next_iteration_secs = self.period
                    elif self.handle['timestampFromFile']['value'] == 'true':
                        next_iteration_secs = self.get_time_stamp_diff(readings)
                    else:
                        next_iteration_secs = self.period
            except StopIteration as ex:
                _LOGGER.warning("playback - EOF reached: {}".format(str(ex)))
                eof_reached = True
                if self.handle['ingestMode']['value'] == 'burst':
                    if len(burst_data_points) > 0:
                        sensor_data.update({"data": burst_data_points})
            except Exception as ex:
                _LOGGER.warning("playback producer exception: {}".format(str(ex)))

            wait_event.wait(timeout=next_iteration_secs - (time.time()-time_start))
            time_stamp = utils.local_timestamp()

            if not self.condition._is_owned():
                self.condition.acquire()
            if len(sensor_data) > 0:
                value = {'data': sensor_data, 'ts': time_stamp}
                self.queue.put(value)
            if self.queue.full() or eof_reached:
                self.condition.notify()
                self.condition.release()

            if eof_reached:
                # Rewind CSV file if it is to be read in an infinite loop
                if self.handle['repeatLoop']['value'] == 'true':
                    # repeatLoop should not continue if thread has been signalled to stop
                    if self._tstate_lock is None:
                        return
                    self.iter_sensor_data = iter(self.get_data())
                    eof_reached = False
                else:
                    return


class Consumer(Thread):
    def __init__(self, queue, cond, handle):
        super(Consumer, self).__init__()
        self.queue = queue
        self.condition = cond
        self.handle = handle

    def run(self):
        global wait_event
        while True:
            if wait_event.is_set():  # i.e. shutdown called
                return
            self.condition.acquire()
            while self.queue.qsize() > 0:
                data = self.queue.get()
                reading = data['data']
                
                if self.handle['historicTimestamps']['value'] == 'true':
                    ts_col = self.handle['timestampCol']['value']
                    raw_time_stamp = reading.pop(ts_col)
                    ts_format = self.handle['timestampFormat']['value']
                    time_stamp = str(datetime.datetime.strptime(raw_time_stamp, ts_format))
                else:
                    time_stamp = data['ts']

                reading = {
                    'asset': self.handle['assetName']['value'],
                    'timestamp': time_stamp,
                    'readings': reading
                }
                async_ingest.ingest_callback(c_callback, c_ingest_ref, reading)
            self.condition.notify()
            self.condition.release()
