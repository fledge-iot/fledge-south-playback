======================
fledge-south-playback
======================

Fledge South Plugin for playback. `read more <https://github.com/fledge/fledge-south-playback/blob/master/python/fledge/plugins/south/playback/readme.rst>`_

Configuration options explained
===============================

.. code-block:: console

    - 'csvFilename': The name of the CSV file with extension. The location is assumed to be data/ folder.
    - 'headerRow': True if first row of the CSV file contains column headings else leave it unchecked.
    - 'fieldNames': If no header row is present in the CSV file, then provide a comma separated list of column names. The list must contain equal no. of labels as no. of columns in the CSV file.
    - 'readingCols': If readings to be ingested for selective columns and/or with different label, a dict must be provided in the format {old label:new label, ...}. Note that this is useful if header row is present, otherwise we can define the desired label in fieldNames itself.
    - 'timestampFromFile': Select this if a timestamp column is present in the CSV file AND the user_ts will be based upon this instead if system time.
    - 'timestampCol': Mandatory if timestampFromFile is choosen. Provide the timestamp column name from headerRow or fieldNames, as the case may be.
    - 'timestampFormat': Mandatory if timestampFromFile is choosen. Python timestamp format. Default is '%Y-%m-%d %H:%M:%S.%f'. If timestamp format is not known, provide 'None'. In that case, system will try to guess the timestamp and this will be slower.
    - 'ingestMode': Choose one from 'burst' or 'batch'.
    - 'sampleRate': Ingest rate valid for 'batch' mode only and only when timestampFromFile is NOT choosen.
    - 'burstInterval': Interval in ms between two consecutive bursts.
    - 'burstSize': No. of readings sets in one burst.
    - 'repeatLoop': If file is small, then we may want to read it in a loop.

``NOTE: CSV string data with single quote (') and double quotes(") are not supported.``
