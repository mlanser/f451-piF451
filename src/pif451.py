#!/usr/bin/env python3
"""f451 Labs piF451 application.

This application is designed for the f451 Labs piF451 device which is also equipped with 
a SenseHat add-on. The object is to continously read environment data (e.g. temperature, 
barometric pressure, and humidity from the SenseHat sensors and then upload the data to 
the Adafruit IO service.

To launch this application from terminal:

    $ nohup python -u pif451.py > pif451.out &

This will start the application in the background and it will keep running even after 
terminal window is closed. Any output will be redirected to the 'pif451.out' file.    
"""

import time
import sys
import logging
import asyncio
import signal

from collections import deque
from random import randint
from pathlib import Path

from Adafruit_IO import Client, MQTTClient, RequestError, ThrottlingError

import constants as const

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

try:
    from sense_hat import SenseHat, ACTION_PRESSED, ACTION_HELD, ACTION_RELEASED
except ImportError:
    from mocks.fake_hat import FakeHat as SenseHat, ACTION_PRESSED, ACTION_HELD, ACTION_RELEASED


# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
class Device:
    def __init__(self, sense, aio, logger, config):
        self.sense = sense
        self.aio = aio
        self.logger = logger
        self.config = config
        self.displRotation = 0
        self.displSleep = 0
        self.displMode = 0
        self.displProgress = False
        self.sleepCounter = 0
        # self.ioDelay = 0
        # self.delayCounter = 0
        # self.maxDelay = 0

    def init_SenseHat(self):
        """Initialize SenseHat
        
        Initialize the SenseHat device, set some default 
        parameters, and clear LED.

        Args:
            defRotation:
                Default rotation
        """
        self.sense.clear()                               # Clear 8x8 LED
        self.sense.low_light = True

        self.sense.set_rotation(self.displRotation)      # Set initial rotation
        self.sense.set_imu_config(False, False, False)   # Disable IMU functions

        self.sense.stick.direction_up = self._pushed_up
        self.sense.stick.direction_down = self._pushed_down
        self.sense.stick.direction_left = self._pushed_left
        self.sense.stick.direction_right = self._pushed_right
        self.sense.stick.direction_middle = self._pushed_middle

    def init_logger(self, logLvl, logFile=None):
        """Initialize logger
        
        This method will always initialize the logger with a stream 
        handler. But file handler will only be created if a file
        name has been provided.

        Args:
            logLvl:
                Log level used for handlers
            logFile:
                Path object for log file
        """
        self.logger.setLevel(logLvl)

        if logFile:
            fileHandler = logging.FileHandler(logFile)
            fileHandler.setLevel(logLvl)
            fileHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
            self.logger.addHandler(fileHandler)

        streamHandler = logging.StreamHandler()
        streamHandler.setLevel(logLvl if logLvl == const.LOG_DEBUG else logging.ERROR)
        streamHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
        self.logger.addHandler(streamHandler)

    def _pushed_up(self, event):
        """SenseHat Joystick UP event
        
        Rotate display by -90 degrees and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displRotation = 270 if self.displRotation <= 0 else self.displRotation - const.ROTATE_90
            self.sleepCounter = self.displSleep 

    def _pushed_down(self, event):
        """SenseHat Joystick DOWN event
        
        Rotate display by +90 degrees and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displRotation = 0 if self.displRotation >= 270 else self.displRotation + const.ROTATE_90 
            self.sleepCounter = self.displSleep 

    def _pushed_left(self, event):
        """SenseHat Joystick LEFT event
        
        Switch display mode by 1 mode and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displMode = 4 if self.displMode <= 1 else self.displMode - 1
            self.sleepCounter = self.displSleep 

    def _pushed_right(self, event):
        """SenseHat Joystick RIGHT event
        
        Switch display mode by 1 mode and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displMode = 1 if self.displMode >= 4 else self.displMode + 1
            self.sleepCounter = self.displSleep 

    def _pushed_middle(self, event):
        """SenseHat Joystick MIDDLE (down) event
        
        Turn off display and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displMode = const.DISPL_BLANK
            self.sleepCounter = 1 

    def get_feed_info(self, feedKwd, default=""):
        """Get Adafruit IO feed info

        Args:
            feedKwd:
                'str' with feed keyword to find in config/settings
        """
        feed = get_setting(self.config, feedKwd, default)
        try:
            info = self.aio.feeds(feed)

        except RequestError as e:
            self.logger.log(logging.ERROR, f"Failed to get feed info - ADAFRUIT REQUEST ERROR: {e}")
            raise
        
        return info

    def get_sensor_data(self):
        """
        Read sensor data and round values to 1 decimal place

        Returns:
            'tuple' with 1 data point from each of the SenseHat sensors
        """
        tempC = round(self.sense.get_temperature(), 1)     # Temperature in C
        press = round(self.sense.get_pressure(), 1)        # Presure in hPa
        humid = round(self.sense.get_humidity(), 1)        # Humidity 

        return tempC, press, humid 

    def log(self, lvl, msg):
        """Wrapper of Logger.log()"""
        self.logger.log(lvl, msg)

    def blank_LED(self):
        """Show clear/blank LED"""
        self.sense.clear()
        
    def reset_LED(self):
        """Reset and clear LED"""
        self.sense.clear()
        self.sense.low_light = False

    def sparkle_LED(self):
        """Show random sparkles on LED"""
        x = randint(0, 7)
        y = randint(0, 7)
        r = randint(0, 255)
        g = randint(0, 255)
        b = randint(0, 255)

        toggle = randint(0, 3)

        if toggle != 0:
            self.sense.set_pixel(x, y, r, g, b)
        else:    
            self.sense.clear()

    def update_LED(self, data, inMin, inMax):
        """
        Update all pixels on SenseHat 8x8 LED with new color values

        Args:
            data:
                'list' with one value for each column of pixels on LED
            inMin:
                Min value of range for (sensor) data
            inMax:
                Max value of range for (sensor) data
        """
        normalized = [round(num_to_range(val, inMin, inMax, 0, const.LED_MAX_ROW)) for val in data]
        maxCol = min(const.LED_MAX_COL, len(normalized))

        pixels = [const.RGB_BLACK if row < (const.LED_MAX_ROW - normalized[col]) else convert_to_rgb(data[col], inMin, inMax, COLORS) for row in range(const.LED_MAX_ROW) for col in range(maxCol)]
        # self.sense.set_rotation(self.displRotation)
        self.sense.set_pixels(pixels)
    
    def update_LED_progress(self, inVal, maxVal=100):
        """Update progressbar on bottom row of LED

        Args:
            inVal:
                Value to represent on progressbar
            maxVal:
                Max value so we can calculate percentage
        """
        # Convert value to percentange and map against num pixels in a row
        normalized = int(num_to_range(inVal / maxVal, 0.0, 1.0, 0.0, float(const.LED_MAX_COL)))
        
        # Update LED bottom row
        for x in range(0, normalized):
            self.sense.set_pixel(x, const.LED_MAX_ROW - 1, const.RGB_PROGRESS)

    async def send_sensor_data(self, data):
        """Send sensor data to Adafruit IO

        Args:
            data:
                'dict' with feed key and data point

        Raises:
            RequestError:
                When API request fails
            ThrottlingError:
                When exceeding Adafruit IO rate limit
        """
        global logger

        try:
            self.aio.send_data(data["feed"].key, data["data"])
        except RequestError as e:
            self.logger.log(logging.ERROR, f"Upload failed for {data['feed'].key} - REQUEST ERROR: {e}")
            raise RequestError
        except ThrottlingError as e:
            self.logger.log(logging.ERROR, f"Upload failed for {data['feed'].key} - THROTTLING ERROR: {e}")
            raise ThrottlingError


EPSILON = sys.float_info.epsilon    # Smallest possible difference.
EXIT_NOW = False                    # Global flag for immediate (graceful) exit

#         - 0    1    2    3    4    5    6    7 -
EMPTY_Q = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
COLORS  = [const.RGB_BLUE, const.RGB_GREEN, const.RGB_YELLOW, const.RGB_RED]

LOGLVL = "ERROR"
LOGFILE = "f451-piF451.log"
LOGNAME = "f451-piF451"


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def exit_now(self, *args):
    """Changes global 'EXIT_NOW' flag.
    
    This function is called/triggered by signals (e.g. SIGINT, SIGTERM, etc.)
    and allows us run some clean-up tasks before shutting down.
    
    NOTE: It's not possible to catch SIGKILL
    
    Based on code from: https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully/31464349#31464349
    """
    global EXIT_NOW
    EXIT_NOW = True


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


def get_setting(settings, key, default=None):
    """Get a config value from settings
    
    This function will use the value from settings (TOML), but 
    can use a default value if settings value is not provided.

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


async def send_all_sensor_data(client, tempsData, pressData, humidData):
    """
    Send sensor data to Adafruit IO

    Args:
        client:
            We need full app context client
        tempsData:
            'dict' with 'temperature feed' key and temperature data point
        pressData:
            'dict' with 'pressure feed' key and pressure data point
        humidData:
            'dict' with 'humidity feed' key and humidity data point

    Raises:
        RequestError:
            When API request fails
        ThrottlingError:
            When exceeding Adafruit IO rate limit
    """
    await asyncio.gather(
        client.send_sensor_data(tempsData),
        client.send_sensor_data(pressData),
        client.send_sensor_data(humidData)
    )


# =========================================================
#      M A I N   F U N C T I O N    /   A C T I O N S
# =========================================================
if __name__ == '__main__':
    # Init signals
    signal.signal(signal.SIGINT, exit_now)
    signal.signal(signal.SIGTERM, exit_now)

    # Get app dir
    appDir = Path(__file__).parent

    # Initialize TOML parser and load 'settings.toml' file
    try:
        with open(appDir.joinpath("settings.toml"), mode="rb") as fp:
            config = tomllib.load(fp)
    except tomllib.TOMLDecodeError:
        sys.exit("Invalid 'settings.toml' file")      

    # Get core settings
    ioUser = get_setting(config, const.KWD_AIO_USER, "")
    ioKey = get_setting(config, const.KWD_AIO_KEY, "")
    ioDelay = get_setting(config, const.KWD_DELAY, const.DEF_DELAY)
    ioWait = get_setting(config, const.KWD_WAIT, const.DEF_WAIT)
    ioThrottle = get_setting(config, const.KWD_THROTTLE, const.DEF_THROTTLE)
    
    # Initialize app context instance
    piF451 = Device(
        SenseHat(), 
        Client(ioUser, ioKey), 
        logging.getLogger("f451-piF451"),
        config
    )

    piF451.displRotation = get_setting(config, const.KWD_ROTATION, const.DEF_ROTATION)
    piF451.displMode = get_setting(config, const.KWD_DISPLAY, const.DISPL_SPARKLE)
    piF451.displProgress = convert_to_bool(get_setting(config, const.KWD_PROGRESS, const.STATUS_ON))
    piF451.displSleep = get_setting(config, const.KWD_SLEEP, const.DEF_SLEEP)

    # Initialize logger
    logFile = get_setting(config, const.KWD_LOG_FILE)
    logFileFP = appDir.parent.joinpath(logFile) if logFile else None

    piF451.init_logger(
        get_setting(config, const.KWD_LOG_LEVEL, const.LOG_INFO),
        logFileFP
    )

    # Initialize core data queues
    tempsQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Temperature queue
    pressQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Pressure queue
    humidQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Humidity queue

    # Initialize SenseHat and Adafruit IO clients
    piF451.init_SenseHat()

    try:
        tempsFeed = piF451.get_feed_info(const.KWD_FEED_TEMPS)
        pressFeed = piF451.get_feed_info(const.KWD_FEED_PRESS)
        humidFeed = piF451.get_feed_info(const.KWD_FEED_HUMID)

    except RequestError as e:
        piF451.log(logging.ERROR, (f"Application terminated due to REQUEST ERROR: {e}"))
        piF451.reset_LED()
        sys.exit(1)

    # -- Main application loop --
    delayCounter = maxDelay = ioDelay       # Ensure that we upload first reading
    piF451.sleepCounter = piF451.displSleep   # Reset counter for screen blanking
    piF451.log(logging.INFO, "-- START Data Logging --")

    while not EXIT_NOW:
        # We check the sensors each time we loop through ...
        tempC, press, humid = piF451.get_sensor_data()

        # ... and add the data to the queues
        tempsQ.append(tempC)
        pressQ.append(press)
        humidQ.append(humid)

        # Check 'sleepCounter' before we display anything
        if piF451.sleepCounter == 1:
            piF451.blank_LED()       # Need to blank screen once
        elif piF451.sleepCounter > 1:
            if piF451.displMode == const.DISPL_TEMP:
                piF451.update_LED(tempsQ, const.MIN_TEMP, const.MAX_TEMP)
            elif piF451.displMode == const.DISPL_PRESS:    
                piF451.update_LED(pressQ, const.MIN_PRESS, const.MAX_PRESS)
            elif piF451.displMode == const.DISPL_HUMID:    
                piF451.update_LED(humidQ, const.MIN_HUMID, const.MAX_HUMID)
            elif piF451.displMode == const.DISPL_SPARKLE:    
                piF451.sparkle_LED()
            else:    
                piF451.blank_LED()

            if piF451.displProgress:
                piF451.update_LED_progress(delayCounter, maxDelay)    

        # Update sleep counter for screen blanking as needed
        if piF451.sleepCounter > 0:    
            piF451.sleepCounter -= 1

        # Is it time to upload data?
        if delayCounter < maxDelay:
            delayCounter += 1       # We send data at set intervals
        else:
            try:
                asyncio.run(send_all_sensor_data(
                    piF451,
                    {"data": tempC, "feed": tempsFeed},
                    {"data": press, "feed": pressFeed},
                    {"data": humid, "feed": humidFeed},
                ))

            except RequestError as e:
                piF451.log(logging.ERROR, f"Application terminated due to REQUEST ERROR: {e}")
                raise

            except ThrottlingError as e:
                # Keep increasing 'maxDelay' each time we get a 'ThrottlingError'
                maxDelay += ioThrottle
                
            else:
                # Reset 'maxDelay' back to normal 'ioDelay' on successful upload
                maxDelay = ioDelay
                piF451.log(logging.INFO, f"Uploaded: TEMP: {tempC} - PRESS: {press} - HUMID: {humid}")

            finally:
                # Reset counter even on failure
                delayCounter = 1

        # Let's rest a bit before we go through the loop again
        time.sleep(ioWait)

    # A bit of clean-up before we exit
    piF451.log(logging.INFO, "-- END Data Logging --")
    piF451.reset_LED()
