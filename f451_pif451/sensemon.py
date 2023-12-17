#!/usr/bin/env python3
"""f451 Labs SenseMon application on piRED & piF451 devices.

This application is designed for the f451 Labs piRED and piF451 devices which are both 
equipped with Sense HAT add-ons. The main objective is to continously read environment 
data (e.g. temperature, barometric pressure, and humidity from the Sense HAT sensors and 
then upload the data to the Adafruit IO service.

To launch this application from terminal:

    $ nohup python -u sensemon.py > sensemon.out &

This command launches the 'sensemon' application in the background. The application will 
keep running even after the terminal window is closed. Any output will be redirected to 
the 'sensemon.out' file.    

It's also possible to install this application via 'pip' from Github and one 
can launch the application as follows:

    $ nohup sensemon > sensemon.out &

NOTE: This code is based on the 'luftdaten_combined.py' example from the Enviro+ Python
      example files. Main modifications include support for Adafruit.io, using Python 
      'deque' to manage data queues, moving device support to a separate class, etc.

      Furthermore, this application is designed to get sensor data from the Raspberry 
      Pi Sense HAT which has fewer sensors than the Enviro+, an 8x8 LED, and a joystick.
      
      We also support additional display modes including a screen-saver mode, support 
      for 'settings.toml', and more.

Dependencies:
    - adafruit-io - only install if you have an account with Adafruit IO

TODO:
    - add support for custom colors in 'settings.toml'
    - add support for custom range factor in 'settings.toml'
"""

import argparse
import time
import sys
import asyncio
import platform

from collections import deque
from datetime import datetime
from pathlib import Path

from . import constants as const

import f451_cli_ui.cli_ui as f451CLIUI
import f451_common.common as f451Common
import f451_logger.logger as f451Logger
import f451_cloud.cloud as f451Cloud

import f451_sensehat.sensehat as f451SenseHat
import f451_sensehat.sensehat_data as f451SenseData

from rich.live import Live
from rich.traceback import install as install_rich_traceback

from Adafruit_IO import RequestError, ThrottlingError


# Install Rich 'traceback' to make (debug) life is
# easier. Trust me!
install_rich_traceback(show_locals=True)


# fmt: off
# =========================================================
#          G L O B A L    V A R S   &   I N I T S
# =========================================================
APP_VERSION = '0.5.1'
APP_NAME = 'f451 Labs - SenseMon'
APP_NAME_SHORT = 'SenseMon'
APP_HOST = platform.node()          # Get device 'hostname'
APP_LOG = 'f451-sensemon.log'       # Individual logs for devices with multiple apps
APP_SETTINGS = 'settings.toml'      # Standard for all f451 Labs projects
APP_DIR = Path(__file__).parent     # Find dir for this app

APP_MIN_SENSOR_READ_WAIT = 1        # Min wait in sec between sensor reads
APP_MIN_PROG_WAIT = 1               # Remaining min (loop) wait time to display prog bar
APP_WAIT_1SEC = 1
APP_MAX_DATA = 120                  # Max number of data points in the queue
APP_DELTA_FACTOR = 0.02             # Any change within X% is considered negligable

APP_DATA_TYPES = ['temperature', 'pressure', 'humidity']

APP_DISPLAY_MODES = {
    f451SenseHat.KWD_DISPLAY_MIN: const.MIN_DISPL,
    f451SenseHat.KWD_DISPLAY_MAX: const.MAX_DISPL,
}

# Load settings
CONFIG = f451Common.load_settings(APP_DIR.joinpath(APP_SETTINGS))

# Initialize device instance which includes all sensors
# and LED display on Sense HAT
SENSE_HAT = f451SenseHat.SenseHat(CONFIG)

# Initialize logger and IO cloud
LOGGER = f451Logger.Logger(CONFIG, LOGFILE=APP_LOG)
UPLOADER = f451Cloud.Cloud(CONFIG)

# Verify that feeds exist
try:
    FEED_TEMPS = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_TEMPS, None))
    FEED_PRESS = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_PRESS, None))
    FEED_HUMID = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_HUMID, None))

except RequestError as e:
    LOGGER.log_error(f'Application terminated due to REQUEST ERROR: {e}')
    sys.exit(1)

# We use these timers to track when to upload data and/or set
# display to sleep mode. Normally we'd want them to be local vars
# inside 'main()'. However, since we need them reset them in the
# button actions, they need to be global.
timeUpdate = time.time()
displayUpdate = timeUpdate
# fmt: on


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def debug_config_info(cliArgs, console=None):
    """Print/log some basic debug info."""

    if console:
        console.rule('Config Settings', style='grey', align='center')
    else:
        LOGGER.log_debug('-- Config Settings --')

    LOGGER.log_debug(f'DISPL ROT:   {SENSE_HAT.displRotation}')
    LOGGER.log_debug(f'DISPL MODE:  {SENSE_HAT.displMode}')
    LOGGER.log_debug(f'DISPL PROGR: {SENSE_HAT.displProgress}')
    LOGGER.log_debug(f'SLEEP TIME:  {SENSE_HAT.displSleepTime}')
    LOGGER.log_debug(f'SLEEP MODE:  {SENSE_HAT.displSleepMode}')
    LOGGER.log_debug(f'IO DEL:      {CONFIG.get(const.KWD_DELAY, const.DEF_DELAY)}')
    LOGGER.log_debug(f'IO WAIT:     {CONFIG.get(const.KWD_WAIT, const.DEF_WAIT)}')
    LOGGER.log_debug(f'IO THROTTLE: {CONFIG.get(const.KWD_THROTTLE, const.DEF_THROTTLE)}')

    LOGGER.log_debug(
        f'TEMP COMP:   {CONFIG.get(f451Common.KWD_TEMP_COMP, f451Common.DEF_TEMP_COMP_FACTOR)}'
    )

    # Display Raspberry Pi serial and Wi-Fi status
    LOGGER.log_debug(f'Raspberry Pi serial: {f451Common.get_RPI_serial_num()}')
    LOGGER.log_debug(
        f'Wi-Fi: {(f451Common.STATUS_YES if f451Common.check_wifi() else f451Common.STATUS_UNKNOWN)}'
    )

    # Display CLI args
    LOGGER.log_debug(f'CLI Args:\n{cliArgs}')


def prep_data_for_sensehat(inData, ledWidth):
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
        inData: data set with 'raw' data from sensors
        ledWidth: width of LED display
    
    Returns:
        'dict' with compatible structure:
            {
                'data': [list of values],
                'valid': <tuple with min/max>,
                'unit': <unit string>,
                'label': <label string>,
                'limits': [list of limits]
            }
    """
    # Data slice we can display on Sense HAT LED
    dataSlice = list(inData['data'])[-ledWidth:]

    # Return filtered data
    dataClean = [i if f451Common.is_valid(i, inData['valid']) else 0 for i in dataSlice]

    return {
                'data': dataClean,
                'valid': inData['valid'],
                'unit': inData['unit'],
                'label': inData['label'],
                'limit': inData['limits']
            }


def prep_data_for_screen(inData, labelsOnly=False, conWidth=f451CLIUI.APP_2COL_MIN_WIDTH):
    """Prep data for display in terminal

    We only display temperature, humidity, and pressure, and we
    need to normalize all data to fit within the 1-8 range. We
    can use 0 for missing values and for values that fall outside the
    valid range for the Sense HAT, which we'll assume are erroneous.

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
            dataPtPrev = dataSlice[-2] if f451Common.is_valid(dataSlice[-2], row['valid']) else None
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


def init_cli_parser():
    """Initialize CLI (ArgParse) parser.

    Initialize the ArgParse parser with the CLI 'arguments' and
    return a new parser instance.

    Returns:
        ArgParse parser instance
    """
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f'{APP_NAME} [v{APP_VERSION}] - read sensor data from Sense HAT and upload to Adafruit IO and/or Arduino Cloud.',
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
        '--cron',
        action='store_true',
        help='use when running as cron job - run script once and exit',
    )
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


async def upload_sensor_data(*args, **kwargs):
    """Send sensor data to cloud services.

    This helper function parses and sends enviro data to
    Adafruit IO and/or Arduino Cloud.

    NOTE: This function will upload specific environment
          data using the following keywords:

          'temperature' - temperature data
          'pressure'    - barometric pressure
          'humidity'    - humidity

    Args:
        args:
            User can provide single 'dict' with data
        kwargs:
            User can provide individual data points as key-value pairs
    """
    # We combine 'args' and 'kwargs' to allow users to provide a 'dict' with
    # all data points and/or individual data points (which could override
    # values in the 'dict').
    data = {**args[0], **kwargs} if args and isinstance(args[0], dict) else kwargs

    sendQ = []

    # Send temperature data ?
    if data.get(const.KWD_DATA_TEMPS, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_TEMPS.key, data.get(const.KWD_DATA_TEMPS)))  # type: ignore

    # Send barometric pressure data ?
    if data.get(const.KWD_DATA_PRESS, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_PRESS.key, data.get(const.KWD_DATA_PRESS)))  # type: ignore

    # Send humidity data ?
    if data.get(const.KWD_DATA_HUMID, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_HUMID.key, data.get(const.KWD_DATA_HUMID)))  # type: ignore

    # deviceID = SENSE_HAT.get_ID(DEF_ID_PREFIX)

    await asyncio.gather(*sendQ)


def btn_up(event):
    """SenseHat Joystick UP event

    Rotate display by -90 degrees and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        # SENSE_HAT.display_rotate(-1)
        SENSE_HAT.debug_joystick("up")
        displayUpdate = time.time()


def btn_down(event):
    """SenseHat Joystick DOWN event

    Rotate display by +90 degrees and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        # SENSE_HAT.display_rotate(1)
        SENSE_HAT.debug_joystick("down")
        displayUpdate = time.time()


def btn_left(event):
    """SenseHat Joystick LEFT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        # SENSE_HAT.update_display_mode(-1)
        SENSE_HAT.debug_joystick("left")
        displayUpdate = time.time()


def btn_right(event):
    """SenseHat Joystick RIGHT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        # SENSE_HAT.update_display_mode(1)
        SENSE_HAT.debug_joystick("right")
        displayUpdate = time.time()


def btn_middle(event):
    """SenseHat Joystick MIDDLE (down) event

    Turn display on/off
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.debug_joystick("press")
        # Wake up?
        # if SENSE_HAT.displSleepMode:
        #     SENSE_HAT.update_sleep_mode(False)
        #     displayUpdate = time.time()
        # else:
        #     SENSE_HAT.update_sleep_mode(True)


APP_JOYSTICK_ACTIONS = {
    f451SenseHat.KWD_BTN_UP: btn_up,
    f451SenseHat.KWD_BTN_DWN: btn_down,
    f451SenseHat.KWD_BTN_LFT: btn_left,
    f451SenseHat.KWD_BTN_RHT: btn_right,
    f451SenseHat.KWD_BTN_MDL: btn_middle,
}


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
    global LOGGER
    global SENSE_HAT
    global timeUpdate
    global displayUpdate

    # Parse CLI args and show 'help' and exit if no args
    cli = init_cli_parser()
    cliArgs, _ = cli.parse_known_args(cliArgs)
    if not cliArgs and len(sys.argv) == 1:
        cli.print_help(sys.stdout)
        sys.exit(0)

    if cliArgs.version:
        print(f'{APP_NAME} (v{APP_VERSION})')
        sys.exit(0)

    # Initialize core data queues and related variables
    senseData = f451SenseData.SenseData(None, APP_MAX_DATA)
    tempCompFactor = CONFIG.get(f451Common.KWD_TEMP_COMP, f451Common.DEF_TEMP_COMP_FACTOR)
    cpuTempsQMaxLen = CONFIG.get(f451Common.KWD_MAX_LEN_CPU_TEMPS, f451Common.MAX_LEN_CPU_TEMPS)

    # If comp factor is 0 (zero), then
    # do NOT compensate for CPU temp
    tempCompYN = tempCompFactor > 0

    cpuTempsQ = []
    if tempCompYN:
        cpuTempsQ = deque(
            [SENSE_HAT.get_CPU_temp(False)] * cpuTempsQMaxLen, maxlen=cpuTempsQMaxLen
        )

    # Initialize UI for terminal
    screen = f451CLIUI.BaseUI()
    screen.initialize(
        APP_NAME,
        APP_NAME_SHORT,
        APP_VERSION,
        prep_data_for_screen(senseData.as_dict(), True),
        not cliArgs.noCLI,
    )

    # Initialize Sense HAT joystick and LED display
    SENSE_HAT.joystick_init(**APP_JOYSTICK_ACTIONS)
    SENSE_HAT.display_init(**APP_DISPLAY_MODES)
    SENSE_HAT.update_sleep_mode(cliArgs.noLED)

    if cliArgs.progress:
        SENSE_HAT.displProgress = True

    # Get core settings
    ioFreq = CONFIG.get(const.KWD_FREQ, const.DEF_FREQ)
    ioDelay = CONFIG.get(const.KWD_DELAY, const.DEF_DELAY)
    ioWait = max(CONFIG.get(const.KWD_WAIT, const.DEF_WAIT), APP_MIN_SENSOR_READ_WAIT)
    ioThrottle = CONFIG.get(const.KWD_THROTTLE, const.DEF_THROTTLE)
    ioRounding = CONFIG.get(const.KWD_ROUNDING, const.DEF_ROUNDING)
    ioUploadAndExit = cliArgs.cron

    logLvl = CONFIG.get(f451Logger.KWD_LOG_LEVEL, f451Logger.LOG_NOTSET)
    debugMode = logLvl == f451Logger.LOG_DEBUG

    # Update log file or level?
    if cliArgs.debug:
        LOGGER.set_log_level(f451Logger.LOG_DEBUG)
        logLvl = f451Logger.LOG_DEBUG
        debugMode = True

    if cliArgs.log is not None:
        LOGGER.set_log_file(logLvl, cliArgs.log)

    if debugMode:
        debug_config_info(cliArgs, screen.console)

    # -- Main application loop --
    workStart = datetime.now()
    timeSinceUpdate = 0
    timeUpdate = time.time()
    displayUpdate = timeUpdate
    uploadDelay = ioDelay  # Ensure that we do NOT upload first reading
    maxUploads = int(cliArgs.uploads)
    numUploads = 0
    exitNow = False

    # Let user know when first upload will happen
    screen.update_upload_next(timeUpdate + uploadDelay)

    # If log level <= INFO
    LOGGER.log_info('-- START Data Logging --')

    with Live(screen.layout, screen=True, redirect_stderr=False) as live:  # noqa: F841
        try:
            while not exitNow:
                timeCurrent = time.time()
                timeSinceUpdate = timeCurrent - timeUpdate
                SENSE_HAT.update_sleep_mode(
                    (timeCurrent - displayUpdate) > SENSE_HAT.displSleepTime, cliArgs.noLED
                )

                # --- Get sensor data ---
                #
                screen.update_action('Reading sensors …')

                # Get raw temp from sensor
                tempRaw = tempComp = SENSE_HAT.get_temperature()

                # Do we need to compensate for CPUY temp?
                if tempCompYN:
                    # Get current CPU temp, add to queue, and calculate new average
                    #
                    # NOTE: This feature relies on the 'vcgencmd' which is found on
                    #       RPIs. If this is not run on a RPI (e.g. during testing),
                    #       then we need to neutralize the 'cpuTemp' compensation.
                    cpuTempsQ.append(SENSE_HAT.get_CPU_temp(False))
                    cpuTempAvg = sum(cpuTempsQ) / float(cpuTempsQMaxLen)

                    # Smooth out with some averaging to decrease jitter
                    tempComp = tempRaw - ((cpuTempAvg - tempRaw) / tempCompFactor)

                # Get barometric pressure and humidity data
                pressRaw = SENSE_HAT.get_pressure()
                humidRaw = SENSE_HAT.get_humidity()
                #
                # -----------------------

                # Is it time to upload data?
                if timeSinceUpdate >= uploadDelay:
                    screen.update_action('Uploading …')
                    try:
                        asyncio.run(
                            upload_sensor_data(
                                temperature=round(tempComp, ioRounding),
                                pressure=round(pressRaw, ioRounding),
                                humidity=round(humidRaw, ioRounding),
                                deviceID=f451Common.get_RPI_ID(f451Common.DEF_ID_PREFIX),
                            )
                        )

                    except RequestError as e:
                        LOGGER.log_error(f'Application terminated: {e}')
                        sys.exit(1)

                    except ThrottlingError:
                        # Keep increasing 'ioDelay' each time we get
                        # a 'ThrottlingError'
                        uploadDelay += ioThrottle

                    else:
                        # Reset 'uploadDelay' back to normal 'ioFreq'
                        # on successful upload
                        numUploads += 1
                        uploadDelay = ioFreq
                        exitNow = exitNow or ioUploadAndExit
                        screen.update_upload_status(
                            timeCurrent,
                            f451CLIUI.STATUS_OK,
                            timeCurrent + uploadDelay,
                            numUploads,
                            maxUploads,
                        )
                        LOGGER.log_info(
                            f'Uploaded: TEMP: {round(tempComp, ioRounding)} - PRESS: {round(pressRaw, ioRounding)} - HUMID: {round(humidRaw, ioRounding)}'
                        )

                    finally:
                        timeUpdate = timeCurrent
                        exitNow = (maxUploads > 0) and (numUploads >= maxUploads)
                        screen.update_action(f451CLIUI.STATUS_LBL_WAIT)

                # Update data set and display to terminal as needed
                senseData.temperature.data.append(tempComp)
                senseData.pressure.data.append(pressRaw)
                senseData.humidity.data.append(humidRaw)
                screen.update_data(prep_data_for_screen(senseData.as_dict()))

                # Check display mode. Each mode corresponds to a data type
                if SENSE_HAT.displMode == const.DISPL_TEMP:  # type = "temperature"
                    SENSE_HAT.display_as_graph(prep_data_for_sensehat(
                        senseData.temperature.as_dict(), 
                        SENSE_HAT.widthLED
                    ))

                elif SENSE_HAT.displMode == const.DISPL_PRESS:  # type = "pressure"
                    SENSE_HAT.display_as_graph(prep_data_for_sensehat(
                        senseData.pressure.as_dict(), 
                        SENSE_HAT.widthLED
                    ))

                elif SENSE_HAT.displMode == const.DISPL_HUMID:  # type = "humidity"
                    SENSE_HAT.display_as_graph(prep_data_for_sensehat(
                        senseData.humidity.as_dict(), 
                        SENSE_HAT.widthLED
                    ))

                else:  # Display sparkles
                    SENSE_HAT.display_sparkle()

                # Are we done? And do we have to wait a bit before next sensor read?
                if not exitNow:
                    # If we'tre not done and there's a substantial wait before we can
                    # read the sensors again (e.g. we only want to read sensors every
                    # few minutes for whatever reason), then lets display and update
                    # the progress bar as needed. Once the wait is done, we can go
                    # through this whole loop all over again ... phew!
                    if ioWait > APP_MIN_PROG_WAIT:
                        screen.update_progress(None, 'Waiting for sensors …')
                        for i in range(ioWait):
                            screen.update_progress(int(i / ioWait * 100))
                            time.sleep(APP_WAIT_1SEC)
                        screen.update_action()
                    else:
                        screen.update_action()
                        time.sleep(ioWait)

                    # Update Sense HAT prog bar as needed
                    SENSE_HAT.display_progress(timeSinceUpdate / uploadDelay)

        except KeyboardInterrupt:
            exitNow = True

    # If log level <= INFO
    LOGGER.log_info('-- END Data Logging --')

    # A bit of clean-up before we exit ...
    SENSE_HAT.display_reset()
    SENSE_HAT.display_off()

    # ... and display summary info
    print()
    screen.console.rule(f'{APP_NAME_SHORT} - Summary', style='grey', align='center')
    print(f'App name:    {APP_NAME}')
    print(f'App version: {APP_VERSION}\n')
    print(f'Work start:  {workStart:%a %b %-d, %Y at %-I:%M:%S %p}')
    print(f'Work end:    {(datetime.now()):%a %b %-d, %Y at %-I:%M:%S %p}\n')

    ofMaxStr = f' of {maxUploads}' if maxUploads > 0 else ''
    print(f'Num uploads: {numUploads}{ofMaxStr}')

    screen.console.rule(style='grey', align='center')
    print()


# =========================================================
#            G L O B A L   C A T C H - A L L
# =========================================================
if __name__ == '__main__':
    main()  # pragma: no cover
