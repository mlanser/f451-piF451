"""Custom class for demo data.

This class defines a data structure that can be used 
to manage misc. demo data. This object follows overall 
design of SenseHat Data object, but is customized for
random demo data collected in the demo application.

Dependencies:
    TBD
"""

from collections import deque
import f451_sensehat.sensehat_data as f451SenseData


# =========================================================
#              M I S C .   C O N S T A N T S
# =========================================================


# =========================================================
#                     M A I N   C L A S S
# =========================================================
class DemoData:
    """Data structure for holding and managing demo data.

    This class follows same principles and design as used
    with the 'SystemData' and 'SenseData' classes.

    Attributes:
        number1: random number 
        number2: random number

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
        self.number1 = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            (1, 200),  # min/max range for valid data
            'km/h',
            [None, None, None, None],
            'Demo Speed',
        )
        self.number2 = f451SenseData.SenseObject(
            deque([defVal] * maxLen, maxlen=maxLen),
            (0, 100),  # min/max range for valid data
            '%',
            [None, None, None, None],
            'Demo Pcnt',
        )

    def as_list(self):
        return [
            self.number1.as_dict(),
            self.number2.as_dict(),
        ]
    
    def as_dict(self):
        return {
            'number1': self.number1.as_dict(),
            'number2': self.number2.as_dict(),
        }
