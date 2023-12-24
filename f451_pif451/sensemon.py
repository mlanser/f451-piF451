#!/usr/bin/env python3
"""f451 Labs SenseMon application on piRED & piF451 devices.

This application is designed for the f451 Labs piRED and piF451 devices which are both 
equipped with Sense HAT add-ons. The main objective is to continously read environment 
data (e.g. temperature, barometric pressure, and humidity) from the Sense HAT sensors 
and then upload the data to the Adafruit IO service.

To launch this application from terminal:

    $ nohup python -u sensemon.py > sensemon.out &

This command launches the 'sensemon' application in the background. The application will 
keep running even after the terminal window is closed. Any output will be redirected to 
the 'sensemon.out' file.    

It's also possible to install this application via 'pip' from Github and one 
can launch the application as follows:

    $ nohup sensemon > sensemon.out &

NOTE: Parts of this code is based on ideas found in the 'luftdaten_combined.py' example 
      from the Enviro+ Python example files. Main modifications include support for 
      Adafruit.io, using Python 'deque' to manage data queues, moving device support 
      to a separate class, etc.

      Furthermore, this application is designed to get sensor data from the Raspberry 
      Pi Sense HAT which has fewer sensors than the Enviro+, an 8x8 LED, and a joystick.
      
      We also support additional display modes including a screen-saver mode, support 
      for 'settings.toml', and more. And finally, this app also has support for a 
      terminal UI (using the Rich library) with live data updates, sparklines graphs,
      and more.

Dependencies:
    - adafruit-io - only install if you have an account with Adafruit IO

TODO:
    - add support for custom colors in 'settings.toml'
    - add support for custom range factor in 'settings.toml'
"""

import time
import sys
import asyncio
import platform

from collections import deque, namedtuple
from datetime import datetime
from pathlib import Path

from . import constants as const

import f451_common.cli_ui as f451CLIUI
import f451_common.common as f451Common
import f451_common.logger as f451Logger
import f451_common.cloud as f451Cloud

import f451_sensehat.sensehat as f451SenseHat
import f451_sensehat.sensehat_data as f451SenseData

from rich.console import Console
from rich.live import Live

from Adafruit_IO import RequestError, ThrottlingError

# Install Rich 'traceback' and 'pprint' to
# make (debug) life is easier. Trust me!
from rich.pretty import pprint
from rich.traceback import install as install_rich_traceback

install_rich_traceback(show_locals=True)


# fmt: off
# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
APP_VERSION = '0.5.2'
APP_NAME = 'f451 Labs - SenseMon'
APP_NAME_SHORT = 'SenseMon'
APP_LOG = 'f451-sensemon.log'       # Individual logs for devices with multiple apps
APP_SETTINGS = 'settings.toml'      # Standard for all f451 Labs projects

APP_MIN_SENSOR_READ_WAIT = 1        # Min wait in sec between sensor reads
APP_MIN_PROG_WAIT = 1               # Remaining min (loop) wait time to display prog bar
APP_WAIT_1SEC = 1
APP_MAX_DATA = 120                  # Max number of data points in the queue
APP_DELTA_FACTOR = 0.02             # Any change within X% is considered negligable

APP_DATA_TYPES = [
    const.KWD_DATA_TEMPS,           # 'temperature' in C
    const.KWD_DATA_PRESS,           # barometric 'pressure'
    const.KWD_DATA_HUMID            # 'humidity'
]

APP_DISPLAY_MODES = {
    f451SenseHat.KWD_DISPLAY_MIN: const.MIN_DISPL,
    f451SenseHat.KWD_DISPLAY_MAX: const.MAX_DISPL,
}

class AppRT(f451Common.Runtime):
    """Application runtime object.
    
    We use this object to store/manage configuration and any other variables
    required to run this application as object atrtribustes. This allows us to
    have fewer global variables.
    """
    def __init__(self, appName, appVersion, appNameShort=None, appLog=None, appSettings=None):
        super().__init__(
            appName, 
            appVersion, 
            appNameShort, 
            appLog, 
            appSettings,
            platform.node(),        # Get device 'hostname'
            Path(__file__).parent   # Find dir for this app
        )
        
    def init_runtime(self, cliArgs, data):
        """Initialize the 'runtime' variable
        
        We use an object to hold all core runtime values, flags, etc. 
        This makes it easier to send global values around the app as
        a single entitye rather than having to manage a series of 
        individual (global) values.

        Args:
            cliArgs: holds user-supplied values from ArgParse
            data: general data set (used to create CLI UI table rows, etc.)
        """
        # Load settings and initialize logger
        self.config = f451Common.load_settings(self.appDir.joinpath(self.appSettings))
        self.logger = f451Logger.Logger(self.config, LOGFILE=self.appLog)

        self.ioFreq = self.config.get(const.KWD_FREQ, const.DEF_FREQ)
        self.ioDelay = self.config.get(const.KWD_DELAY, const.DEF_DELAY)
        self.ioWait = max(self.config.get(const.KWD_WAIT, const.DEF_WAIT), APP_MIN_SENSOR_READ_WAIT)
        self.ioThrottle = self.config.get(const.KWD_THROTTLE, const.DEF_THROTTLE)
        self.ioRounding = self.config.get(const.KWD_ROUNDING, const.DEF_ROUNDING)
        self.ioUploadAndExit = False

        # Update log file or level?
        if cliArgs.debug:
            self.logLvl = f451Logger.LOG_DEBUG
            self.debugMode = True
        else:
            self.logLvl = self.config.get(f451Logger.KWD_LOG_LEVEL, f451Logger.LOG_NOTSET)
            self.debugMode = (self.logLvl == f451Logger.LOG_DEBUG)

        self.logger.set_log_level(self.logLvl)

        if cliArgs.log is not None:
            self.logger.set_log_file(appRT.logLvl, cliArgs.log)

        # Initialize various counters, etc.
        self.timeSinceUpdate = float(0)
        self.timeUpdate = time.time()
        self.displayUpdate = self.timeUpdate
        self.uploadDelay = self.ioDelay
        self.maxUploads = int(cliArgs.uploads)
        self.numUploads = 0

        self.tempCompFactor = self.config.get(f451Common.KWD_TEMP_COMP, f451Common.DEF_TEMP_COMP_FACTOR)
        self.cpuTempsQMaxLen = self.config.get(f451Common.KWD_MAX_LEN_CPU_TEMPS, f451Common.MAX_LEN_CPU_TEMPS)

        # If comp factor is 0 (zero), then do NOT compensate for CPU temp
        self.tempCompYN = self.tempCompFactor > 0

        # Initialize UI for terminal
        if cliArgs.noCLI:
            self.console = Console() # type: ignore
        else:
            UI = f451CLIUI.BaseUI()
            UI.initialize(
                self.appName,
                self.appNameShort,
                self.appVersion,
                prep_data_for_screen(data.as_dict(), True),
                not cliArgs.noCLI,
            )
            self.console = UI # type: ignore

    def debug(self, cli=None, data=None):
        """Print/log some basic debug info.
        
        Args:
            cli: CLI args
            data: app data
        """

        self.console.rule('Config Settings', style='grey', align='center')

        self.logger.log_debug(f"DISPL ROT:   {self.sensors['SenseHat'].displRotation}")
        self.logger.log_debug(f"DISPL MODE:  {self.sensors['SenseHat'].displMode}")
        self.logger.log_debug(f"DISPL PROGR: {self.sensors['SenseHat'].displProgress}")
        self.logger.log_debug(f"SLEEP TIME:  {self.sensors['SenseHat'].displSleepTime}")
        self.logger.log_debug(f"SLEEP MODE:  {self.sensors['SenseHat'].displSleepMode}")

        self.logger.log_debug(f'IO DEL:      {self.ioDelay}')
        self.logger.log_debug(f'IO WAIT:     {self.ioWait}')
        self.logger.log_debug(f'IO THROTTLE: {self.ioThrottle}')

        # Display Raspberry Pi serial and Wi-Fi status
        self.logger.log_debug(
            f'Raspberry Pi serial: {f451Common.get_RPI_serial_num()}'
        )
        self.logger.log_debug(
            f'Wi-Fi: {(f451Common.STATUS_YES if f451Common.check_wifi() else f451Common.STATUS_UNKNOWN)}'
        )

        # List CLI args
        if cli:
            for key, val in vars(cli).items():
                self.logger.log_debug(f"CLI Arg '{key}': {val}")

        # List config settings
        self.console.rule('CONFIG', style='grey', align='center')  # type: ignore
        pprint(self.config, expand_all=True)

        if data:
            self.console.rule('APP DATA', style='grey', align='center')  # type: ignore
            pprint(data.as_dict(), expand_all=True)

        # Display nice border below everything
        self.console.rule(style='grey', align='center')  # type: ignore

    def show_summary(self, cli=None, data=None):
        """Display summary info
        
        We (usually) call this method to display summary info
        at the before we exit the application.

        Args:
            cli: CLI args
            data: app data
        """
        print()
        self.console.rule(f'{self.appName} (v{self.appVersion})', style='grey', align='center')  # type: ignore
        print(f'Work start:  {self.workStart:%a %b %-d, %Y at %-I:%M:%S %p}')
        print(f'Work end:    {(datetime.now()):%a %b %-d, %Y at %-I:%M:%S %p}')
        print(f'Num uploads: {self.numUploads}')

        # Show config info, etc. if in 'debug' mode
        if self.debugMode:
            self.debug(cli, data)

    def add_sensor(self, sensorName, sensorType, **kwargs):
        self.sensors[sensorName] = sensorType(self.config, **kwargs)
        return self.sensors[sensorName]

    def add_feed(self, feedName, feedService, feedKey):
        service = feedService(self.config)
        feed = service.feed_info(feedKey)

        self.feeds[feedName] = f451Cloud.AdafruitFeed(service, feed)

        return self.feeds[feedName]

    def update_action(self, cliUI, msg=None):
        """Wrapper to help streamline code"""
        if cliUI:
            self.console.update_action(msg) # type: ignore

    def update_progress(self, cliUI, prog=None, msg=None):
        """Wrapper to help streamline code"""
        if cliUI:
            self.console.update_progress(prog, msg) # type: ignore        

    def update_upload_status(self, cliUI, lastTime, lastStatus, nextTime, numUploads, maxUploads=0):
        """Wrapper to help streamline code"""
        if cliUI:
            self.console.update_upload_status(lastTime, lastStatus, nextTime, numUploads, maxUploads) # type: ignore

    def update_data(self, cliUI, data):
        """Wrapper to help streamline code"""
        if cliUI:
            self.console.update_data(data) # type: ignore

# Define app runtime object and basic data unit
appRT = AppRT(APP_NAME, APP_VERSION, APP_NAME_SHORT, APP_LOG, APP_SETTINGS)
DataUnit = namedtuple("DataUnit", APP_DATA_TYPES)
# fmt: on


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def prep_data_for_sensehat(inData, lenSlice=0):
    """Prep data for Sense HAT

    This function will filter data to ensure we don't have incorrect
    outliers (e.g. from faulty sensors, etc.). The final data set will
    have only valid values. Any invalid values will be replaced with
    0's so that we can display the set on the Sense HAT LED.

    This will technically affect the min/max values for the set. However,
    we're displaying this data on an 8x8 LED. So visual 'accuracy' is
    already less than ideal ;-)

    NOTE: the data structure is more complex than we need for Sense HAT
    devices. But we want to maintain a basic level of compatibility with
    other f451 Labs modules.

    Args:
        inData: 'DataUnit' named tuple with 'raw' data from sensors
        lenSlice: (optional) length of data slice

    Returns:
        'DataUnit' named tuple with the following fields:
            data   = [list of values],
            valid  = <tuple with min/max>,
            unit   = <unit string>,
            label  = <label string>,
            limits = [list of limits]
    """
    # Size of data slice we want to send to Sense HAT. The 'f451 Labs SenseHat'
    # library will ulimately only display the last 8 values anyway.
    dataSlice = list(inData.data)[-lenSlice:]

    # Return filtered data
    dataClean = [i if f451Common.is_valid(i, inData.valid) else 0 for i in dataSlice]

    return f451SenseData.DataUnit(
        data=dataClean,
        valid=inData.valid,
        unit=inData.unit,
        label=inData.label,
        limits=inData.limits,
    )


def prep_data_for_screen(inData, labelsOnly=False, conWidth=f451CLIUI.APP_2COL_MIN_WIDTH):
    """Prep data for display in terminal

    We display a table in the terminal with a row for each data type. On
    each row, we the display label, last value (with unit), and a sparkline
    graph.

    This function will filter data to ensure we don't have incorrect
    outliers (e.g. from faulty sensors, etc.). The final data set will
    have only valid values. Any invalid values will be replaced with
    0's so that we can display the set as a sparkline graph.

    This will technically affect the min/max values for the set. However,
    we're displaying this data in a table cells that will have about
    40 columns, and each column is made up of block characters which
    can only show 8 different heights. So visual 'accuracy' is
    already less than ideal ;-)

    NOTE: We need to map the data sets agains a numeric range of 1-8 so
          that we can display them as sparkline graphs in the terminal.

    NOTE: We're using the 'limits' list to color the values, which means
          we need to create a special 'coloring' set for the sparkline
          graphs using converted limit values.

          The limits list has 4 values (see also 'SenseData' class) and
          we need to map them to colors:

          Limit set [A, B, C, D] means:

                     val <= A -> Dangerously Low    = "bright_red"
                B >= val >  A -> Low                = "bright_yellow"
                C >= val >  B -> Normal             = "green"
                D >= val >  C -> High               = "cyan"
                     val >  D -> Dangerously High   = "blue"

          Note that the Sparkline library has a specific syntax for
          limits and colors:

            "<name of color>:<gt|eq|lt>:<value>"

          Also, we only care about 'low', 'normal', and 'high'

    Args:
        inData: 'dict' with Sense HAT data

    Returns:
        'list' with processed data and only with data rows (i.e. temp,
        humidity, pressure) and columns (i.e. label, last data pt, and
        sparkline) that we want to display. Each row in the list is
        designed for display in the terminal.
    """
    outData = []

    def _sparkline_colors(limits, customColors=None):
        """Create color mapping for sparkline graphs

        This function creates the 'color' list which allows
        the 'sparklines' library to add add correct ANSI
        color codes to the graph.

        Args:
            limits: list with limits -- see SenseHat module for details
            customColors: (optional) custom color map

        Return:
            'list' with definitions for 'emph' param of 'sparklines' method
        """
        # fmt: off
        colors = None

        if all(limits):
            colorMap = f451Common.get_tri_colors(customColors)

            colors = [
                f'{colorMap.high}:gt:{round(limits[2], 1)}',    # High   # type: ignore
                f'{colorMap.normal}:eq:{round(limits[2], 1)}',  # Normal # type: ignore
                f'{colorMap.normal}:lt:{round(limits[2], 1)}',  # Normal # type: ignore
                f'{colorMap.low}:eq:{round(limits[1], 1)}',     # Low    # type: ignore
                f'{colorMap.low}:lt:{round(limits[1], 1)}',     # Low    # type: ignore
            ]

        return colors
        # fmt: on

    def _dataPt_color(val, limits, default='', customColors=None):
        """Determine color mapping for specific value

        Args:
            val: value to check
            limits: list with limits -- see SenseHat module for details
            default: (optional) default color name string
            customColors: (optional) custom color map

        Return:
            'list' with definitions for 'emph' param of 'sparklines' method
        """
        color = default
        colorMap = f451Common.get_tri_colors(customColors)

        if val is not None and all(limits):
            if val > round(limits[2], 1):
                color = colorMap.high
            elif val <= round(limits[1], 1):
                color = colorMap.low
            else:
                color = colorMap.normal

        return color

    # Process each data row and create a new data structure that we can use
    # for displaying all necessary data in the terminal.
    for key, row in inData.items():
        if key in APP_DATA_TYPES:
            # Create new crispy clean set :-)
            dataSet = {
                'sparkData': [],
                'sparkColors': None,
                'sparkMinMax': (None, None),
                'dataPt': None,
                'dataPtOK': True,
                'dataPtDelta': 0,
                'dataPtColor': '',
                'unit': row['unit'],
                'label': row['label'],
            }

            # If we only need labels, then we'll skip to
            # next iteration of the loop
            if labelsOnly:
                outData.append(dataSet)
                continue

            # Data slice we can display in table row
            graphWidth = min(int(conWidth / 2), 40)
            dataSlice = list(row['data'])[-graphWidth:]

            # Get filtered data to calculate min/max. Note that 'valid' data
            # will have only valid values. Any invalid values would have been
            # replaced with 'None' values. We can display this set using the
            # 'sparklines' library. We continue refining the data by removing
            # all 'None' values to get a 'clean' set, which we can use to
            # establish min/max values for the set.
            dataValid = [i if f451Common.is_valid(i, row['valid']) else None for i in dataSlice]
            dataClean = [i for i in dataValid if i is not None]

            # We set 'OK' flag to 'True' if current data point is valid or
            # missing (i.e. None).
            dataPt = dataSlice[-1] if f451Common.is_valid(dataSlice[-1], row['valid']) else None
            dataPtOK = dataPt or dataSlice[-1] is None

            # We determine up/down/sideways trend by looking at delate between
            # current value and previous value. If current and/or previous value
            # is 'None' for whatever reason, then we assume 'sideways' (0)trend.
            dataPtPrev = (
                dataSlice[-2] if f451Common.is_valid(dataSlice[-2], row['valid']) else None
            )
            dataPtDelta = f451Common.get_delta_range(dataPt, dataPtPrev, APP_DELTA_FACTOR)

            # Update data set
            dataSet['sparkData'] = dataValid
            dataSet['sparkColors'] = _sparkline_colors(row['limits'])
            dataSet['sparkMinMax'] = (
                (min(dataClean), max(dataClean)) if any(dataClean) else (None, None)
            )

            dataSet['dataPt'] = dataPt
            dataSet['dataPtOK'] = dataPtOK
            dataSet['dataPtDelta'] = dataPtDelta
            dataSet['dataPtColor'] = _dataPt_color(dataPt, row['limits'])

            outData.append(dataSet)

    return outData


async def upload_sensor_data(app, *args, **kwargs):
    """Send sensor data to cloud services.

    This helper function parses and sends enviro data to
    Adafruit IO and/or Arduino Cloud.

    NOTE: This function will upload specific environment
          data using the following keywords:

          'temperature' - temperature data
          'pressure'    - barometric pressure
          'humidity'    - humidity

    Args:
        app:    app: hook to app runtime object
        args:   user can provide single 'dict' with data
        kwargs: user can provide individual data points as key-value pairs
    """
    # We combine 'args' and 'kwargs' to allow users to provide a 'dict' with
    # all data points and/or individual data points (which could override
    # values in the 'dict').
    data = {**args[0], **kwargs} if args and isinstance(args[0], dict) else kwargs

    sendQ = []

    # Send temperature data?
    if data.get(const.KWD_DATA_TEMPS) is not None:
        sendQ.append(app.feeds[const.KWD_DATA_TEMPS].send_data(data.get(const.KWD_DATA_TEMPS)))  # type: ignore

    # Send barometric pressure data?
    if data.get(const.KWD_DATA_PRESS) is not None:
        sendQ.append(app.feeds[const.KWD_DATA_PRESS].send_data(data.get(const.KWD_DATA_PRESS)))  # type: ignore

    # Send humidity data?
    if data.get(const.KWD_DATA_HUMID) is not None:
        sendQ.append(app.feeds[const.KWD_DATA_HUMID].send_data(data.get(const.KWD_DATA_HUMID)))  # type: ignore

    # deviceID = SENSE_HAT.get_ID(DEF_ID_PREFIX)

    await asyncio.gather(*sendQ)


def btn_up(event):
    """SenseHat Joystick UP event

    Rotate display by -90 degrees and reset screen blanking
    """
    global appRT

    if event.action != f451SenseHat.BTN_RELEASE:
        appRT.sensors['SenseHat'].display_rotate(-1)
        appRT.displayUpdate = time.time()


def btn_down(event):
    """SenseHat Joystick DOWN event

    Rotate display by +90 degrees and reset screen blanking
    """
    global appRT

    if event.action != f451SenseHat.BTN_RELEASE:
        appRT.sensors['SenseHat'].display_rotate(1)
        appRT.displayUpdate = time.time()


def btn_left(event):
    """SenseHat Joystick LEFT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global appRT

    if event.action != f451SenseHat.BTN_RELEASE:
        appRT.sensors['SenseHat'].update_display_mode(-1)
        appRT.displayUpdate = time.time()


def btn_right(event):
    """SenseHat Joystick RIGHT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global appRT

    if event.action != f451SenseHat.BTN_RELEASE:
        appRT.sensors['SenseHat'].update_display_mode(1)
        appRT.displayUpdate = time.time()


def btn_middle(event):
    """SenseHat Joystick MIDDLE (down) event

    Turn display on/off
    """
    global appRT

    if event.action != f451SenseHat.BTN_RELEASE:
        # Wake up?
        if appRT.sensors['SenseHat'].displSleepMode:
            appRT.sensors['SenseHat'].update_sleep_mode(False)
            appRT.displayUpdate = time.time()
        else:
            appRT.sensors['SenseHat'].update_sleep_mode(True)


APP_JOYSTICK_ACTIONS = {
    f451SenseHat.KWD_BTN_UP: btn_up,
    f451SenseHat.KWD_BTN_DWN: btn_down,
    f451SenseHat.KWD_BTN_LFT: btn_left,
    f451SenseHat.KWD_BTN_RHT: btn_right,
    f451SenseHat.KWD_BTN_MDL: btn_middle,
}


def update_SenseHat_LED(sense, data, colors=None):
    """Update Sense HAT LED display depending on display mode

    We check current display mode and then prep data as needed
    for display on LED.

    Args:
        sense: hook to SenseHat object
        data: full data set where we'll grab a slice from the end
        colors: (optional) custom color map
    """

    def _minMax(data):
        """Create min/max based on all collecxted data

        This will smooth out some hard edges that may occur
        when the data slice is to short.
        """
        scrubbed = [i for i in data if i is not None]
        return (min(scrubbed), max(scrubbed)) if scrubbed else (0, 0)

    def _get_color_map(data, colors=None):
        return f451Common.get_tri_colors(colors, True) if all(data.limits) else None

    # Check display mode. Each mode corresponds to a data type.
    # Show temperature?
    if sense.displMode == const.DISPL_TEMP:
        minMax = _minMax(data.temperature.as_tuple().data)
        dataClean = prep_data_for_sensehat(data.temperature.as_tuple())
        colorMap = _get_color_map(dataClean, colors)
        sense.display_as_graph(dataClean, minMax, colorMap)

    # Show pressure?
    elif sense.displMode == const.DISPL_PRESS:
        minMax = _minMax(data.pressure.as_tuple().data)
        dataClean = prep_data_for_sensehat(data.pressure.as_tuple())
        colorMap = _get_color_map(dataClean, colors)
        sense.display_as_graph(dataClean, minMax, colorMap)

    # Show humidity?
    elif sense.displMode == const.DISPL_HUMID:
        minMax = _minMax(data.humidity.as_tuple().data)
        dataClean = prep_data_for_sensehat(data.humidity.as_tuple())
        colorMap = _get_color_map(dataClean, colors)
        sense.display_as_graph(dataClean, minMax, colorMap)

    # Show sparkles? :-)
    else:
        sense.display_sparkle()


def init_cli_parser(appName, appVersion, setDefaults=True):
    """Initialize CLI (ArgParse) parser.

    Initialize the ArgParse parser with CLI 'arguments'
    and return new parser instance.

    Args:
        appName: 'str' with app name
        appVersion: 'str' with app version
        setDefaults: 'bool' flag indicates whether to set up default CLI args

    Returns:
        ArgParse parser instance
    """
    # fmt: off
    parser = f451Common.init_cli_parser(appName, appVersion, setDefaults)

    # Add app-specific CLI args
    parser.add_argument(
        '--noCLI',
        action='store_true',
        default=False,
        help='do not display output on CLI',
    )
    parser.add_argument(
        '--noLED',
        action='store_true',
        default=False,
        help='do not display output on LED',
    )
    parser.add_argument(
        '--progress',
        action='store_true',
        default=False,
        help='show upload progress bar on LED',
    )
    parser.add_argument(
        '--uploads',
        action='store',
        type=int,
        default=-1,
        help='number of uploads before exiting',
    )

    return parser
    # fmt: on


def hurry_up_and_wait(app, cliUI=False):
    """Display wait messages and progress bars

    This function comes into play if we have longer wait times
    between sensor reads, etc. For example, we may want to read
    temperature sensors every second. But we may want to wait a
    minute or more to run internet speed tests.

    Args:
        app: hook to app runtime object
        cliUI: 'bool' indicating whether user wants full UI
    """
    if app.ioWait > APP_MIN_PROG_WAIT:
        app.update_progress(cliUI, None, 'Waiting for sensors')
        for i in range(app.ioWait):
            app.update_progress(cliUI, int(i / app.ioWait * 100))
            time.sleep(APP_WAIT_1SEC)
        app.update_action(cliUI, None)
    else:
        time.sleep(app.ioWait)

    # Update Sense HAT prog bar as needed with time remaining
    # until next data upload
    app.sensors['SenseHat'].display_progress(app.timeSinceUpdate / app.uploadDelay)


def main_loop(app, data, cliUI=False):
    """Main application loop.

    This is where most of the action happens. We continously collect
    data from our sensors, process it, display it, and upload it at
    certain intervals.

    Args:
        app: application runtime object with config, counters, etc.
        data: main application data queue
        cliUI: 'bool' to indicate if we use full (console) UI
    """
    # Do we need to compensate for CPU temps? If so, initialize a
    # CPU temp queue so that we have data to calculate averages.
    cpuTempsQ = []
    if appRT.tempCompYN:
        cpuTempsQ = deque(
            [appRT.sensors['SenseHat'].get_CPU_temp(False)] * appRT.cpuTempsQMaxLen,
            maxlen=appRT.cpuTempsQMaxLen,
        )

    # Set 'exit' flag and start the loop!
    exitNow = False
    while not exitNow:
        try:
            # fmt: off
            timeCurrent = time.time()
            app.timeSinceUpdate = timeCurrent - app.timeUpdate
            app.sensors['SenseHat'].update_sleep_mode(
                (timeCurrent - app.displayUpdate) > app.sensors['SenseHat'].displSleepTime, # Time to sleep?
                # cliArgs.noLED,                                                            # Force no LED?
                app.sensors['SenseHat'].displSleepMode                                      # Already asleep?
            )

            # Update Sense HAT prog bar as needed
            app.sensors['SenseHat'].display_progress(app.timeSinceUpdate / app.uploadDelay)

            # --- Get magic data ---
            #
            app.update_action(cliUI, 'Reading sensors …')
            # Get raw temp from sensor
            tempRaw = tempComp = app.sensors['SenseHat'].get_temperature()

            # Do we need to compensate for CPU temp?
            if app.tempCompYN:
                # Get current CPU temp, add to queue, and calculate new average
                #
                # NOTE: This feature relies on the 'vcgencmd' which is found on
                #       RPIs. If this is not run on a RPI (e.g. during testing),
                #       then we need to neutralize the 'cpuTemp' compensation.
                cpuTempsQ.append(app.sensors['SenseHat'].get_CPU_temp(False))
                cpuTempAvg = sum(cpuTempsQ) / float(app.cpuTempsQMaxLen)

                # Smooth out with some averaging to decrease jitter
                tempComp = tempRaw - ((cpuTempAvg - tempRaw) / app.tempCompFactor)

            # Get barometric pressure and humidity data
            pressRaw = app.sensors['SenseHat'].get_pressure()
            humidRaw = app.sensors['SenseHat'].get_humidity()
            #
            # ----------------------
            # fmt: on

            # Is it time to upload data?
            if app.timeSinceUpdate >= app.uploadDelay:
                try:
                    asyncio.run(
                        upload_sensor_data(
                            app,
                            {
                                const.KWD_DATA_TEMPS: round(tempComp, app.ioRounding),
                                const.KWD_DATA_PRESS: round(pressRaw, app.ioRounding),
                                const.KWD_DATA_HUMID: round(humidRaw, app.ioRounding),
                            },
                            deviceID=f451Common.get_RPI_ID(f451Common.DEF_ID_PREFIX),
                        )
                    )

                except RequestError as e:
                    app.logger.log_error(f'Application terminated: {e}')
                    sys.exit(1)

                except ThrottlingError:
                    # Keep increasing 'ioDelay' each time we get a 'ThrottlingError'
                    app.uploadDelay += app.ioThrottle

                else:
                    # Reset 'uploadDelay' back to normal 'ioFreq' on successful upload
                    app.numUploads += 1
                    app.uploadDelay = app.ioFreq
                    exitNow = exitNow or app.ioUploadAndExit
                    app.logger.log_info(
                        f'Uploaded: TEMP: {round(tempComp, app.ioRounding)} - PRESS: {round(pressRaw, app.ioRounding)} - HUMID: {round(humidRaw, app.ioRounding)}'
                    )
                    app.update_upload_status(
                        cliUI,
                        timeCurrent,
                        f451CLIUI.STATUS_OK,
                        timeCurrent + app.uploadDelay,
                        app.numUploads,
                        app.maxUploads,
                    )
                finally:
                    app.timeUpdate = timeCurrent
                    exitNow = (app.maxUploads > 0) and (app.numUploads >= app.maxUploads)
                    app.update_action(cliUI, None)

            # Update data set and display to terminal as needed
            data.temperature.data.append(tempComp)
            data.pressure.data.append(pressRaw)
            data.humidity.data.append(humidRaw)

            update_SenseHat_LED(app.sensors['SenseHat'], data)
            app.update_data(cliUI, prep_data_for_screen(data.as_dict()))

            # Are we done? And do we have to wait a bit before next sensor read?
            if not exitNow:
                # If we're not done and there's a substantial wait before we can
                # read the sensors again (e.g. we only want to read sensors every
                # few minutes for whatever reason), then lets display and update
                # the progress bar as needed. Once the wait is done, we can go
                # through this whole loop all over again ... phew!
                hurry_up_and_wait(app, cliUI)

                # Update Sense HAT prog bar as needed
                app.sensors['SenseHat'].display_progress(app.timeSinceUpdate / app.uploadDelay)

        except KeyboardInterrupt:
            exitNow = True


# =========================================================
#      M A I N   F U N C T I O N    /   A C T I O N S
# =========================================================
def main(cliArgs=None):
    """Main function.

    This function will goes through the setup and then runs the
    main application loop.

    NOTE:
     -  Application will exit with error level 1 if invalid Adafruit IO
        or Arduino Cloud feeds are provided

     -  Application will exit with error level 0 if either no arguments
        are entered via CLI, or if arguments '-V' or '--version' are used.
        No data will be uploaded will be sent in that case.

    Args:
        cliArgs:
            CLI arguments used to start application
    """
    global appRT

    # Parse CLI args and show 'help' and exit if no args
    cli = init_cli_parser(APP_NAME, APP_VERSION, True)
    cliArgs, _ = cli.parse_known_args(cliArgs)
    if not cliArgs and len(sys.argv) == 1:
        cli.print_help(sys.stdout)
        sys.exit(0)

    if cliArgs.version:
        print(f'{APP_NAME} (v{APP_VERSION})')
        sys.exit(0)

    # Get core settings and initialize core data queue
    appData = f451SenseData.SenseData(None, APP_MAX_DATA)
    appRT.init_runtime(cliArgs, appData)

    # Verify that feeds exist and initialize them
    try:
        appRT.add_feed(
            const.KWD_DATA_TEMPS, 
            f451Cloud.AdafruitCloud, 
            appRT.config.get(const.KWD_FEED_TEMPS, None)
        )
        appRT.add_feed(
            const.KWD_DATA_PRESS, 
            f451Cloud.AdafruitCloud, 
            appRT.config.get(const.KWD_FEED_PRESS, None)
        )
        appRT.add_feed(
            const.KWD_DATA_HUMID, 
            f451Cloud.AdafruitCloud, 
            appRT.config.get(const.KWD_FEED_HUMID, None)
        )

    except RequestError as e:
        appRT.logger.log_error(f'Application terminated due to REQUEST ERROR: {e}')
        sys.exit(1)

    # Initialize device instance which includes all sensors
    # and LED display on Sense HAT. Also initialize joystick
    # events and set 'sleep' and 'display' modes.
    appRT.add_sensor('SenseHat', f451SenseHat.SenseHat)
    appRT.sensors['SenseHat'].joystick_init(**APP_JOYSTICK_ACTIONS)
    appRT.sensors['SenseHat'].display_init(**APP_DISPLAY_MODES)
    appRT.sensors['SenseHat'].update_sleep_mode(cliArgs.noLED)
    appRT.sensors['SenseHat'].displProgress = cliArgs.progress
    appRT.sensors['SenseHat'].display_message(APP_NAME)

    # -- Main application loop --
    appRT.logger.log_info('-- START Data Logging --')

    if cliArgs.noCLI:
        main_loop(appRT, appData)
    else:
        appRT.console.update_upload_next(appRT.timeUpdate + appRT.uploadDelay)  # type: ignore
        with Live(appRT.console.layout, screen=True, redirect_stderr=False):  # noqa: F841 # type: ignore
            main_loop(appRT, appData, True)

    appRT.logger.log_info('-- END Data Logging --')

    # A bit of clean-up before we exit
    appRT.sensors['SenseHat'].display_reset()
    appRT.sensors['SenseHat'].display_off()

    # Show session summary
    appRT.show_summary(cliArgs, appData)


# =========================================================
#            G L O B A L   C A T C H - A L L
# =========================================================
if __name__ == '__main__':
    main()  # pragma: no cover
