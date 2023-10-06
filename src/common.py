"""Helper Module for applications on the f451 Labs piF451 device.

This module holds a few common helper functions that can be used across 
most/all applications designed for the f451 Labs piF451 device.
"""

import sys
import constants as const


# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
EXIT_NOW = False    # Global flag for immediate (graceful) exit

def exit_now(self, *args):
    """Changes global 'EXIT_NOW' flag.
    
    This function is called/triggered by signals (e.g. SIGINT, SIGTERM, etc.)
    and allows us run some clean-up tasks before shutting down.
    
    NOTE: It's not possible to catch SIGKILL
    
    Based on code from: https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully/31464349#31464349
    """
    global EXIT_NOW
    EXIT_NOW = True


def get_setting(settings, key, default=None):
    """Get a config value from settings
    
    This function rerieves value from settings (TOML), but can
    return a default value if key does not exist (i.e. settings 
    value has not been defined in TOML file.

    Args:
        settings:
            'dict' with settings values
        key:
            'str' with name of settings key
        defaul:
            Default value

    Returns:
        Settings value        
    """
    return settings[key] if key in settings else default


def num_to_range(num, inMin, inMax, outMin, outMax):
    """Map value to range

    We use this function to map values (e.g. temp, etc.) against the Y-axis of 
    the SenseHat 8x8 LED display. This means that all values must be mapped 
    against a range of 0-7.

    Based on code found here: https://www.30secondsofcode.org/python/s/num-to-range/

    Args:
        num:
            Number to map against range
        inMin:
            Min value of range for numbers to be converted
        inMax:
            Max value of range for numbers to be converted
        outMin:
            Min value of target range
        outMax:
            Max value of target range

    Returns:
        'float'
    """
    return outMin + (float(num - inMin) / float(inMax - inMin) * (outMax - outMin))


def convert_to_rgb(num, inMin, inMax, colors):
    """
    Map a value to RGB

    Based on reply found on StackOverflow by `martineau`: 

    See: https://stackoverflow.com/questions/20792445/calculate-rgb-value-for-a-range-of-values-to-create-heat-map

    Args:
        num:
            Number to convert/map to RGB
        inMin:
            Min value of range for numbers to be converted
        inMax:
            Max value of range for numbers to be converted
        colors:
            series of RGB colors delineating a series of adjacent 
            linear color gradients.

    Returns:
        'tuple' with RGB value
    """
    EPSILON = sys.float_info.epsilon    # Smallest possible difference

    # Determine where the given value falls proportionality within
    # the range from inMin->inMax and scale that fractional value
    # by the total number in the `colors` palette.
    i_f = float(num - inMin) / float(inMax - inMin) * (len(colors) - 1)

    # Determine the lower index of the pair of color indices this
    # value corresponds and its fractional distance between the lower
    # and the upper colors.
    i, f = int(i_f // 1), i_f % 1  # Split into whole & fractional parts.

    # Does it fall exactly on one of the color points?
    if f < EPSILON:
        return colors[i]
    
    # ... if not, then return a color linearly interpolated in the 
    # range between it and the following one.
    else:
        (r1, g1, b1), (r2, g2, b2) = colors[i], colors[i+1]
        return int(r1 + f * (r2 - r1)), int(g1 + f * (g2 - g1)), int(b1 + f * (b2 - b1))


def convert_to_bool(inVal):
    """Convert value to boolean.

    If value is a string, then we check against predefined string 
    constants. If value is an integer, then we return 'True' if value
    is greater than 0 (zero).

    For anything else we return a 'False'. 

    Args:
        inVal:
            Value to be converted to boolean.
    """
    if isinstance(inVal, int) or isinstance(inVal, float):
        return (abs(int(inVal)) > 0)
    elif isinstance(inVal, str):
        return (inVal.lower() in [const.STATUS_ON, const.STATUS_TRUE, const.STATUS_YES])
    else:
        return False
