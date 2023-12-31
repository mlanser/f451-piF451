"""Custom class for System data.

This class defines a data structure that can be used 
to manage misc. system data. This object follows overall 
design of SenseHat Data object, but is customized for
system data collected in the 'sysmon' application.
"""

from collections import deque
import f451_sensehat.sensehat_data as f451SenseData


# =========================================================
#                     M A I N   C L A S S
# =========================================================
class SystemData:
    """Data structure for holding and managing system data.

    Create an empty full-size data structure that we use
    in the app to collect a series of system data.

    NOTE: The 'limits' attribute stores a list of limits. You
            can define your own warning limits for your environment
            data as follows:

            Example limits explanation for temperature:
            [4,18,28,35] means:
            -273.15 ... 4     -> Dangerously Low
                  4 ... 18    -> Low
                 18 ... 28    -> Normal
                 28 ... 35    -> High
                 35 ... MAX   -> Dangerously High

    DISCLAIMER: The limits provided here are just examples and come
    with NO WARRANTY. The authors of this example code claim
    NO RESPONSIBILITY if reliance on the following values or this
    code in general leads to ANY DAMAGES or DEATH.

    Attributes:
        download:       download speed in MB/sec
        upload:         upload speed in MB/sec
        ping:           ping response time in ms

    Methods:
        as_list: returns a 'list' with data from each attribute as 'dict'
    """

    def __init__(self, defVal, maxLen):
        """Initialize data structurte.

        Args:
            defVal: default value to use when filling up the queues
            maxLen: max length of each queue

        Returns:
            'dict' - holds entiure data structure
        """
        self.download = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            (None, None),  # min/max range for valid data
            'MB/s',
            [None, None, None, None],
            'Download',
        )
        self.upload = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            (None, None),  # min/max range for valid data
            'MB/s',
            [None, None, None, None],
            'Upload',
        )
        self.ping = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            (None, None),  # min/max range for valid data
            'ms',
            [None, None, None, None],
            'Ping',
        )

    def as_list(self):
        return [
            self.download.as_dict(),
            self.upload.as_dict(),
            self.ping.as_dict(),
        ]

    def as_dict(self):
        return {
            'download': self.download.as_dict(),
            'upload': self.upload.as_dict(),
            'ping': self.ping.as_dict(),
        }

