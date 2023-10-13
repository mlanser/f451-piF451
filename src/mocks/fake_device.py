"""Mock version of SenseHat library.

This mock version of the SenseHat library can be used during 
testing, etc. It mimicks the SenseHat sensors by generating
random values within the limits of the actual hardware.
"""
import random
import constants as const

# =========================================================
#              M I S C .   C O N S T A N T S
# =========================================================
ACTION_PRESSED = False
ACTION_HELD = False
ACTION_RELEASED = False


class Stick:
    def __init__(self):
        self.direction_up = None
        self.direction_down = None
        self.direction_left = None
        self.direction_right = None
        self.direction_middle = None


class FakeHat:
    def __init__(self):
        self.low_light = True
        self.rotation = 0
        self.stick = Stick()

    def clear(self):
        pass

    def set_pixel(self, *args):
        pass

    def set_pixels(self, *args):
        pass

    def set_rotation(self, *args):
        pass

    def set_imu_config(self, *args):
        pass

    def get_temperature(self):
        return random.randint(const.MIN_TEMP * 10, const.MAX_TEMP * 10) / 10
    
    def get_pressure(self):
        return random.randint(const.MIN_PRESS * 10, const.MAX_PRESS * 10) / 10
    
    def get_humidity(self):
        return random.randint(const.MIN_HUMID * 10, const.MAX_HUMID * 10) / 10
