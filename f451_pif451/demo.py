#!/usr/bin/env python3
"""f451 Labs Demo application for piF451 device.

This application is designed demo core features of the f451 Labs piF451 device which is 
also equipped with a Sense HAT add-on. A secondary objective is to verify that the device
is configured properly and that the Sense HAT works.

To launch this application from terminal:

    $ python -m f451_pif451

It's also possible to install this package via 'pip' from Github and one can then launch 
this application as follows:

    $ f451_pif451

NOTE: This application is designed to display data on the Raspberry Pi Sense HAT which 
      has an 8x8 LED, and a joystick. We also support various display modes including 
      a screen-saver mode, support for 'settings.toml', and more.

NOTE: This application will NOT upload any data to the cloud.

TODO:
    - add more 8x8 images
    - add ability to pull data from Adafruit IO 'random feed'
"""

import argparse
import time
import sys
import asyncio
import platform
import random


from collections import deque, namedtuple
from datetime import datetime
from pathlib import Path

from . import constants as const
from . import demo_data as f451DemoData

import f451_common.cli_ui as f451CLIUI
import f451_common.common as f451Common
import f451_common.logger as f451Logger
import f451_common.cloud as f451Cloud

import f451_sensehat.sensehat as f451SenseHat
import f451_sensehat.sensehat_data as f451SenseData

from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn

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
APP_NAME = 'f451 Labs - piF451 Demo'
APP_NAME_SHORT = 'Demo'
APP_LOG = 'f451-piF451-demo.log'    # Individual logs for devices with multiple apps
APP_SETTINGS = 'settings.toml'      # Standard for all f451 Labs projects

APP_MIN_SENSOR_READ_WAIT = 1        # Min wait in sec between sensor reads
APP_MIN_PROG_WAIT = 1               # Remaining min (loop) wait time to display prog bar
APP_WAIT_1SEC = 1
APP_MAX_DATA = 120                  # Max number of data points in the queue
APP_DELTA_FACTOR = 0.02             # Any change within X% is considered negligable

APP_DATA_TYPES = ['number1', 'number2']

APP_DISPLAY_MODES = {
    f451SenseHat.KWD_DISPLAY_MIN: const.MIN_DISPL,
    f451SenseHat.KWD_DISPLAY_MAX: 2,
}

class AppRT(f451Common.Runtime):
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
        self.feeds = {}             # Placeholder for cloud service feeds  
        self.sensors = {}           # Placeholder for connected sensors  
        
    def init_runtime(self, cliArgs, data):
        """Initialize the 'runtime' variable
        
        We use an object to hold all core runtime values, flags, etc. 
        This makes it easier to send global values around the app as
        a single entitye rather than having to manage a series of 
        individual (global) values. 
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

        # Initialize UI for terminal
        if cliArgs.noCLI or True:
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

    def add_sensor(self, sensorName, sensorType, *args, **kwargs):
        self.sensors[sensorName] = sensorType(*args, **kwargs)

    def add_feed(self, feedName, feedType, *args, **kwargs):
        # self.feeds[feedName] = feedType(*args, **kwargs)
        pass

# Define app runtime object and basic data unit
appRT = AppRT(APP_NAME, APP_VERSION, APP_NAME_SHORT, APP_LOG, APP_SETTINGS)
DataUnit = namedtuple("DataUnit", APP_DATA_TYPES)

# Verify that feeds exist
# try:
#     FEED_DWNLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_DWNLD, None))
#     FEED_UPLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_UPLD, None))
#     FEED_PING = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_PING, None))

# except RequestError as e:
#     LOGGER.log_error(f"Application terminated due to REQUEST ERROR: {e}")
#     sys.exit(1)
# fmt: on


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def debug_config_info(cliArgs, console=None):
    """Print/log some basic debug info.

    Args:
        cliArgs: CLI arguments used to start application
        console: Optional console object
    """

    if console:
        console.rule('Config Settings', style='grey', align='center')
    else:
        appRT.logger.log_debug('-- Config Settings --')

    appRT.logger.log_debug(f"DISPL ROT:   {appRT.sensors['SenseHat'].displRotation}")
    appRT.logger.log_debug(f"DISPL MODE:  {appRT.sensors['SenseHat'].displMode}")
    appRT.logger.log_debug(f"DISPL PROGR: {appRT.sensors['SenseHat'].displProgress}")
    appRT.logger.log_debug(f"SLEEP TIME:  {appRT.sensors['SenseHat'].displSleepTime}")
    appRT.logger.log_debug(f"SLEEP MODE:  {appRT.sensors['SenseHat'].displSleepMode}")
    appRT.logger.log_debug(f'IO DEL:      {appRT.config.get(const.KWD_DELAY, const.DEF_DELAY)}')
    appRT.logger.log_debug(f'IO WAIT:     {appRT.config.get(const.KWD_WAIT, const.DEF_WAIT)}')
    appRT.logger.log_debug(f'IO THROTTLE: {appRT.config.get(const.KWD_THROTTLE, const.DEF_THROTTLE)}')

    # Display Raspberry Pi serial and Wi-Fi status
    appRT.logger.log_debug(f'Raspberry Pi serial: {f451Common.get_RPI_serial_num()}')
    appRT.logger.log_debug(
        f'Wi-Fi: {(f451Common.STATUS_YES if f451Common.check_wifi() else f451Common.STATUS_UNKNOWN)}'
    )

    # Display CLI args
    appRT.logger.log_debug(f'CLI Args:\n{cliArgs}')


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
    # Data slice we want to send to Sense HAT. The 'f451 Labs SenseHat' library
    # will ulimately only display the last 8 values anyway.
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
        colorMap = f451Common.get_tri_colors(customColors)

        # fmt: off
        return [
            f'{colorMap.high}:gt:{round(limits[2], 1)}',    # High   # type: ignore
            f'{colorMap.normal}:eq:{round(limits[2], 1)}',  # Normal # type: ignore
            f'{colorMap.normal}:lt:{round(limits[2], 1)}',  # Normal # type: ignore
            f'{colorMap.low}:eq:{round(limits[1], 1)}',     # Low    # type: ignore
            f'{colorMap.low}:lt:{round(limits[1], 1)}',     # Low    # type: ignore
        ]
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

        if val is not None:
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
                'sparkColors': [],
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


async def send_data(*args):
    """Fake 'send' function"""
    print('Fake upload start ...')
    time.sleep(5)
    print('... fake upload end')


async def upload_demo_data(*args, **kwargs):
    """Fake upload function

    This helper function simulates parsing and uploading data
    to some cloud service.

    Args:
        args:
            User can provide single 'dict' with data
        kwargs:
            User can provide individual data points as key-value pairs
    """
    # We combine 'args' and 'kwargs' to allow users to provide a 'dict' with
    # all data points and/or individual data points (which could override
    # values in the 'dict').
    upload = {**args[0], **kwargs} if args and isinstance(args[0], dict) else kwargs

    sendQ = []

    # Send download speed data ?
    if upload.get('data') is not None:
        sendQ.append(send_data(upload.get('data')))  # type: ignore

    # deviceID = appRT.sensors['SenseHat'].get_ID(DEF_ID_PREFIX)

    await asyncio.gather(*sendQ)


def get_random_demo_data(limits=None):
    """Generate random data

    Returns:
        'namedtuple' 'DataUnit with random demo data
    """
    return DataUnit(number1=random.randint(1, 200), number2=random.randint(0, 100))


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


def update_SenseHat_LED(sense, data):
    """Update Sense HAT LED display depending on display mode

    We check current display mode and then prep data as needed
    for display on LED.

    Args:
        data: full data set. We'll grab a slice from the end
    """

    def _minMax(data):
        """Create min/max based on all collecxted data

        This will smooth out some hard edges that may occur
        when the data slice is to short.
        """
        scrubbed = [i for i in data if i is not None]
        return (min(scrubbed), max(scrubbed)) if scrubbed else (0, 0)

    # Check display mode. Each mode corresponds to a data type
    if sense.displMode == 1:
        # dataClean = prep_data_for_sensehat(data.number1.as_tuple(), sense.widthLED)
        dataClean = prep_data_for_sensehat(data.number1.as_tuple())
        minMax = _minMax(data.number1.as_tuple().data)
        sense.display_as_graph(dataClean, minMax)

    elif sense.displMode == 2:
        # dataClean = prep_data_for_sensehat(data.number2.as_tuple(), sense.widthLED)
        dataClean = prep_data_for_sensehat(data.number2.as_tuple())
        minMax = _minMax(data.number2.as_tuple().data)
        sense.display_as_graph(dataClean, minMax)

    else:  # Display sparkles
        sense.display_sparkle()


def init_cli_parser():
    """Initialize CLI (ArgParse) parser.

    Initialize the ArgParse parser with the CLI 'arguments' and
    return a new parser instance.

    Returns:
        ArgParse parser instance
    """
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f'{APP_NAME} [v{APP_VERSION}] - collect internet speed test data using Speedtest CLI, and upload to Adafruit IO and/or Arduino Cloud.',
        epilog='NOTE: This application requires active accounts with corresponding cloud services.',
    )

    parser.add_argument(
        '-V',
        '--version',
        action='store_true',
        help='display script version number and exit',
    )
    parser.add_argument('-d', '--debug', action='store_true', help='run script in debug mode')
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
        '--log',
        action='store',
        type=str,
        help='name of log file',
    )
    parser.add_argument(
        '--uploads',
        action='store',
        type=int,
        default=-1,
        help='number of uploads before exiting',
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        default=False,
        help='show output to CLI stdout',
    )

    return parser


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
    cli = init_cli_parser()
    cliArgs, _ = cli.parse_known_args(cliArgs)
    if not cliArgs and len(sys.argv) == 1:
        cli.print_help(sys.stdout)
        sys.exit(0)

    if cliArgs.version:
        print(f'{APP_NAME} (v{APP_VERSION})')
        sys.exit(0)

    # Get core settings and initialize core data queue
    appData = f451DemoData.DemoData(None, APP_MAX_DATA)
    appRT.init_runtime(cliArgs, appData)

    # Initialize device instance which includes all sensors
    # and LED display on Sense HAT
    appRT.add_sensor('SenseHat', f451SenseHat.SenseHat, appRT.config)

    # Initialize Sense HAT joystick and LED display
    appRT.sensors['SenseHat'].joystick_init(**APP_JOYSTICK_ACTIONS)
    appRT.sensors['SenseHat'].display_init(**APP_DISPLAY_MODES)
    appRT.sensors['SenseHat'].update_sleep_mode(cliArgs.noLED)
    appRT.sensors['SenseHat'].displProgress = cliArgs.progress

    # -- Main application loop --
    appRT.sensors['SenseHat'].display_message(APP_NAME)
    # appRT.console.update_upload_next(appRT.timeUpdate + appRT.uploadDelay)  # type: ignore
    appRT.logger.log_info('-- START Data Logging --')

    print('BEEP')

    exitNow = False
    while not exitNow:
        try:
            # fmt: off
            timeCurrent = time.time()
            appRT.timeSinceUpdate = timeCurrent - appRT.timeUpdate
            appRT.sensors['SenseHat'].update_sleep_mode(
                (timeCurrent - appRT.displayUpdate) > appRT.sensors['SenseHat'].displSleepTime, # Time to sleep?
                # cliArgs.noLED,                                                # Force no LED?
                appRT.sensors['SenseHat'].displSleepMode                                        # Already asleep?
            )

            # Update Sense HAT prog bar as needed
            appRT.sensors['SenseHat'].display_progress(appRT.timeSinceUpdate / appRT.uploadDelay)

            # --- Get magic data ---
            #
            # screen.update_action('Reading sensors …')
            newData = get_random_demo_data()
            #
            # ----------------------
            # fmt: on

            # Is it time to upload data?
            if appRT.timeSinceUpdate >= appRT.uploadDelay:
                try:
                    asyncio.run(
                        upload_demo_data(
                            data=newData.number1,
                            deviceID=f451Common.get_RPI_ID(f451Common.DEF_ID_PREFIX),
                        )
                    )

                except RequestError as e:
                    appRT.logger.log_error(f'Application terminated: {e}')
                    sys.exit(1)

                except ThrottlingError:
                    # Keep increasing 'ioDelay' each time we get a 'ThrottlingError'
                    appRT.uploadDelay += appRT.ioThrottle

                else:
                    # Reset 'uploadDelay' back to normal 'ioFreq' on successful upload
                    appRT.numUploads += 1
                    appRT.uploadDelay = appRT.ioFreq
                    exitNow = exitNow or appRT.ioUploadAndExit
                    appRT.logger.log_info(
                        f'Uploaded: Magic #: {round(newData.number1, appRT.ioRounding)}'
                    )

                finally:
                    appRT.timeUpdate = timeCurrent
                    exitNow = (appRT.maxUploads > 0) and (appRT.numUploads >= appRT.maxUploads)

            # Update data set and display to terminal as needed
            appData.number1.data.append(newData.number1)
            appData.number2.data.append(newData.number2)

            update_SenseHat_LED(appRT.sensors['SenseHat'], appData)

            # Are we done? And do we have to wait a bit before next sensor read?
            if not exitNow:
                # If we'tre not done and there's a substantial wait before we can
                # read the sensors again (e.g. we only want to read sensors every
                # few minutes for whatever reason), then lets display and update
                # the progress bar as needed. Once the wait is done, we can go
                # through this whole loop all over again ... phew!
                # if app.ioWait > APP_MIN_PROG_WAIT:
                #     screen.update_progress(None, 'Waiting for sensors …')
                #     for i in range(app.ioWait):
                #         screen.update_progress(int(i / app.ioWait * 100))
                #         time.sleep(APP_WAIT_1SEC)
                #     screen.update_action()
                # else:
                #     screen.update_action()
                #     time.sleep(app.ioWait)
                time.sleep(appRT.ioWait)

                # Update Sense HAT prog bar as needed
                # appRT.sensors['SenseHat'].display_progress(app.timeSinceUpdate / app.uploadDelay)

        except KeyboardInterrupt:
            exitNow = True

    # If log level <= INFO
    appRT.logger.log_info('-- END Data Logging --')

    # A bit of clean-up before we exit ...
    appRT.sensors['SenseHat'].display_reset()
    appRT.sensors['SenseHat'].display_off()

    # ... and display summary info
    print()
    appRT.console.rule(f'{APP_NAME} (v{APP_VERSION})', style='grey', align='center')  # type: ignore
    print(f'Work start:  {appRT.workStart:%a %b %-d, %Y at %-I:%M:%S %p}')
    print(f'Work end:    {(datetime.now()):%a %b %-d, %Y at %-I:%M:%S %p}')
    print(f'Num uploads: {appRT.numUploads}')
    appRT.console.rule(style='grey', align='center')  # type: ignore
    pprint(locals(), expand_all=True)
    pprint(appRT.config, expand_all=True)

    if appRT.debugMode:
        debug_config_info(cliArgs, appRT.console)


# =========================================================
#            G L O B A L   C A T C H - A L L
# =========================================================
if __name__ == '__main__':
    main()  # pragma: no cover
