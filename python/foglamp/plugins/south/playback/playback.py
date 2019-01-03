# -*- coding: utf-8 -*-

# FOGLAMP_BEGIN
# See: http://foglamp.readthedocs.io/
# FOGLAMP_END

""" Module for playback async plugin """

import asyncio
import copy
import csv
import uuid
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

from foglamp.common import logger
from foglamp.plugins.common import utils
from foglamp.services.south import exceptions
from foglamp.services.south.ingest import Ingest


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
        'order': '1'
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
        'description': 'Cherry pick data columns with the same/new name e.g. {"readings": "readings", "ts": "timestamp"}',
        'type': 'JSON',
        'default': '{}',
        'displayName': 'Cherry pick column with same/new name',
        'order': '5'
    },
    'timestampFromFile': {
        'description': 'Time Delta to be choosen from a column in the CSV',
        'type': 'boolean',
        'default': 'false',
        'displayName': 'Time stamp from file?',
        'order': '6'
    },
    'timestampCol': {
        'description': 'Timestamp header column, mandatory if timestampFromFile is true',
        'type': 'string',
        'default': 'ts',
        'displayName': 'Timestamp column name',
        'order': '7'
    },
    'timestampFormat': {
        'description': 'Timestamp format in File, mandatory if timestampFromFile is true',
        'type': 'string',
        'default': '%Y-%m-%d %H:%M:%S.%f',
        'displayName': 'Timestamp format',
        'order': '8'
    },
    'ingestMode': {
        'description': 'Mode of data ingest - burst/batch',
        'type': 'enumeration',
        'default': 'batch',
        'options': ['batch', 'burst'],
        'displayName': 'Ingest mode',
        'order': '9'
    },
    'sampleRate': {
        'description': 'No. of readings per sec',
        'type': 'integer',
        'default': '100',
        'displayName': 'Sample Rate',
        'minimum': '1',
        'maximum': '1000000',
        'order': '10'
    },
    'burstInterval': {
        'description': 'Time interval between consecutive bursts in milliseconds',
        'type': 'integer',
        'default': '1000',
        'displayName': 'Burst Interval (ms)',
        'minimum': '1',
        'order': '11'
    },
    'burstSize': {
        'description': 'No. of data points in one burst',
        'type': 'integer',
        'default': '1',
        'displayName': 'Burst size',
        'minimum': '1',
        'order': '12'
    },
    'repeatLoop': {
        'description': 'Read CSV in a loop i.e. on reaching EOF, again go back to beginning of the file',
        'type': 'boolean',
        'default': 'false',
        'displayName': 'Read file in a loop',
        'order': '13'
    },
}

_FOGLAMP_ROOT = os.getenv("FOGLAMP_ROOT", default='/usr/local/foglamp')
_FOGLAMP_DATA = os.path.expanduser(_FOGLAMP_ROOT + '/data')
_LOGGER = logger.setup(__name__, level=logging.INFO)
producer = None
consumer = None
bucket = None
condition = None
BUCKET_SIZE = 1
wait_event = Event()


def plugin_info():
    """ Returns information about the plugin.
    Args:
    Returns:
        dict: plugin information
    Raises:
    """
    return {
        'name': 'Playback',
        'version': '1.0',
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
        csv_file_name = "{}/{}".format(_FOGLAMP_DATA, data['csvFilename']['value'])
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

    loop = asyncio.get_event_loop()
    condition = Condition()
    bucket = Queue(BUCKET_SIZE)
    producer = Producer(bucket, condition, handle, loop)
    consumer = Consumer(bucket, condition, handle, loop)

    wait_event.clear()

    producer.start()
    consumer.start()
    bucket.join()


def plugin_reconfigure(handle, new_config):
    """ Reconfigures the plugin

    Args:
        handle: handle returned by the plugin initialisation call
        new_config: JSON object representing the new configuration category for the category
    Returns:
        new_handle: new handle to be used in the future calls
    """
    _LOGGER.info("Old config for playback plugin {} \n new config {}".format(handle, new_config))
    # Find diff between old config and new config
    diff = utils.get_diff(handle, new_config)
    # Plugin should re-initialize and restart if key configuration is changed
    if 'assetName' in diff or 'csvFilename' in diff or 'headerRow' in diff or \
        'fieldNames' in diff or 'readingCols' in diff or 'timestampFromFile' in diff or \
        'timestampCol' in diff or 'timestampFormat' in diff or \
        'sampleRate' in diff or 'ingestMode' in diff or 'burstSize' in diff or 'burstInterval' in diff or \
        'repeatLoop' in diff:
        plugin_shutdown(handle)
        new_handle = plugin_init(new_config)
        new_handle['restart'] = 'yes'
        _LOGGER.info("Restarting playback plugin due to change in configuration key [{}]".format(', '.join(diff)))
    else:
        new_handle = copy.deepcopy(new_config)
        new_handle['restart'] = 'no'
    return new_handle


def plugin_shutdown(handle):
    """ Shutdowns the plugin doing required cleanup, to be called prior to the South plugin service being shut down.

    Args:
        handle: handle returned by the plugin initialisation call
    Returns:
        plugin shutdown
    """
    global producer, consumer, wait_event

    wait_event.set()
    if producer is not None:
        producer._tstate_lock = None
        producer._stop()
        producer = None
    if consumer is not None:
        consumer._tstate_lock = None
        consumer._stop()
        consumer = None
    _LOGGER.info('playback plugin shut down.')


class Producer(Thread):
    def __init__(self, queue, condition, handle, loop):
        super(Producer, self).__init__()
        self.queue = queue
        self.condition = condition
        self.handle = handle
        self.loop = loop

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

        self.csv_file_name = "{}/{}".format(_FOGLAMP_DATA, self.handle['csvFilename']['value'])
        self.iter_sensor_data = iter(self.get_data())

        # Cherry pick columns from readings and if desired, with
        self.reading_cols = json.loads(self.handle['readingCols']['value'])

        self.prv_readings_ts = None
        self.timestamp_interval = None

    def get_data(self):
        has_header = True if self.handle['headerRow']['value'] == 'true' else False
        field_names = None
        with open(self.csv_file_name, 'r' ) as data_file:
            headr = data_file.readline()
            headr = headr.strip().replace('\n','').replace('\r','').replace(' ','')
            field_names = headr.split(',') if has_header else \
                None if self.handle['fieldNames']['value'] == 'None' else self.handle['fieldNames']['value'].split(",")

        # TODO: Improve this and remove above lines
        # has_header = None
        # field_names = None
        # with open(self.csv_file_name, 'r' ) as data_file:
        #     headr = data_file.readline()
        #     has_header = csv.Sniffer().has_header(headr)
        #     headr = headr.strip().replace('\n','').replace('\r','').replace(' ','')
        #     field_names = self.handle['fieldNames']['value'].split(",") if has_header is None else headr.split(',')

        # Exclude timestampCol from reading_cols
        if self.handle['timestampFromFile']['value'] == 'true':
            ts_col = self.handle['timestampCol']['value']
            new_reading_cols = self.reading_cols.copy()
            if len(self.reading_cols) > 0:
                for k, v in self.reading_cols.items():
                    if ts_col != k: new_reading_cols.update({k: v})
            else:
                for i in field_names:
                    if ts_col != i: new_reading_cols.update({i:i})
            self.reading_cols = new_reading_cols.copy()

        with open(self.csv_file_name, 'r' ) as data_file:
            reader = csv.DictReader(data_file, fieldnames=field_names)
            # Skip Header
            if has_header:
                next(reader)
            regex = re.compile(
                '[ `~!@#$%^&*()_=+}{\]\[|;:"<>,?/\\\'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz]')
            for line in reader:
                new_line = {}
                for k, v in line.items():
                    try:
                        if regex.search(v) is not None:
                            nv = v
                        else:
                            nv = int(v) if isinstance(ast.literal_eval(v), int) else \
                                float(v) if isinstance(ast.literal_eval(v), float) else v
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
            time_stamp = None
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
                    if self.handle['timestampFromFile']['value'] == 'true':
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
            if len(sensor_data) > 0 :
                value = {'data': sensor_data, 'ts': time_stamp}
                self.queue.put(value)
            if self.queue.full() or eof_reached:
                self.condition.notify()
                self.condition.release()

            if eof_reached:
                # Rewind CSV file if it is to be read in an infinite loop
                if self.handle['repeatLoop']['value'] == 'true':
                    self.iter_sensor_data = iter(self.get_data())
                    eof_reached = False
                else:
                    return


class Consumer(Thread):
    def __init__(self, queue, condition, handle, loop):
        super(Consumer, self).__init__()
        self.queue = queue
        self.condition = condition
        self.handle = handle
        self.loop = loop

    def run(self):
        asyncio.set_event_loop(loop=self.loop)
        while True:
            self.condition.acquire()
            while self.queue.qsize() > 0:
                data = self.queue.get()
                asyncio.ensure_future(self.save_data(data['data'], data['ts']), loop=self.loop)
            self.condition.notify()
            self.condition.release()

    async def save_data(self, sensor_data, time_stamp):
        try:
            data = {
                'asset': self.handle['assetName']['value'],
                'timestamp': time_stamp,
                'key': str(uuid.uuid4()),
                'readings': sensor_data
            }
            await Ingest.add_readings(asset='{}'.format(data['asset']),
                                      timestamp=data['timestamp'], key=data['key'],
                                      readings=data['readings'])
        except (asyncio.CancelledError, RuntimeError):
            pass
        except RuntimeWarning as ex:
            _LOGGER.exception("playback warning: {}".format(str(ex)))
        except Exception as ex:
            _LOGGER.exception("playback exception: {}".format(str(ex)))
            raise exceptions.DataRetrievalError(ex)
