"""f451 Labs piF451 Device Class.

The piF451 Device class includes support for hardware extensions (e.g. Sense Hat, etc.),
core services (e.g. Adafruit IO, etc.), and uitilities (e.g. logger, etc.).

The class wraps -- and extends as needed -- the methods and functions supported by 
underlying libraries, and also keeps track of core counters, flags, etc.
"""

import logging

from random import randint

from Adafruit_IO import Client, MQTTClient, RequestError, ThrottlingError

import constants as const
from common import convert_to_bool, convert_to_rgb, get_setting, num_to_range

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

try:
    from sense_hat import SenseHat, ACTION_PRESSED, ACTION_HELD, ACTION_RELEASED
except ImportError:
    from mocks.fake_device import FakeHat as SenseHat, ACTION_PRESSED, ACTION_HELD, ACTION_RELEASED


# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
class Device:
    def __init__(self, config, appDir):
        """Initialize SenseHat hardware and logger

        Args:
            config:
                Config values from 'settings.toml'
            appDir:
                Path object for app parent folder
        """
        self.config = config
        self.aio = Client(                                  # Adafruit Client
            get_setting(config, const.KWD_AIO_USER, ""), 
            get_setting(config, const.KWD_AIO_KEY, "")
        )
        self.logger = self._init_logger(config, appDir)     # Logger
        self.sense = self._init_SenseHat(config)            # SenseHat

        self.displRotation = get_setting(config, const.KWD_ROTATION, const.DEF_ROTATION)
        self.displMode = get_setting(config, const.KWD_DISPLAY, const.DISPL_SPARKLE)
        self.displProgress = convert_to_bool(get_setting(config, const.KWD_PROGRESS, const.STATUS_ON))
        self.displSleep = get_setting(config, const.KWD_SLEEP, const.DEF_SLEEP)

    def _init_logger(self, config, appDir):
        """Initialize Logger

        We always initialize the logger with a stream 
        handler. But file handler is only created if 
        a file name has been provided in settings.
        """
        logger = logging.getLogger("f451-piRED")
        logFile = get_setting(config, const.KWD_LOG_FILE)
        logFileFP = appDir.parent.joinpath(logFile) if logFile else None
        logLvl = get_setting(config, const.KWD_LOG_LEVEL, const.LOG_INFO)

        logger.setLevel(logLvl)

        if logFileFP:
            fileHandler = logging.FileHandler(logFileFP)
            fileHandler.setLevel(logLvl)
            fileHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
            logger.addHandler(fileHandler)

        streamHandler = logging.StreamHandler()
        streamHandler.setLevel(logLvl if logLvl == const.LOG_DEBUG else logging.ERROR)
        streamHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
        logger.addHandler(streamHandler)

        return logger

    def _init_SenseHat(self, config):
        """Initialize SenseHat

        Initialize the SenseHat device, set some 
        default parameters, and clear LED.
        """
        sense = SenseHat()
        
        sense.set_imu_config(False, False, False)   # Disable IMU functions
        sense.low_light = True
        sense.clear()                               # Clear 8x8 LED
        sense.set_rotation(get_setting(config, const.KWD_ROTATION, const.DEF_ROTATION)) # Set initial rotation

        sense.stick.direction_up = self._pushed_up
        sense.stick.direction_down = self._pushed_down
        sense.stick.direction_left = self._pushed_left
        sense.stick.direction_right = self._pushed_right
        sense.stick.direction_middle = self._pushed_middle

        return sense

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
            self.displMode = const.MAX_DISPL if self.displMode <= const.MIN_DISPL else self.displMode - 1
            self.sleepCounter = self.displSleep 

    def _pushed_right(self, event):
        """SenseHat Joystick RIGHT event
        
        Switch display mode by 1 mode and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displMode = const.MIN_DISPL if self.displMode >= const.MAX_DISPL else self.displMode + 1
            self.sleepCounter = self.displSleep 

    def _pushed_middle(self, event):
        """SenseHat Joystick MIDDLE (down) event
        
        Turn off display and reset screen blanking
        """
        if event.action != ACTION_RELEASED:
            self.displMode = const.DISPL_BLANK
            self.sleepCounter = 1 

    def get_config(self, key, default=None):
        """Get a config value from settings
        
        This method rerieves value from settings (TOML), but can
        return a default value if key does not exist (i.e. settings 
        value has not been defined in TOML file.

        Args:
            key:
                'str' with name of settings key
            defaul:
                Default value

        Returns:
            Settings value        
        """
        return self.config[key] if key in self.config else default

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

    def log_error(self, msg):
            """Wrapper of Logger.error()"""
            self.logger.error(msg)

    def log_info(self, msg):
            """Wrapper of Logger.info()"""
            self.logger.info(msg)

    def log_debug(self, msg):
            """Wrapper of Logger.debug()"""
            self.logger.debug(msg)

    def display_blank(self):
        """Show clear/blank LED"""
        self.sense.clear()
        
    def display_reset(self):
        """Reset and clear LED"""
        self.sense.clear()
        self.sense.low_light = False

    def display_sparkle(self):
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

    def display_update(self, data, inMin, inMax):
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
    
    def display_progress(self, inVal, maxVal=100):
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
