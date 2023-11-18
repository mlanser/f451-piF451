"""Custom class for System data.

This class defines a data structure that can be used 
to manage misc. system data. This object follows overall 
design of SenseHat Data object, but is customized for
system data collected in the 'sysmon' application.

Dependencies:
    TBD
"""

from collections import deque
import f451_sensehat.sensehat_data as f451SenseData


# =========================================================
#              M I S C .   C O N S T A N T S
# =========================================================
# TEMP_UNIT_C = "C"   # Celsius
# TEMP_UNIT_F = "F"   # Fahrenheit
# TEMP_UNIT_K = "K"   # Kelvin


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
        temperature:    temperature in C
        pressure:       barometric pressure in hPa
        humidity:       humidity in %
        light:          illumination in Lux

    Methods:
        as_list: returns a 'list' with data from each attribute as 'dict'
        convert_C2F: static (wrapper) method. Converts Celsius to Fahrenheit 
        convert_C2K: static (wrapper) method. Converts Celsius to Kelvin 
    """
    def __init__(self, defVal, maxLen):
        """Initialize data structurte.

        Args:
            defVal: default value to use when filling up the queues
            maxLen: max length of each queue

        Returns:
            'dict' - holds entiure data structure
        """
        self.pressure = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            "hPa",
            [250, 650, 1013.25, 1015],
            "Pressure"
        )
        self.humidity = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            "%",
            [20, 30, 60, 70],
            "Humidity"
        )
        self.light = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            "Lux",
            [-1, -1, 30000, 100000],
            "Light"
        )

    def as_list(self, tempUnit=TEMP_UNIT_C):
        return [
            self.temperature.as_dict(tempUnit),
            self.pressure.as_dict(),
            self.humidity.as_dict(),
            self.light.as_dict(),
        ]
    
    def convert_C2F(self, celsius):
        return self.temperature._convert_C2F(celsius)

    def convert_C2K(self, celsius):
        return self.temperature._convert_C2K(celsius)
